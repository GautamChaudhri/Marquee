"""The library routes against the test database. The list, availability, and retirement
cases came from the old library route tests; the poster status, review, and resolution
fields went with the columns they read."""

from __future__ import annotations

import hashlib
import io
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

import pytest
from httpx import AsyncClient
from PIL import Image
from sqlalchemy.ext.asyncio import AsyncSession

from marquee.config import settings
from marquee.db.models import Artwork, Movie, PosterRun

pytestmark = pytest.mark.db

RADARR_ROOT = "/radarr/movies"
NOW = datetime(2026, 11, 24, 12, 0, tzinfo=UTC)


@pytest.fixture
def headers(api_key: str) -> dict[str, str]:
    return {"X-Api-Key": api_key}


def _movie(radarr_id: int, title: str, **values: object) -> Movie:
    defaults: dict[str, object] = {
        "radarr_id": radarr_id,
        "title": title,
        "sort_title": title.lower().removeprefix("the "),
        "year": 2000 + radarr_id,
        "radarr_path": f"{RADARR_ROOT}/4K/{title}",
        "has_file": True,
        "radarr_added_at": datetime(2024, 1, radarr_id, tzinfo=UTC),
        "genres": ["Drama"],
        "last_seen_at": NOW,
    }
    return Movie(**{**defaults, **values})


def _poster(movie: Movie, data: bytes, mtime: datetime = NOW, origin: str = "library") -> Artwork:
    return Artwork(
        movie_id=movie.id,
        artwork_kind="poster",
        file_name="poster.jpg",
        sha256=hashlib.sha256(data).hexdigest(),
        size_bytes=len(data),
        file_modified_at=mtime,
        origin=origin,
        first_seen_at=NOW,
        last_seen_at=NOW,
    )


async def _seed(session: AsyncSession) -> dict[str, Movie]:
    movies = {
        "alpha": _movie(1, "Alpha"),
        "bravo": _movie(2, "Bravo", has_file=False),
        "charlie": _movie(3, "The Charlie Movie", year=None),
        "delta": _movie(4, "Delta 100%_done"),
        "retired": _movie(5, "Retired", is_present=False, retired_at=NOW),
    }
    session.add_all(movies.values())
    await session.flush()
    session.add(_poster(movies["alpha"], b"alpha poster"))
    session.add(_poster(movies["retired"], b"retired poster"))
    await session.commit()
    return movies


async def test_the_list_shows_movies_with_a_file_by_sort_title(
    client: AsyncClient, db_session: AsyncSession, headers: dict
) -> None:
    await _seed(db_session)
    body = (await client.get("/api/movies", headers=headers)).json()
    assert body["total"] == 3
    assert (body["page"], body["page_size"]) == (1, 50)
    assert [m["title"] for m in body["items"]] == ["Alpha", "The Charlie Movie", "Delta 100%_done"]


async def test_include_unavailable_adds_wanted_movies(
    client: AsyncClient, db_session: AsyncSession, headers: dict
) -> None:
    await _seed(db_session)
    response = await client.get(
        "/api/movies", params={"include_unavailable": "true", "q": "bra"}, headers=headers
    )
    (bravo,) = response.json()["items"]
    assert (bravo["title"], bravo["has_file"], bravo["poster"]) == ("Bravo", False, None)


async def test_retired_movies_are_never_listed_or_found(
    client: AsyncClient, db_session: AsyncSession, headers: dict
) -> None:
    movies = await _seed(db_session)
    listing = await client.get(
        "/api/movies", params={"include_unavailable": "true", "q": "Retired"}, headers=headers
    )
    assert listing.json()["items"] == []
    retired_id = movies["retired"].id
    assert (await client.get(f"/api/movies/{retired_id}", headers=headers)).status_code == 404
    poster = await client.get(f"/api/movies/{retired_id}/poster", headers=headers)
    assert poster.status_code == 404


@pytest.mark.parametrize(
    ("q", "titles"),
    [
        ("alp", ["Alpha"]),
        ("ALPHA", ["Alpha"]),
        ("movie", ["The Charlie Movie"]),
        ("100%", ["Delta 100%_done"]),
        ("%", ["Delta 100%_done"]),
        ("_", ["Delta 100%_done"]),
        ("a_p", []),
        ("\\", []),
    ],
)
async def test_title_search_is_case_insensitive_and_literal(
    client: AsyncClient, db_session: AsyncSession, headers: dict, q: str, titles: list[str]
) -> None:
    await _seed(db_session)
    body = (await client.get("/api/movies", params={"q": q}, headers=headers)).json()
    assert [m["title"] for m in body["items"]] == titles


