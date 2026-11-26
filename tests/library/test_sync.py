"""The Radarr sync. The movie cases came from the old sync service's tests; the poster, path
check, lock, and dry-run cases are new with the artwork table."""

from __future__ import annotations

import hashlib
import os
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

import pytest
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from marquee.cancellation import JobCancelledError
from marquee.db.engine import create_session_factory
from marquee.db.models import Artwork, Movie
from marquee.fs.paths import PathMapper, RootMapping
from marquee.integrations.arr.exceptions import ArrConnectionError
from marquee.integrations.tmdb.client import MovieDetails
from marquee.library import sync
from marquee.library.sync import (
    MovieDataError,
    PosterAction,
    PosterStat,
    SyncAlreadyRunningError,
    SyncPathError,
    SyncUnavailableError,
    apply_radarr_movie,
    check_root_folders,
    parse_radarr_movie,
    poster_action,
    stat_poster,
)

RADARR_ROOT = "/radarr/movies"


def _radarr_movie(**overrides: Any) -> dict[str, Any]:
    """A minimal entry of Radarr's movie list."""
    movie: dict[str, Any] = {
        "id": 1,
        "title": "Dune",
        "sortTitle": "dune",
        "year": 2021,
        "tmdbId": 438631,
        "imdbId": "tt1160419",
        "genres": ["Science Fiction", "Adventure"],
        "path": f"{RADARR_ROOT}/4K/Dune (2021)",
        "rootFolderPath": f"{RADARR_ROOT}/4K",
        "hasFile": True,
        "added": "2024-05-01T10:20:30Z",
        "movieFile": {"id": 11, "relativePath": "Dune (2021).mkv", "size": 1234},
    }
    movie.update(overrides)
    return movie


class FakeRadarr:
    def __init__(self, movies: list[dict[str, Any]], roots: list[str] | None = None) -> None:
        self.movies = movies
        self.roots = roots if roots is not None else [f"{RADARR_ROOT}/1080p", f"{RADARR_ROOT}/4K"]

    async def get_root_folders(self) -> list[dict]:
        return [{"path": root} for root in self.roots]

    async def get_movies(self) -> list[dict]:
        return self.movies


class FakeTMDB:
    def __init__(self, details: MovieDetails | Exception | None = None) -> None:
        self.details = details or MovieDetails(
            director="Denis Villeneuve",
            production_companies=["Legendary Pictures", "Warner Bros."],
            tagline="It begins.",
        )
        self.calls: list[int] = []

    async def get_movie_details(self, tmdb_id: int) -> MovieDetails:
        self.calls.append(tmdb_id)
        if isinstance(self.details, Exception):
            raise self.details
        return self.details


# ── Parsing Radarr's record (no database) ─────────────────────────────────


def test_parse_keeps_what_the_schema_holds() -> None:
    record = parse_radarr_movie(_radarr_movie())
    assert record.radarr_id == 1
    assert record.title == "Dune"
    assert record.sort_title == "dune"
    assert record.year == 2021
    assert record.tmdb_id == 438631
    assert record.genres == ["Science Fiction", "Adventure"]
    assert record.radarr_path == f"{RADARR_ROOT}/4K/Dune (2021)"
    assert (record.has_file, record.file_relative_path, record.file_size_bytes) == (
        True,
        "Dune (2021).mkv",
        1234,
    )
    assert record.radarr_file_id == 11
    assert record.radarr_added_at == datetime(2024, 5, 1, 10, 20, 30, tzinfo=UTC)


def test_parse_treats_radarr_zeroes_and_gaps_as_unknown() -> None:
    record = parse_radarr_movie(
        _radarr_movie(year=0, tmdbId=0, sortTitle="", genres=None, added="0001-01-01T00:00:00Z")
    )
    assert record.year is None
    assert record.tmdb_id is None
    assert record.sort_title == "dune"
    assert record.genres == []
    assert record.radarr_added_at is None


def test_a_movie_without_a_file_has_no_file_fields() -> None:
    record = parse_radarr_movie(_radarr_movie(hasFile=False))
    assert not record.has_file
    assert (record.file_relative_path, record.file_size_bytes, record.radarr_file_id) == (
        None,
        None,
        None,
    )


