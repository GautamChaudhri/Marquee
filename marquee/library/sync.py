"""Copy the movie list from Radarr into the database, and record the posters on disk.

One sync, in order:

1. **Path check.** Every root folder Radarr reports must translate, through
   :mod:`marquee.fs.paths`, to a folder that exists here. If one does not (a wrong mapping,
   or the share is not mounted), the sync stops before it reads or writes anything else, so
   a missing share can never look like a library whose posters were all deleted.
2. **Radarr and the library.** One request for every movie Radarr lists, and one scan of the
   movie folders with :mod:`marquee.library.folders`.
3. **Movies.** Under a PostgreSQL advisory lock held for the rest of the sync, every Radarr
   movie is upserted by its Radarr id. A movie that cannot be stored is counted and logged,
   and the others go on. Movies Radarr no longer lists are retired (kept, marked absent), and
   a retired movie that comes back is restored, but only when no movie failed: a partial sync
   never retires anything.
4. **Posters.** Each scanned folder is matched to its movie through the translated Radarr
   path. A ``poster.jpg`` is stat'ed, and read and hashed only when there is no row for it
   yet or its size or modification time changed (compared for equality: the share holds
   files dated in the future). When the file is gone from a folder the scan found, its row
   is deleted. A poster Marquee wrote (origin ``marquee``) is treated the same way, with one
   difference: when its bytes changed, the owner replaced it by hand, so its row becomes
   origin ``library`` and the report counts it. The ``deployments`` audit is never touched.
5. **TMDB details** (director, production companies, tagline) for movies with a file that
   were never enriched, at most five requests at a time. A failure is logged and does not
   fail the sync.

Everything happens in one transaction, committed at the end; a dry run rolls it back.
Filesystem work runs off the event loop. Nothing here writes to the media library.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import stat
import time
from collections.abc import Awaitable, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from marquee.cancellation import CancellationSignal, raise_if_cancelled
from marquee.db.models import (
    ARTWORK_POSTER,
    ORIGIN_LIBRARY,
    ORIGIN_MARQUEE,
    SUBJECT_MOVIE,
    Artwork,
    Movie,
)
from marquee.db.models.artwork import ORIGINS
from marquee.fs.boundary import FilesystemBoundary, FilesystemBoundaryError, boundary_for_roots
from marquee.fs.paths import PathMapper, PathMappingError, UnsafePathError
from marquee.integrations.arr.exceptions import ArrConnectionError
from marquee.library.folders import POSTER_NAME, LibraryRootError, MovieFolder, scan_movie_roots

logger = logging.getLogger(__name__)

# One sync at a time across every process that shares the database.
SYNC_LOCK_KEY = int.from_bytes(b"marqsync", "big")
TMDB_CONCURRENCY = 5
# A root folder on a network share that does not answer within this long counts as missing.
ROOT_FOLDER_TIMEOUT_S = 30.0
_HASH_CHUNK = 1024 * 1024
_CANCELLED = "library sync cancelled"


class SyncError(Exception):
    """The sync cannot go on; the message says why."""


class SyncPathError(SyncError):
    """Radarr's folders do not map to folders here, or the library cannot be scanned."""


class SyncUnavailableError(SyncError):
    """The share or Radarr did not answer in time. Unlike a folder that does not map, a later
    attempt may succeed (a share still waking up, a busy Radarr)."""


class SyncAlreadyRunningError(SyncError):
    """Another sync holds the lock."""


class MovieDataError(ValueError):
    """One movie from Radarr cannot be stored."""


class RadarrSource(Protocol):
    async def get_root_folders(self) -> list[dict]: ...

    async def get_movies(self) -> list[dict]: ...


class MovieDetailsSource(Protocol):
    async def get_movie_details(self, tmdb_id: int) -> Any: ...


# ── The report ────────────────────────────────────────────────────────────