async def test_has_poster_filters(
    client: AsyncClient, db_session: AsyncSession, headers: dict
) -> None:
    await _seed(db_session)
    with_poster = await client.get("/api/movies", params={"has_poster": "true"}, headers=headers)
    without = await client.get("/api/movies", params={"has_poster": "false"}, headers=headers)
    assert [m["title"] for m in with_poster.json()["items"]] == ["Alpha"]
    assert [m["title"] for m in without.json()["items"]] == ["The Charlie Movie", "Delta 100%_done"]


async def test_origin_and_run_filters(
    client: AsyncClient, db_session: AsyncSession, headers: dict
) -> None:
    movies = await _seed(db_session)
    delta = movies["delta"]
    db_session.add(_poster(delta, b"delta poster", origin="marquee"))
    db_session.add(
        PosterRun(
            movie_id=movies["charlie"].id, state="failed", run_folder="00000001-c", started_at=NOW
        )
    )
    await db_session.commit()

    async def titles(**params: str) -> list[str]:
        body = (await client.get("/api/movies", params=params, headers=headers)).json()
        assert body["total"] == len(body["items"])
        return [m["title"] for m in body["items"]]

    assert await titles(origin="library") == ["Alpha"]
    assert await titles(origin="marquee") == ["Delta 100%_done"]
    assert await titles(has_run="true") == ["The Charlie Movie"]
    assert await titles(has_run="false") == ["Alpha", "Delta 100%_done"]
    assert await titles(has_run="false", origin="marquee") == ["Delta 100%_done"]
    bad = await client.get("/api/movies", params={"origin": "tmdb"}, headers=headers)
    assert bad.status_code == 422


@pytest.mark.parametrize(
    ("sort", "titles"),
    [
        ("title", ["Alpha", "The Charlie Movie", "Delta 100%_done"]),
        ("year", ["Delta 100%_done", "Alpha", "The Charlie Movie"]),
        ("added", ["Delta 100%_done", "The Charlie Movie", "Alpha"]),
    ],
)
async def test_sorting(
    client: AsyncClient, db_session: AsyncSession, headers: dict, sort: str, titles: list[str]
) -> None:
    await _seed(db_session)
    body = (await client.get("/api/movies", params={"sort": sort}, headers=headers)).json()
    assert [m["title"] for m in body["items"]] == titles


async def test_paging(client: AsyncClient, db_session: AsyncSession, headers: dict) -> None:
    await _seed(db_session)
    first = (await client.get("/api/movies", params={"page_size": 2}, headers=headers)).json()
    second = (
        await client.get("/api/movies", params={"page_size": 2, "page": 2}, headers=headers)
    ).json()
    past = (await client.get("/api/movies", params={"page": 9}, headers=headers)).json()
    assert [m["title"] for m in first["items"]] == ["Alpha", "The Charlie Movie"]
    assert [m["title"] for m in second["items"]] == ["Delta 100%_done"]
    assert (first["total"], second["total"], past["total"], past["items"]) == (3, 3, 3, [])


@pytest.mark.parametrize(
    "params",
    [{"page": 0}, {"page_size": 0}, {"page_size": 201}, {"sort": "rating"}, {"q": "x" * 201}],
)
async def test_bad_list_parameters_are_refused(
    client: AsyncClient, headers: dict, params: dict
) -> None:
    assert (await client.get("/api/movies", params=params, headers=headers)).status_code == 422


async def test_one_movie_and_its_poster_object(
    client: AsyncClient, db_session: AsyncSession, headers: dict
) -> None:
    movies = await _seed(db_session)
    alpha = movies["alpha"]
    body = (await client.get(f"/api/movies/{alpha.id}", headers=headers)).json()
    assert body["title"] == "Alpha"
    assert body["genres"] == ["Drama"]
    assert body["poster"] == {
        "file_name": "poster.jpg",
        "sha256": hashlib.sha256(b"alpha poster").hexdigest(),
        "size_bytes": len(b"alpha poster"),
        "origin": "library",
        "url": f"/api/movies/{alpha.id}/poster",
        "version": hashlib.sha256(b"alpha poster").hexdigest()[:12],
    }
    assert (body["poster_origin"], body["latest_run_id"]) == ("library", None)