@pytest.mark.parametrize(
    "data",
    [
        None,
        {"title": "No id", "path": "/radarr/movies/x"},
        _radarr_movie(id=0),
        _radarr_movie(id=2**31),
        _radarr_movie(title=None),
        _radarr_movie(title="   "),
        _radarr_movie(path=None),
        _radarr_movie(path="relative/Dune"),
        _radarr_movie(path="/radarr/movies/\0"),
    ],
)
def test_parse_refuses_an_unusable_movie(data: Any) -> None:
    with pytest.raises(MovieDataError):
        parse_radarr_movie(data)


def test_apply_reports_whether_anything_changed() -> None:
    now = datetime(2026, 11, 24, tzinfo=UTC)
    movie = Movie(radarr_id=1, is_present=True)
    record = parse_radarr_movie(_radarr_movie())
    assert apply_radarr_movie(movie, record, now) is True
    assert apply_radarr_movie(movie, record, now) is False
    assert apply_radarr_movie(movie, parse_radarr_movie(_radarr_movie(title="Dune 2")), now)


def test_a_changed_tmdb_id_clears_the_tmdb_details() -> None:
    now = datetime(2026, 11, 24, tzinfo=UTC)
    movie = Movie(radarr_id=1, is_present=True, director="Someone", tmdb_enriched_at=now)
    apply_radarr_movie(movie, parse_radarr_movie(_radarr_movie()), now)
    assert (movie.director, movie.production_companies, movie.tagline) == (None, None, None)
    assert movie.tmdb_enriched_at is None


# ── Poster decisions (no database) ────────────────────────────────────────

_STAT = PosterStat(size_bytes=10, modified_at=datetime(2026, 1, 2, 3, 4, 5, 6, tzinfo=UTC))


def _row(**values: Any) -> Artwork:
    defaults: dict[str, Any] = {
        "origin": "library",
        "file_name": "poster.jpg",
        "size_bytes": _STAT.size_bytes,
        "file_modified_at": _STAT.modified_at,
    }
    return Artwork(**{**defaults, **values})


@pytest.mark.parametrize(
    ("row", "poster", "action"),
    [
        (None, None, PosterAction.LEAVE),
        (None, _STAT, PosterAction.HASH),
        (_row(), _STAT, PosterAction.KEEP),
        (_row(size_bytes=11), _STAT, PosterAction.HASH),
        (_row(file_modified_at=datetime(2098, 1, 1, tzinfo=UTC)), _STAT, PosterAction.HASH),
        (_row(), None, PosterAction.DELETE),
        # A poster Marquee wrote is kept, hashed again, or deleted the same way.
        (_row(origin="marquee"), _STAT, PosterAction.KEEP),
        (_row(origin="marquee", size_bytes=11), _STAT, PosterAction.HASH),
        (_row(origin="marquee"), None, PosterAction.DELETE),
        # An origin the sync does not know is never changed, whatever is on disk.
        (_row(origin="deployed"), None, PosterAction.LEAVE),
        (_row(origin="deployed", size_bytes=99), _STAT, PosterAction.LEAVE),
    ],
)
def test_poster_action(row: Artwork | None, poster: PosterStat | None, action: str) -> None:
    assert poster_action(row, poster) is action


def test_modification_times_compare_by_equality_even_in_the_future(tmp_path: Path) -> None:
    poster = tmp_path / "poster.jpg"
    poster.write_bytes(b"x")
    future_ns = 4_070_908_800_123_456_789  # 2099, with sub-microsecond digits
    os.utime(poster, ns=(future_ns, future_ns))
    first = stat_poster(poster)
    assert first is not None
    assert first.modified_at == datetime(2099, 1, 1, 0, 0, 0, 123456, tzinfo=UTC)
    assert poster_action(_row(size_bytes=1, file_modified_at=first.modified_at), first) is (
        PosterAction.KEEP
    )


def test_stat_poster_ignores_symbolic_links_and_missing_files(tmp_path: Path) -> None:
    target = tmp_path / "real.jpg"
    target.write_bytes(b"x")
    link = tmp_path / "poster.jpg"
    link.symlink_to(target)
    assert stat_poster(link) is None
    assert stat_poster(tmp_path / "absent.jpg") is None


# ── The path check (no database) ──────────────────────────────────────────


def _mapper(local_root: Path) -> PathMapper:
    return PathMapper([RootMapping(PurePosixPath(RADARR_ROOT), local_root)])


def _library(root: Path) -> Path:
    """Two quality folders, as Radarr's two root folders see them."""
    (root / "1080p").mkdir(parents=True)
    (root / "4K").mkdir()
    return root