@dataclass
class SyncReport:
    dry_run: bool
    root_folders_checked: int = 0
    movies_listed: int = 0
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    retired: int = 0
    failed: int = 0
    with_file: int = 0
    posters_found: int = 0
    posters_hashed: int = 0
    posters_removed: int = 0
    posters_unreadable: int = 0
    """Posters that could not be stat'ed or read this time; their rows were left alone."""
    posters_replaced_by_hand: int = 0
    """Posters Marquee wrote whose bytes changed since: the owner's again (origin library)."""
    tmdb_enriched: int = 0
    tmdb_failed: int = 0
    tmdb_skipped: bool = False
    """True when no TMDB token is configured, so nothing was enriched."""
    folders_without_movie: list[str] = field(default_factory=list)
    """Folders on disk (``<quality>/<name>``) that no Radarr movie points at."""
    movies_without_folder: list[str] = field(default_factory=list)
    """Movies Radarr says have a file whose folder the scan did not find."""
    failed_radarr_ids: list[int | None] = field(default_factory=list)
    seconds: float = 0.0

    @property
    def ok(self) -> bool:
        return self.failed == 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ── Radarr's movie record ─────────────────────────────────────────────────


@dataclass(frozen=True)
class RadarrMovie:
    """The fields the sync keeps from one entry of Radarr's movie list."""

    radarr_id: int
    title: str
    sort_title: str
    year: int | None
    tmdb_id: int | None
    imdb_id: str | None
    genres: list[str]
    radarr_path: str
    has_file: bool
    file_relative_path: str | None
    file_size_bytes: int | None
    radarr_file_id: int | None
    radarr_added_at: datetime | None


_INT_MAX = 2**31 - 1
_BIGINT_MAX = 2**63 - 1


def _positive_int(value: Any) -> int | None:
    """Radarr uses 0 for "unknown" in ids and years. Anything too big for the column is unknown."""
    if isinstance(value, int) and not isinstance(value, bool) and 0 < value <= _INT_MAX:
        return value
    return None


def _text(value: Any) -> str | None:
    if isinstance(value, str) and value.strip() and "\0" not in value:
        return value.strip()
    return None


def _timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.year <= 1:
        return None
    return (parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)).astimezone(UTC)


def parse_radarr_movie(data: Any) -> RadarrMovie:
    """Validate one entry of ``GET /api/v3/movie``; raise :class:`MovieDataError` if unusable."""
    if not isinstance(data, dict):
        raise MovieDataError("not an object")
    radarr_id = _positive_int(data.get("id"))
    if radarr_id is None:
        raise MovieDataError("no Radarr id")
    title = _text(data.get("title"))
    if title is None:
        raise MovieDataError("no title")
    path = data.get("path")
    if not isinstance(path, str) or not path.startswith("/") or "\0" in path:
        raise MovieDataError("no absolute folder path")
    genres = data.get("genres")
    has_file = data.get("hasFile") is True
    movie_file = data.get("movieFile") if has_file else None
    movie_file = movie_file if isinstance(movie_file, dict) else {}
    size = movie_file.get("size")
    if not (isinstance(size, int) and not isinstance(size, bool) and 0 <= size <= _BIGINT_MAX):
        size = None
    return RadarrMovie(
        radarr_id=radarr_id,
        title=title,
        sort_title=_text(data.get("sortTitle")) or title.lower(),
        year=_positive_int(data.get("year")),
        tmdb_id=_positive_int(data.get("tmdbId")),
        imdb_id=_text(data.get("imdbId")),
        genres=[g for g in genres if isinstance(g, str)] if isinstance(genres, list) else [],
        radarr_path=path,
        has_file=has_file,
        file_relative_path=_text(movie_file.get("relativePath")),
        file_size_bytes=size,
        radarr_file_id=_positive_int(movie_file.get("id")),
        radarr_added_at=_timestamp(data.get("added")),
    )


_RADARR_FIELDS = tuple(RadarrMovie.__dataclass_fields__)
_TMDB_FIELDS = ("director", "production_companies", "tagline", "tmdb_enriched_at")


