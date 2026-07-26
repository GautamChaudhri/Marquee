"""Tests for the enriched /api/library/movies list + filters (frontend G2)."""

from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from marquee.config import settings
from marquee.main import app
from marquee.models import Job, MediaFile, Movie


@pytest_asyncio.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


async def _seed(db: AsyncSession) -> None:
    # Alpha — approved poster, 4K, has a file.
    alpha = Movie(
        title="Alpha",
        year=2020,
        folder_path="/m/a",
        poster_path="/m/a/poster.jpg",
        poster_user_approved=True,
        video_width=3840,
        video_height=1600,
        genres=["Sci-Fi"],
    )
    # Bravo — unreviewed AI pick, 1080p, no file on disk.
    bravo = Movie(
        title="Bravo",
        year=2022,
        folder_path="/m/b",
        poster_path="/m/b/poster.jpg",
        poster_ai_selected=True,
        video_width=1920,
        video_height=1080,
    )
    # Charlie — missing poster, no resolution, has a file.
    charlie = Movie(title="Charlie", year=2019, folder_path="/m/c")
    db.add_all([alpha, bravo, charlie])
    await db.flush()

    db.add_all(
        [
            MediaFile(
                source="radarr",
                source_key="r:a",
                path="/m/a/a.mkv",
                movie_id=alpha.id,
                is_active=True,
            ),
            MediaFile(
                source="radarr",
                source_key="r:c",
                path="/m/c/c.mkv",
                movie_id=charlie.id,
                is_active=True,
            ),
        ]
    )
    await db.commit()


@pytest.mark.asyncio
async def test_list_enrichment_fields(db: AsyncSession, client: AsyncClient):
    await _seed(db)
    resp = await client.get("/api/library/movies?sort=title")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 2
    by_title = {m["title"]: m for m in body["items"]}

    alpha = by_title["Alpha"]
    assert alpha["resolution"] == "4K"
    assert alpha["poster_status"] == "approved"
    assert alpha["genres"] == ["Sci-Fi"]

    assert "Bravo" not in by_title

    charlie = by_title["Charlie"]
    assert charlie["resolution"] is None
    assert charlie["poster_status"] == "missing"


@pytest.mark.asyncio
async def test_movie_library_item_snapshot_is_the_full_contract(
    db: AsyncSession, client: AsyncClient
):
    await _seed(db)

    resp = await client.get("/api/library/movies?q=Alpha")
    assert resp.status_code == 200
    item = resp.json()["items"][0]
    assert resp.json() == {
        "items": [
            {
                "container": None,
                "genres": ["Sci-Fi"],
                "id": item["id"],
                "media_file_id": item["media_file_id"],
                "poster_status": "approved",
                "poster_url": f"/api/library/movies/{item['id']}/poster",
                "resolution": "4K",
                "title": "Alpha",
                "tmdb_id": None,
                "video_height": 1600,
                "video_width": 3840,
                "year": 2020,
            }
        ],
        "page": 1,
        "page_size": 50,
        "total": 1,
    }


@pytest.mark.asyncio
async def test_list_filters_and_sort(db: AsyncSession, client: AsyncClient):
    await _seed(db)

    r = await client.get("/api/library/movies?poster_status=missing")
    body = r.json()
    assert body["total"] == 1
    assert [m["title"] for m in body["items"]] == ["Charlie"]

    r = await client.get("/api/library/movies?q=bra")
    assert r.json()["total"] == 0

    r = await client.get("/api/library/movies?sort=year")
    assert [m["title"] for m in r.json()["items"]] == ["Alpha", "Charlie"]


@pytest.mark.asyncio
async def test_include_unavailable_returns_radarr_movies_without_files(
    db: AsyncSession, client: AsyncClient
):
    await _seed(db)

    r = await client.get("/api/library/movies?include_unavailable=true&q=bra")
    body = r.json()
    assert body["total"] == 1
    bravo = body["items"][0]
    assert bravo["title"] == "Bravo"
    assert bravo["media_file_id"] is None


@pytest.mark.asyncio
async def test_retired_movies_are_excluded_from_active_library(
    db: AsyncSession, client: AsyncClient
):
    retired = Movie(
        title="Retired",
        year=2020,
        folder_path="/m/retired",
        is_present=False,
    )
    db.add(retired)
    await db.commit()

    listing = await client.get("/api/library/movies?include_unavailable=true")
    assert listing.status_code == 200
    assert listing.json()["items"] == []

    detail = await client.get(f"/api/library/movies/{retired.id}")
    assert detail.status_code == 404


@pytest.mark.asyncio
async def test_detail_has_derived_fields(db: AsyncSession, client: AsyncClient):
    await _seed(db)
    list_body = (await client.get("/api/library/movies?q=Alpha")).json()
    movie_id = list_body["items"][0]["id"]

    detail = (await client.get(f"/api/library/movies/{movie_id}")).json()
    assert detail["poster_status"] == "approved"
    assert detail["resolution"] == "4K"
    assert "media_file_path" in detail


@pytest.mark.asyncio
async def test_delete_movie_poster(
    db: AsyncSession,
    client: AsyncClient,
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    installed_pgqueuer,
):
    # 1. Create a dummy movie with a valid folder_path and poster_path
    movie_folder = tmp_path / "Dune (2021)"
    movie_folder.mkdir()
    monkeypatch.setattr(settings, "MEDIA_ROOTS", [str(tmp_path)])
    poster_file = movie_folder / "poster.jpg"
    poster_file.write_bytes(b"dummy image data")

    movie = Movie(
        title="Dune Delete Test",
        year=2021,
        folder_path=str(movie_folder),
        poster_path=str(poster_file),
        poster_user_approved=True,
    )
    db.add(movie)
    await db.commit()
    await db.refresh(movie)

    # 2. Call DELETE endpoint
    resp = await client.delete(
        f"/api/library/movies/{movie.id}/poster",
        headers={"Idempotency-Key": "poster_reset:movie-reset-1"},
    )
    assert resp.status_code == 202
    body = resp.json()
    job = await db.get(Job, body["job_id"])
    assert job.type == "poster_reset"
    assert job.subject_kind == "movie"
    assert job.subject_reference == str(movie.id)

    # Submission never performs the mutation inline.
    assert poster_file.exists()

    # 4. Verify DB state is reset
    await db.refresh(movie)
    assert movie.poster_path == str(poster_file)
    assert movie.poster_user_approved is True