def test_the_path_check_passes_when_every_root_folder_exists(tmp_path: Path) -> None:
    root = _library(tmp_path / "movies")
    folders = [{"path": f"{RADARR_ROOT}/1080p"}, {"path": f"{RADARR_ROOT}/4K"}]
    assert check_root_folders(folders, _mapper(root)) == 2


def test_the_path_check_names_a_missing_folder_and_the_settings(tmp_path: Path) -> None:
    root = tmp_path / "movies"
    (root / "4K").mkdir(parents=True)
    with pytest.raises(SyncPathError, match=r"1080p.*MOVIE_ROOTS and RADARR_MOVIE_ROOTS"):
        check_root_folders([{"path": f"{RADARR_ROOT}/1080p"}], _mapper(root))


def test_the_path_check_names_an_unmapped_folder(tmp_path: Path) -> None:
    with pytest.raises(SyncPathError, match=r"/elsewhere/movies .*RADARR_MOVIE_ROOTS"):
        check_root_folders([{"path": "/elsewhere/movies"}], _mapper(_library(tmp_path / "m")))


def test_the_path_check_needs_at_least_one_root_folder(tmp_path: Path) -> None:
    with pytest.raises(SyncPathError, match="no root folders"):
        check_root_folders([], _mapper(_library(tmp_path / "m")))


# ── The sync against the database ─────────────────────────────────────────


def _title_folder(root: Path, quality: str, name: str, *, poster: bytes | None = None) -> Path:
    folder = root / quality / name
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{name} {{imdbid-tt0000001}} - [Bluray-1080p].mkv").write_bytes(b"")
    if poster is not None:
        (folder / "poster.jpg").write_bytes(poster)
    return folder


@pytest.fixture
def library(tmp_path: Path) -> Path:
    root = _library(tmp_path / "movies")
    _title_folder(root, "4K", "Dune (2021)", poster=b"\xff\xd8dune poster")
    return root