async def test_no_response_carries_a_path(
    client: AsyncClient, db_session: AsyncSession, headers: dict
) -> None:
    movies = await _seed(db_session)
    listing = await client.get(
        "/api/movies", params={"include_unavailable": "true"}, headers=headers
    )
    detail = await client.get(f"/api/movies/{movies['alpha'].id}", headers=headers)
    for text in (listing.text, detail.text):
        assert RADARR_ROOT not in text
        assert "radarr_path" not in text
        assert "file_relative_path" not in text


async def test_unknown_movies_are_404(client: AsyncClient, headers: dict) -> None:
    assert (await client.get("/api/movies/999999", headers=headers)).status_code == 404
    assert (await client.get("/api/movies/999999/poster", headers=headers)).status_code == 404


# ── The poster file ───────────────────────────────────────────────────────


@pytest.fixture
def library(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "movies"
    (root / "4K").mkdir(parents=True)
    monkeypatch.setattr(settings, "MOVIE_ROOTS", [root])
    monkeypatch.setattr(settings, "RADARR_MOVIE_ROOTS", [PurePosixPath(RADARR_ROOT)])
    return root


async def _movie_with_poster_file(
    session: AsyncSession, library: Path, data: bytes = b"\xff\xd8 a poster"
) -> Movie:
    movie = _movie(1, "Alpha")
    session.add(movie)
    await session.flush()
    folder = library / "4K" / "Alpha"
    folder.mkdir()
    (folder / "poster.jpg").write_bytes(data)
    session.add(_poster(movie, data))
    await session.commit()
    return movie


async def test_the_poster_route_serves_the_recorded_file(
    client: AsyncClient, db_session: AsyncSession, headers: dict, library: Path
) -> None:
    movie = await _movie_with_poster_file(db_session, library)
    response = await client.get(f"/api/movies/{movie.id}/poster", headers=headers)
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"
    assert response.content == b"\xff\xd8 a poster"
    digest = hashlib.sha256(response.content).hexdigest()
    assert response.headers["etag"] == f'"{digest}"'

    again = await client.get(
        f"/api/movies/{movie.id}/poster", headers={**headers, "If-None-Match": f'"{digest}"'}
    )
    assert again.status_code == 304


async def test_the_poster_route_needs_the_key(
    client: AsyncClient, db_session: AsyncSession, library: Path
) -> None:
    movie = await _movie_with_poster_file(db_session, library)
    assert (await client.get(f"/api/movies/{movie.id}/poster")).status_code == 401


async def test_a_poster_file_that_is_gone_is_404(
    client: AsyncClient, db_session: AsyncSession, headers: dict, library: Path
) -> None:
    movie = await _movie_with_poster_file(db_session, library)
    (library / "4K" / "Alpha" / "poster.jpg").unlink()
    assert (await client.get(f"/api/movies/{movie.id}/poster", headers=headers)).status_code == 404


async def test_a_poster_file_that_changed_since_the_sync_is_404(
    client: AsyncClient, db_session: AsyncSession, headers: dict, library: Path
) -> None:
    movie = await _movie_with_poster_file(db_session, library)
    (library / "4K" / "Alpha" / "poster.jpg").write_bytes(b"a longer, different poster")
    assert (await client.get(f"/api/movies/{movie.id}/poster", headers=headers)).status_code == 404


async def test_a_movie_without_a_poster_row_is_404(
    client: AsyncClient, db_session: AsyncSession, headers: dict, library: Path
) -> None:
    movie = _movie(1, "Alpha")
    db_session.add(movie)
    await db_session.commit()
    assert (await client.get(f"/api/movies/{movie.id}/poster", headers=headers)).status_code == 404


async def test_a_symlinked_poster_is_not_served(
    client: AsyncClient, db_session: AsyncSession, headers: dict, library: Path, tmp_path: Path
) -> None:
    movie = await _movie_with_poster_file(db_session, library)
    outside = tmp_path / "outside.jpg"
    outside.write_bytes(b"\xff\xd8 a poster")
    poster = library / "4K" / "Alpha" / "poster.jpg"
    poster.unlink()
    poster.symlink_to(outside)
    assert (await client.get(f"/api/movies/{movie.id}/poster", headers=headers)).status_code == 404


# ── Thumbnails ────────────────────────────────────────────────────────────


def _jpeg(width: int, height: int) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (width, height), (200, 30, 30)).save(out, format="JPEG")
    return out.getvalue()