def apply_radarr_movie(movie: Movie, record: RadarrMovie, now: datetime) -> bool:
    """Copy Radarr's fields onto ``movie``; return whether anything but ``last_seen_at`` changed.

    A changed TMDB id clears the TMDB details, so the next enrichment fetches them again.
    """
    changed = False
    if movie.tmdb_id != record.tmdb_id and any(
        getattr(movie, name) is not None for name in _TMDB_FIELDS
    ):
        for name in _TMDB_FIELDS:
            setattr(movie, name, None)
        changed = True
    for name in _RADARR_FIELDS:
        value = getattr(record, name)
        if getattr(movie, name) != value:
            setattr(movie, name, value)
            changed = True
    if movie.is_present is not True or movie.retired_at is not None:
        movie.is_present = True
        movie.retired_at = None
        changed = True
    if changed:
        movie.last_seen_at = now
    return changed


# ── Root folders ──────────────────────────────────────────────────────────


def check_root_folders(root_folders: Any, mapper: PathMapper) -> int:
    """Every root folder Radarr reports must be a folder here. Returns how many were checked.

    Blocking (it stats folders on what is usually a network share): call it off the loop.
    """
    if not isinstance(root_folders, list) or not root_folders:
        raise SyncPathError("Radarr reports no root folders")
    for entry in root_folders:
        remote = entry.get("path") if isinstance(entry, dict) else None
        if not isinstance(remote, str) or not remote:
            raise SyncPathError("Radarr reported a root folder with no path")
        try:
            local = mapper.to_local(remote)
        except PathMappingError as exc:
            raise SyncPathError(
                f"Radarr's root folder {remote} is not under any movie root ({exc}). Set "
                "RADARR_MOVIE_ROOTS to the folders in MOVIE_ROOTS as Radarr sees them"
            ) from exc
        if not local.is_dir():
            raise SyncPathError(
                f"Radarr's root folder {remote} should be {local} here, which is not a folder "
                "(is the share mounted?). Check MOVIE_ROOTS and RADARR_MOVIE_ROOTS"
            )
    return len(root_folders)


# ── Posters on disk ───────────────────────────────────────────────────────


@dataclass(frozen=True)
class PosterStat:
    size_bytes: int
    modified_at: datetime