@pytest.fixture
def factory(db_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return create_session_factory(db_engine)


Run = Callable[..., Any]


@pytest.fixture
def run(factory: async_sessionmaker[AsyncSession], library: Path) -> Run:
    async def _run(radarr: FakeRadarr, **kwargs: Any) -> sync.SyncReport:
        return await sync.sync_library(
            session_factory=factory,
            radarr=radarr,
            mapper=_mapper(library),
            movie_roots=[library],
            **kwargs,
        )

    return _run


async def _movies(factory: async_sessionmaker[AsyncSession]) -> list[Movie]:
    async with factory() as session:
        return list((await session.scalars(select(Movie).order_by(Movie.radarr_id))).all())


async def _artwork(factory: async_sessionmaker[AsyncSession]) -> list[Artwork]:
    async with factory() as session:
        return list((await session.scalars(select(Artwork))).all())


@pytest.mark.db
async def test_a_new_movie_is_created(run: Run, factory: async_sessionmaker) -> None:
    report = await run(FakeRadarr([_radarr_movie()]))
    assert (report.created, report.updated, report.unchanged, report.failed) == (1, 0, 0, 0)
    (movie,) = await _movies(factory)
    assert movie.title == "Dune"
    assert movie.tmdb_id == 438631
    assert movie.radarr_path == f"{RADARR_ROOT}/4K/Dune (2021)"
    assert movie.has_file is True
    assert movie.is_present is True


@pytest.mark.db
async def test_an_existing_movie_is_updated_not_duplicated(
    run: Run, factory: async_sessionmaker
) -> None:
    await run(FakeRadarr([_radarr_movie(title="Old Title", year=2000)]))
    report = await run(FakeRadarr([_radarr_movie()]))
    assert (report.created, report.updated) == (0, 1)
    (movie,) = await _movies(factory)
    assert (movie.title, movie.year) == ("Dune", 2021)


@pytest.mark.db
async def test_a_second_sync_creates_nothing_and_hashes_nothing(
    run: Run, factory: async_sessionmaker
) -> None:
    first = await run(FakeRadarr([_radarr_movie()]))
    (before,) = await _movies(factory)
    second = await run(FakeRadarr([_radarr_movie()]))
    (after,) = await _movies(factory)
    assert (first.posters_found, first.posters_hashed) == (1, 1)
    assert (second.created, second.updated, second.unchanged) == (0, 0, 1)
    assert (second.posters_found, second.posters_hashed, second.posters_removed) == (1, 0, 0)
    assert after.last_seen_at > before.last_seen_at
    assert after.updated_at == before.updated_at


@pytest.mark.db
async def test_a_movie_radarr_drops_is_retired_and_comes_back(
    run: Run, factory: async_sessionmaker
) -> None:
    await run(FakeRadarr([_radarr_movie(), _radarr_movie(id=99, title="Retire Me")]))
    report = await run(FakeRadarr([_radarr_movie()]))
    assert report.retired == 1
    retired = (await _movies(factory))[1]
    assert (retired.is_present, retired.retired_at is not None) == (False, True)

    report = await run(FakeRadarr([_radarr_movie(), _radarr_movie(id=99, title="Retire Me")]))
    assert report.updated == 1
    back = (await _movies(factory))[1]
    assert (back.is_present, back.retired_at) == (True, None)


@pytest.mark.db
async def test_a_partial_sync_never_retires(run: Run, factory: async_sessionmaker) -> None:
    await run(FakeRadarr([_radarr_movie(), _radarr_movie(id=99, title="Keep Me")]))
    report = await run(FakeRadarr([_radarr_movie(), {"id": 2, "title": None}]))
    assert (report.failed, report.retired, report.failed_radarr_ids) == (1, 0, [2])
    keep = (await _movies(factory))[1]
    assert (keep.is_present, keep.retired_at) == (True, None)


@pytest.mark.db
async def test_a_bad_movie_is_counted_and_the_others_go_on(
    run: Run, factory: async_sessionmaker
) -> None:
    report = await run(
        FakeRadarr(
            [
                _radarr_movie(),
                {"id": 2, "title": None},
                _radarr_movie(id=3, path=f"{RADARR_ROOT}/../etc"),
                _radarr_movie(id=1),
            ]
        )
    )
    assert report.created == 1
    assert report.failed == 3
    assert [m.radarr_id for m in await _movies(factory)] == [1]


@pytest.mark.db
async def test_tmdb_details_fill_in_once(run: Run, factory: async_sessionmaker) -> None:
    tmdb = FakeTMDB()
    report = await run(FakeRadarr([_radarr_movie()]), tmdb=tmdb)
    assert (report.tmdb_enriched, report.tmdb_failed) == (1, 0)
    (movie,) = await _movies(factory)
    assert movie.director == "Denis Villeneuve"
    assert movie.production_companies == ["Legendary Pictures", "Warner Bros."]
    assert movie.tagline == "It begins."
    assert movie.tmdb_enriched_at is not None

    await run(FakeRadarr([_radarr_movie()]), tmdb=tmdb)
    assert tmdb.calls == [438631]


@pytest.mark.db
async def test_tmdb_details_only_for_movies_with_a_file(run: Run) -> None:
    tmdb = FakeTMDB()
    await run(FakeRadarr([_radarr_movie(hasFile=False)]), tmdb=tmdb)
    assert tmdb.calls == []


@pytest.mark.db
async def test_a_changed_tmdb_id_fetches_the_details_again(
    run: Run, factory: async_sessionmaker
) -> None:
    await run(FakeRadarr([_radarr_movie(tmdbId=99)]), tmdb=FakeTMDB())
    tmdb = FakeTMDB(MovieDetails(director="Another", production_companies=["New"], tagline=None))
    await run(FakeRadarr([_radarr_movie(tmdbId=438631)]), tmdb=tmdb)
    (movie,) = await _movies(factory)
    assert movie.tmdb_id == 438631
    assert (movie.director, movie.production_companies, movie.tagline) == (
        "Another",
        ["New"],
        None,
    )
    assert tmdb.calls == [438631]


@pytest.mark.db
async def test_a_tmdb_failure_does_not_fail_the_sync(run: Run, factory: async_sessionmaker) -> None:
    report = await run(FakeRadarr([_radarr_movie()]), tmdb=FakeTMDB(RuntimeError("boom")))
    assert (report.failed, report.tmdb_failed, report.ok) == (0, 1, True)
    (movie,) = await _movies(factory)
    assert (movie.director, movie.tmdb_enriched_at) == (None, None)


@pytest.mark.db
async def test_the_poster_is_hashed_and_recorded_by_file_name(
    run: Run, factory: async_sessionmaker, library: Path
) -> None:
    await run(FakeRadarr([_radarr_movie()]))
    (row,) = await _artwork(factory)
    poster = library / "4K" / "Dune (2021)" / "poster.jpg"
    assert row.file_name == "poster.jpg"
    assert row.sha256 == hashlib.sha256(poster.read_bytes()).hexdigest()
    assert row.size_bytes == poster.stat().st_size
    assert (row.subject_kind, row.artwork_kind, row.origin) == ("movie", "poster", "library")


@pytest.mark.db
async def test_a_changed_poster_is_hashed_again(
    run: Run, factory: async_sessionmaker, library: Path
) -> None:
    await run(FakeRadarr([_radarr_movie()]))
    poster = library / "4K" / "Dune (2021)" / "poster.jpg"
    poster.write_bytes(b"\xff\xd8a different poster")
    report = await run(FakeRadarr([_radarr_movie()]))
    assert report.posters_hashed == 1
    (row,) = await _artwork(factory)
    assert row.sha256 == hashlib.sha256(b"\xff\xd8a different poster").hexdigest()


async def _make_marquees(factory: async_sessionmaker[AsyncSession]) -> None:
    """Mark every poster row as one Marquee wrote, as a deploy leaves it."""
    async with factory() as session:
        await session.execute(update(Artwork).values(origin="marquee"))
        await session.commit()


@pytest.mark.db
async def test_a_poster_marquee_wrote_keeps_its_origin_while_its_bytes_hold(
    run: Run, factory: async_sessionmaker, library: Path
) -> None:
    await run(FakeRadarr([_radarr_movie()]))
    await _make_marquees(factory)
    poster = library / "4K" / "Dune (2021)" / "poster.jpg"

    report = await run(FakeRadarr([_radarr_movie()]))
    assert (report.posters_hashed, report.posters_replaced_by_hand) == (0, 0)

    # Touched but the same bytes: the new time is recorded and the origin stays.
    os.utime(poster, ns=(1_800_000_000_000_000_000, 1_800_000_000_000_000_000))
    report = await run(FakeRadarr([_radarr_movie()]))
    assert (report.posters_hashed, report.posters_replaced_by_hand) == (1, 0)
    (row,) = await _artwork(factory)
    assert row.origin == "marquee"
    assert row.file_modified_at == stat_poster(poster).modified_at  # type: ignore[union-attr]


@pytest.mark.db
async def test_a_poster_marquee_wrote_then_replaced_by_hand_is_the_owners_again(
    run: Run, factory: async_sessionmaker, library: Path
) -> None:
    await run(FakeRadarr([_radarr_movie()]))
    await _make_marquees(factory)
    poster = library / "4K" / "Dune (2021)" / "poster.jpg"
    poster.write_bytes(b"\xff\xd8the owner's own pick")

    report = await run(FakeRadarr([_radarr_movie()]))

    assert (report.posters_hashed, report.posters_replaced_by_hand) == (1, 1)
    (row,) = await _artwork(factory)
    assert row.origin == "library"
    assert row.sha256 == hashlib.sha256(b"\xff\xd8the owner's own pick").hexdigest()


@pytest.mark.db
async def test_a_poster_marquee_wrote_that_is_gone_loses_its_row(
    run: Run, factory: async_sessionmaker, library: Path
) -> None:
    await run(FakeRadarr([_radarr_movie()]))
    await _make_marquees(factory)
    (library / "4K" / "Dune (2021)" / "poster.jpg").unlink()

    report = await run(FakeRadarr([_radarr_movie()]))

    assert report.posters_removed == 1
    assert await _artwork(factory) == []


@pytest.mark.db
async def test_a_poster_that_is_gone_loses_its_row(
    run: Run, factory: async_sessionmaker, library: Path
) -> None:
    await run(FakeRadarr([_radarr_movie()]))
    (library / "4K" / "Dune (2021)" / "poster.jpg").unlink()
    report = await run(FakeRadarr([_radarr_movie()]))
    assert report.posters_removed == 1
    assert await _artwork(factory) == []


@pytest.mark.db
async def test_a_folder_the_scan_did_not_find_keeps_its_row(
    run: Run, factory: async_sessionmaker, library: Path
) -> None:
    await run(FakeRadarr([_radarr_movie()]))
    (library / "4K" / "Dune (2021)").rename(library / "4K" / ".moved away")
    report = await run(FakeRadarr([_radarr_movie()]))
    assert report.posters_removed == 0
    assert report.movies_without_folder == ["Dune (2021)"]
    assert len(await _artwork(factory)) == 1


@pytest.mark.db
async def test_folders_and_movies_that_do_not_match_are_reported(run: Run, library: Path) -> None:
    _title_folder(library, "1080p", "Not In Radarr (1999)")
    report = await run(
        FakeRadarr(
            [
                _radarr_movie(),
                _radarr_movie(id=2, title="Gone", year=2001, path=f"{RADARR_ROOT}/4K/Gone (2001)"),
                _radarr_movie(id=3, title="Wanted", path=f"{RADARR_ROOT}/4K/W", hasFile=False),
            ]
        )
    )
    assert report.folders_without_movie == ["1080p/Not In Radarr (1999)"]
    assert report.movies_without_folder == ["Gone (2001)"]
    assert report.with_file == 2


@pytest.mark.db
async def test_a_missing_root_folder_stops_the_sync_before_any_write(
    run: Run, factory: async_sessionmaker, library: Path
) -> None:
    (library / "1080p").rmdir()
    with pytest.raises(SyncPathError, match="1080p"):
        await run(FakeRadarr([_radarr_movie()]))
    assert await _movies(factory) == []


async def test_a_share_that_does_not_answer_in_time_is_not_a_missing_folder(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A slow share may answer on the next attempt; a folder that is not there will not."""
    root = _library(tmp_path / "movies")
    release = threading.Event()

    def hangs(root_folders: Any, mapper: PathMapper) -> int:
        release.wait(5)
        return 2

    monkeypatch.setattr(sync, "check_root_folders", hangs)
    monkeypatch.setattr(sync, "ROOT_FOLDER_TIMEOUT_S", 0.05)
    try:
        with pytest.raises(SyncUnavailableError, match="did not answer within") as raised:
            await sync.sync_library(
                session_factory=None,  # type: ignore[arg-type]
                radarr=FakeRadarr([]),
                mapper=_mapper(root),
                movie_roots=[root],
            )
    finally:
        release.set()
    assert not isinstance(raised.value, SyncPathError)


@pytest.mark.parametrize("request_name", ["get_root_folders", "get_movies"])
async def test_radarr_not_answering_is_unavailable(request_name: str, tmp_path: Path) -> None:
    root = _library(tmp_path / "movies")
    radarr = FakeRadarr([])

    async def times_out() -> list[dict]:
        raise ArrConnectionError("Radarr request timed out.")

    setattr(radarr, request_name, times_out)
    with pytest.raises(SyncUnavailableError, match="Radarr did not answer: Radarr request timed"):
        await sync.sync_library(
            session_factory=None,  # type: ignore[arg-type]
            radarr=radarr,
            mapper=_mapper(root),
            movie_roots=[root],
        )


@pytest.mark.db
async def test_an_absent_library_cannot_look_like_deleted_posters(
    factory: async_sessionmaker, run: Run, library: Path, tmp_path: Path
) -> None:
    await run(FakeRadarr([_radarr_movie()]))
    absent = tmp_path / "unmounted"
    with pytest.raises(SyncPathError):
        await sync.sync_library(
            session_factory=factory,
            radarr=FakeRadarr([_radarr_movie()]),
            mapper=_mapper(absent),
            movie_roots=[absent],
        )
    assert len(await _artwork(factory)) == 1


@pytest.mark.db
async def test_a_dry_run_writes_nothing(run: Run, factory: async_sessionmaker) -> None:
    report = await run(FakeRadarr([_radarr_movie()]), dry_run=True)
    assert (report.dry_run, report.created, report.posters_hashed) == (True, 1, 1)
    assert await _movies(factory) == []
    assert await _artwork(factory) == []


@pytest.mark.db
async def test_a_sync_started_meanwhile_is_refused(
    run: Run, db_engine: AsyncEngine, factory: async_sessionmaker
) -> None:
    async with db_engine.connect() as holder:
        await holder.execute(text("select pg_advisory_lock(:key)"), {"key": sync.SYNC_LOCK_KEY})
        with pytest.raises(SyncAlreadyRunningError, match="another sync is running"):
            await run(FakeRadarr([_radarr_movie()]))
        await holder.execute(text("select pg_advisory_unlock(:key)"), {"key": sync.SYNC_LOCK_KEY})
    assert await _movies(factory) == []


@pytest.mark.db
async def test_cancellation_between_phases_writes_nothing(
    run: Run, factory: async_sessionmaker
) -> None:
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(JobCancelledError):
        await run(FakeRadarr([_radarr_movie()]), cancel=cancel)
    assert await _movies(factory) == []