def _dimensions(data: bytes) -> tuple[int, int]:
    with Image.open(io.BytesIO(data)) as image:
        assert image.format == "JPEG"
        return image.size


@pytest.fixture
def thumbs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path / "data")
    return tmp_path / "data" / "cache" / "thumbs"


async def test_a_thumbnail_is_made_once_kept_and_revalidated(
    client: AsyncClient, db_session: AsyncSession, headers: dict, library: Path, thumbs: Path
) -> None:
    data = _jpeg(600, 900)
    sha = hashlib.sha256(data).hexdigest()
    movie = await _movie_with_poster_file(db_session, library, data)
    url = f"/api/movies/{movie.id}/poster"

    response = await client.get(url, headers=headers, params={"width": 320})

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"
    assert response.headers["etag"] == f'"{sha}-320"'
    assert response.headers["cache-control"] == "private, no-cache"
    assert _dimensions(response.content) == (320, 480)
    assert (thumbs / f"{sha}-320.jpg").read_bytes() == response.content

    again = await client.get(
        url, headers={**headers, "If-None-Match": f'"{sha}-320"'}, params={"width": 320}
    )
    assert again.status_code == 304 and again.content == b""
    # A kept thumbnail is served without reading the share again.
    (library / "4K" / "Alpha" / "poster.jpg").unlink()
    kept = await client.get(url, headers=headers, params={"width": 320})
    assert kept.status_code == 200 and kept.content == response.content
    assert (await client.get(url, headers=headers)).status_code == 404  # the file itself


async def test_a_current_version_may_be_cached_for_a_year(
    client: AsyncClient, db_session: AsyncSession, headers: dict, library: Path, thumbs: Path
) -> None:
    data = _jpeg(600, 900)
    sha = hashlib.sha256(data).hexdigest()
    movie = await _movie_with_poster_file(db_session, library, data)
    url = f"/api/movies/{movie.id}/poster"
    for params in ({"width": 160, "v": sha[:12]}, {"v": sha[:12]}):
        response = await client.get(url, headers=headers, params=params)
        assert response.status_code == 200
        assert response.headers["cache-control"] == "private, max-age=31536000, immutable"
    stale = await client.get(url, headers=headers, params={"width": 160, "v": "0" * 12})
    assert stale.headers["cache-control"] == "private, no-cache"


async def test_a_narrow_poster_is_served_at_its_own_width(
    client: AsyncClient, db_session: AsyncSession, headers: dict, library: Path, thumbs: Path
) -> None:
    movie = await _movie_with_poster_file(db_session, library, _jpeg(500, 750))
    response = await client.get(
        f"/api/movies/{movie.id}/poster", headers=headers, params={"width": 640}
    )
    assert response.status_code == 200 and _dimensions(response.content) == (500, 750)


async def test_thumbnail_refusals(
    client: AsyncClient, db_session: AsyncSession, headers: dict, library: Path, thumbs: Path
) -> None:
    movie = await _movie_with_poster_file(db_session, library)  # bytes that are no image
    url = f"/api/movies/{movie.id}/poster"
    assert (await client.get(url, headers=headers, params={"width": 500})).status_code == 422
    broken = await client.get(url, headers=headers, params={"width": 320})
    assert broken.status_code == 409
    assert broken.json() == {"detail": "the poster file does not decode"}
    assert not thumbs.exists() or not list(thumbs.iterdir())
    assert (await client.get(url, params={"width": 320})).status_code == 401


async def test_a_thumbnail_of_a_poster_changed_since_the_sync_is_404(
    client: AsyncClient, db_session: AsyncSession, headers: dict, library: Path, thumbs: Path
) -> None:
    data = _jpeg(600, 900)
    movie = await _movie_with_poster_file(db_session, library, data)
    # Same size, other bytes: only the hash tells them apart.
    (library / "4K" / "Alpha" / "poster.jpg").write_bytes(data[:-3] + b"xyz")
    response = await client.get(
        f"/api/movies/{movie.id}/poster", headers=headers, params={"width": 320}
    )
    assert response.status_code == 404
    assert not thumbs.exists() or not list(thumbs.iterdir())