def _modified_at(st: os.stat_result) -> datetime:
    """The modification time to the microsecond, exactly as PostgreSQL stores it."""
    micros = st.st_mtime_ns // 1000
    return datetime.fromtimestamp(micros // 1_000_000, UTC).replace(microsecond=micros % 1_000_000)


def stat_poster(path: Path) -> PosterStat | None:
    """Size and modification time of a regular file; ``None`` when absent or not a file."""
    try:
        st = os.stat(path, follow_symlinks=False)
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(st.st_mode):
        logger.warning("SYNC | %s is not a regular file; ignored", path)
        return None
    return PosterStat(size_bytes=st.st_size, modified_at=_modified_at(st))


def hash_poster(boundary: FilesystemBoundary, path: Path) -> tuple[str, PosterStat]:
    """SHA-256 of a poster, read through the read-only boundary (no symbolic links)."""
    classified = boundary.classify(path, require_file=True)
    fd = boundary.open_read(classified)
    with os.fdopen(fd, "rb") as handle:
        st = os.fstat(handle.fileno())
        digest = hashlib.sha256()
        while chunk := handle.read(_HASH_CHUNK):
            digest.update(chunk)
    return digest.hexdigest(), PosterStat(size_bytes=st.st_size, modified_at=_modified_at(st))


class PosterAction(StrEnum):
    LEAVE = "leave"
    """An origin the sync does not know, or nothing there and no row."""
    DELETE = "delete"
    KEEP = "keep"
    """Unchanged since it was hashed: only its last-seen time moves."""
    HASH = "hash"


def poster_action(row: Artwork | None, poster: PosterStat | None) -> PosterAction:
    """What the sync does with a folder's poster, given its row and the file's stat.

    A row of origin ``library`` or ``marquee`` is kept, hashed again, or deleted the same way;
    what a new hash means for a ``marquee`` row is decided after hashing.
    """
    if row is not None and row.origin not in ORIGINS:
        return PosterAction.LEAVE
    if poster is None:
        return PosterAction.LEAVE if row is None else PosterAction.DELETE
    if (
        row is not None
        and row.file_name == POSTER_NAME
        and row.size_bytes == poster.size_bytes
        and row.file_modified_at == poster.modified_at
    ):
        return PosterAction.KEEP
    return PosterAction.HASH


# ── The sync ──────────────────────────────────────────────────────────────


def _label(movie: Movie) -> str:
    return f"{movie.title} ({movie.year})" if movie.year else movie.title


def _local_folder(mapper: PathMapper, radarr_path: str) -> Path | None:
    try:
        return mapper.to_local(radarr_path)
    except PathMappingError:
        return None


async def _upsert_movies(
    session: AsyncSession,
    raw_movies: list[Any],
    mapper: PathMapper,
    now: datetime,
    report: SyncReport,
) -> dict[int, Movie]:
    existing = {m.radarr_id: m for m in (await session.scalars(select(Movie))).all()}
    listed: dict[int, Movie] = {}
    unchanged: list[int] = []
    for data in raw_movies:
        radarr_id = data.get("id") if isinstance(data, dict) else None
        try:
            record = parse_radarr_movie(data)
            if record.radarr_id in listed:
                raise MovieDataError("listed twice")
            try:
                mapper.to_local(record.radarr_path)
            except UnsafePathError as exc:
                raise MovieDataError(str(exc)) from exc
            except PathMappingError:
                logger.warning("SYNC | radarr_id=%s: folder is under no movie root", radarr_id)
        except MovieDataError as exc:
            report.failed += 1
            report.failed_radarr_ids.append(radarr_id if isinstance(radarr_id, int) else None)
            logger.warning("SYNC | movie radarr_id=%s not stored: %s", radarr_id, exc)
            continue
        # Everything that can fail for one movie was checked above; what the database
        # could still refuse (a lost connection) fails the whole sync, which is right.
        movie = existing.get(record.radarr_id)
        if movie is None:
            movie = Movie(radarr_id=record.radarr_id, is_present=True)
            apply_radarr_movie(movie, record, now)
            movie.last_seen_at = now
            session.add(movie)
            report.created += 1
        elif apply_radarr_movie(movie, record, now):
            report.updated += 1
        else:
            report.unchanged += 1
            unchanged.append(movie.id)
        listed[movie.radarr_id] = movie
    if unchanged:
        # Only the last-seen time moves; updated_at keeps meaning "something changed".
        await session.execute(
            update(Movie)
            .where(Movie.id.in_(unchanged))
            .values(last_seen_at=now, updated_at=Movie.updated_at)
            .execution_options(synchronize_session=False)
        )
    if report.failed == 0:
        for radarr_id, movie in existing.items():
            if radarr_id not in listed and movie.is_present:
                movie.is_present = False
                movie.retired_at = now
                report.retired += 1
    else:
        logger.warning("SYNC | %d movie(s) failed; nothing is retired this time", report.failed)
    report.with_file = sum(1 for movie in listed.values() if movie.has_file)
    await session.flush()
    return listed


async def _sync_posters(
    session: AsyncSession,
    listed: dict[int, Movie],
    folders: Sequence[MovieFolder],
    mapper: PathMapper,
    boundary: FilesystemBoundary,
    now: datetime,
    report: SyncReport,
    cancel: CancellationSignal | None,
) -> None:
    by_path = {folder.path: folder for folder in folders}
    rows = {
        row.movie_id: row
        for row in (
            await session.scalars(select(Artwork).where(Artwork.artwork_kind == ARTWORK_POSTER))
        ).all()
    }
    matched: set[Path] = set()
    kept: list[int] = []
    for movie in sorted(listed.values(), key=lambda m: m.radarr_id):
        raise_if_cancelled(cancel, _CANCELLED)
        local = _local_folder(mapper, movie.radarr_path)
        folder = by_path.get(local) if local is not None else None
        if folder is None:
            if movie.has_file:
                report.movies_without_folder.append(_label(movie))
            continue
        matched.add(folder.path)
        row = rows.get(movie.id)
        try:
            poster = await asyncio.to_thread(stat_poster, folder.poster) if folder.poster else None
            action = poster_action(row, poster)
            if action is PosterAction.HASH:
                digest, hashed = await asyncio.to_thread(hash_poster, boundary, folder.poster)
        except (OSError, FilesystemBoundaryError) as exc:
            report.posters_unreadable += 1
            logger.warning("SYNC | poster of %s unreadable: %s", folder.path, exc)
            continue
        if poster is not None and action is not PosterAction.LEAVE:
            report.posters_found += 1
        if action is PosterAction.DELETE:
            await session.delete(row)
            report.posters_removed += 1
        elif action is PosterAction.KEEP:
            kept.append(row.id)  # type: ignore[union-attr]
        elif action is PosterAction.HASH:
            report.posters_hashed += 1
            if row is None:
                row = Artwork(
                    subject_kind=SUBJECT_MOVIE,
                    movie_id=movie.id,
                    artwork_kind=ARTWORK_POSTER,
                    origin=ORIGIN_LIBRARY,
                    first_seen_at=now,
                )
                session.add(row)
            elif row.origin == ORIGIN_MARQUEE and row.sha256 != digest:
                # Not the bytes Marquee wrote: the owner put a poster there by hand.
                row.origin = ORIGIN_LIBRARY
                report.posters_replaced_by_hand += 1
                logger.info("SYNC | poster of %s was replaced by hand", _label(movie))
            row.file_name = POSTER_NAME
            row.sha256 = digest
            row.size_bytes = hashed.size_bytes
            row.file_modified_at = hashed.modified_at
            row.last_seen_at = now
    if kept:
        await session.execute(
            update(Artwork)
            .where(Artwork.id.in_(kept))
            .values(last_seen_at=now, updated_at=Artwork.updated_at)
            .execution_options(synchronize_session=False)
        )
    report.folders_without_movie = sorted(
        f"{folder.quality}/{folder.name}" for folder in folders if folder.path not in matched
    )
    await session.flush()


async def _enrich_from_tmdb(
    tmdb: MovieDetailsSource, listed: dict[int, Movie], now: datetime, report: SyncReport
) -> None:
    todo = [m for m in listed.values() if m.has_file and m.tmdb_id and m.tmdb_enriched_at is None]
    limit = asyncio.Semaphore(TMDB_CONCURRENCY)

    async def fetch(movie: Movie) -> tuple[Movie, Any]:
        async with limit:
            try:
                return movie, await tmdb.get_movie_details(movie.tmdb_id)  # type: ignore[arg-type]
            except Exception as exc:  # noqa: BLE001 - one movie's details never fail a sync
                logger.warning(
                    "SYNC | TMDB details failed for radarr_id=%s tmdb_id=%s: %s",
                    movie.radarr_id,
                    movie.tmdb_id,
                    type(exc).__name__,
                )
                return movie, None

    for movie, details in await asyncio.gather(*(fetch(movie) for movie in todo)):
        if details is None:
            report.tmdb_failed += 1
            continue
        movie.director = details.director
        movie.production_companies = list(details.production_companies)
        movie.tagline = details.tagline
        movie.tmdb_enriched_at = now
        report.tmdb_enriched += 1


async def _from_radarr(request: Awaitable[Any]) -> Any:
    """Await a request to Radarr; Radarr not answering becomes :class:`SyncUnavailableError`."""
    try:
        return await request
    except ArrConnectionError as exc:
        raise SyncUnavailableError(f"Radarr did not answer: {exc}") from exc


async def sync_library(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    radarr: RadarrSource,
    mapper: PathMapper,
    movie_roots: Sequence[Path],
    tmdb: MovieDetailsSource | None = None,
    dry_run: bool = False,
    cancel: CancellationSignal | None = None,
) -> SyncReport:
    """Run one sync and return its report. See the module docstring for the steps."""
    started = time.perf_counter()
    report = SyncReport(dry_run=dry_run, tmdb_skipped=tmdb is None)
    now = datetime.now(UTC)

    root_folders = await _from_radarr(radarr.get_root_folders())
    try:
        report.root_folders_checked = await asyncio.wait_for(
            asyncio.to_thread(check_root_folders, root_folders, mapper), ROOT_FOLDER_TIMEOUT_S
        )
    except TimeoutError as exc:
        raise SyncUnavailableError(
            f"Radarr's root folders did not answer within {ROOT_FOLDER_TIMEOUT_S:.0f} s "
            "(is the share mounted?)"
        ) from exc
    raise_if_cancelled(cancel, _CANCELLED)

    raw_movies = await _from_radarr(radarr.get_movies())
    if not isinstance(raw_movies, list):
        raise SyncError("Radarr's movie list is not a list")
    report.movies_listed = len(raw_movies)
    try:
        folders = await asyncio.to_thread(scan_movie_roots, movie_roots)
    except LibraryRootError as exc:
        raise SyncPathError(str(exc)) from exc
    boundary = boundary_for_roots(
        {f"movies{i}": Path(root) for i, root in enumerate(movie_roots)}, access="read"
    )
    raise_if_cancelled(cancel, _CANCELLED)

    async with session_factory() as session:
        locked = await session.scalar(select(func.pg_try_advisory_xact_lock(SYNC_LOCK_KEY)))
        if not locked:
            raise SyncAlreadyRunningError("another sync is running")
        listed = await _upsert_movies(session, raw_movies, mapper, now, report)
        raise_if_cancelled(cancel, _CANCELLED)
        await _sync_posters(session, listed, folders, mapper, boundary, now, report, cancel)
        raise_if_cancelled(cancel, _CANCELLED)
        if tmdb is not None:
            await _enrich_from_tmdb(tmdb, listed, now, report)
        raise_if_cancelled(cancel, _CANCELLED)
        if dry_run:
            await session.rollback()
        else:
            await session.commit()

    report.seconds = round(time.perf_counter() - started, 2)
    logger.info(
        "SYNC | %s: %d listed, %d created, %d updated, %d unchanged, %d retired, %d failed; "
        "posters %d found, %d hashed, %d removed",
        "dry run" if dry_run else "done",
        report.movies_listed,
        report.created,
        report.updated,
        report.unchanged,
        report.retired,
        report.failed,
        report.posters_found,
        report.posters_hashed,
        report.posters_removed,
    )
    return report


async def sync_from_settings(
    *, dry_run: bool = False, cancel: CancellationSignal | None = None
) -> SyncReport:
    """One sync with the configured database, Radarr, TMDB, and movie roots."""
    from marquee.config import settings
    from marquee.db.engine import create_engine, create_session_factory
    from marquee.integrations.arr import RadarrClient
    from marquee.integrations.tmdb.client import TMDBClient

    if not settings.RADARR_URL or settings.RADARR_API_KEY is None:
        raise SyncError("RADARR_URL and RADARR_API_KEY must be set")
    try:
        mapper = PathMapper.from_settings(settings)
    except PathMappingError as exc:
        raise SyncPathError(str(exc)) from exc

    token = settings.TMDB_READ_ACCESS_TOKEN
    radarr = RadarrClient(settings.RADARR_URL, settings.RADARR_API_KEY.get_secret_value())
    tmdb = TMDBClient(read_access_token=token.get_secret_value()) if token else None
    engine = create_engine()
    try:
        await radarr.connect()
        if tmdb is not None:
            await tmdb.connect()
        return await sync_library(
            session_factory=create_session_factory(engine),
            radarr=radarr,
            mapper=mapper,
            movie_roots=settings.MOVIE_ROOTS,
            tmdb=tmdb,
            dry_run=dry_run,
            cancel=cancel,
        )
    finally:
        await radarr.disconnect()
        if tmdb is not None:
            await tmdb.disconnect()
        await engine.dispose()
