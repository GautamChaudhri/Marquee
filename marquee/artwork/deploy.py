"""The one path that writes a poster into a movie folder, for a deploy and for a restore.

Every write follows the same steps (house rules, "Writing media files"):

1. The folder comes from :class:`marquee.fs.paths.PathMapper` every time; it must exist and
   is never created. The target, ``poster.jpg`` in it, is confined to the movie roots through
   :mod:`marquee.fs.boundary` (no symbolic links).
2. The new bytes arrive validated: a JPEG fetched from TMDB for a deploy
   (:func:`fetch_original`), a verified backup for a restore, or nothing for a restore of "no
   poster".
3. If the target already holds exactly the new bytes, nothing is written.
4. Whatever is in the way is backed up (:mod:`marquee.artwork.backup`) and the backup
   verified.
5. What the write is about to do (the new hash and the previous poster's hash, origin, and
   backup) is kept beside the staged original (:func:`write_intent`). Then the new bytes go
   to a temporary file in the same folder, flushed and synced, and are renamed over the
   target; a failure removes the temporary file.
6. The target is read back and its hash compared; if it differs the backup is put back and
   the write fails loudly.
7. One transaction updates the ``artwork`` row and inserts the ``deployments`` row.

Every step is safe to repeat. A job that already wrote its ``deployments`` row is done; a
file already in place (an earlier attempt renamed it and died before step 7) is recorded
without writing again, with the previous poster its intent names: a sync may have rewritten
the ``artwork`` row in between.
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import json
import logging
import os
import stat
import threading
import warnings
from collections.abc import Callable, Iterator
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

import httpx
from PIL import Image, ImageFile
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from marquee.artwork import backup
from marquee.config import settings
from marquee.db.models import (
    ARTWORK_POSTER,
    ORIGIN_LIBRARY,
    SUBJECT_MOVIE,
    Artwork,
    Deployment,
    Movie,
)
from marquee.fs.boundary import (
    ClassifiedPath,
    FilesystemBoundary,
    FilesystemBoundaryError,
    boundary_for_roots,
)
from marquee.fs.download_guard import ensure_image_response
from marquee.fs.paths import PathMapper
from marquee.integrations.tmdb.client import IMAGE_BASE_URL
from marquee.library.folders import POSTER_NAME
from marquee.library.sync import SYNC_LOCK_KEY, stat_poster

logger = logging.getLogger(__name__)

MIN_SIDE = 500
MAX_SIDE = 10_000
NEW_FILE_MODE = 0o644
FETCH_TIMEOUT_S = 60.0
_DECODE_LOCK = threading.Lock()


class DeployError(Exception):
    """The write cannot go ahead; nothing was changed. The message says why."""


class NotAJpegError(DeployError):
    """What TMDB sent is not a JPEG of a usable size; retrying will not change that."""


class VerificationError(DeployError):
    """The written file did not hold the bytes written; the previous poster was put back."""


class RefusedTargetError(DeployError):
    """The folder or its ``poster.jpg`` is not something Marquee writes to (missing, a
    symbolic link, not a regular file, outside the roots); retrying will not change that."""


# ── The new bytes of a deploy ─────────────────────────────────────────────


def original_url(source_file: str) -> str:
    """TMDB's original-size URL for a candidate, always asking for a ``.jpg``."""
    name = PurePosixPath(source_file).name
    if not name or name != source_file:
        raise DeployError(f"{source_file!r} is not a TMDB file name")
    if PurePosixPath(name).suffix.lower() != ".jpg":
        name = f"{PurePosixPath(name).stem}.jpg"
    return f"{IMAGE_BASE_URL}/original/{name}"


@contextlib.contextmanager
def strict_decoding() -> Iterator[None]:
    """Make Pillow refuse truncated data while decoding here.

    The engine sets ``ImageFile.LOAD_TRUNCATED_IMAGES`` to true for the whole process when it
    is imported (its stages tolerate a cut-off download); a deploy must never accept one.
    The flag is process-wide, so it is set under a lock and put back.
    """
    with _DECODE_LOCK:
        previous = ImageFile.LOAD_TRUNCATED_IMAGES
        ImageFile.LOAD_TRUNCATED_IMAGES = False
        try:
            yield
        finally:
            ImageFile.LOAD_TRUNCATED_IMAGES = previous


def validate_jpeg(data: bytes) -> tuple[int, int]:
    """Decode ``data`` fully and return its size; raise :class:`NotAJpegError` unless it is a
    JPEG between :data:`MIN_SIDE` and :data:`MAX_SIDE` pixels on each side. Blocking."""
    with strict_decoding():
        return _validate_jpeg(data)


def _validate_jpeg(data: bytes) -> tuple[int, int]:
    try:
        with Image.open(io.BytesIO(data)) as probe:
            probe.verify()
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as image:
                if image.format != "JPEG":
                    raise NotAJpegError(
                        f"TMDB sent a {image.format or 'unknown'} image, not a JPEG"
                    )
                width, height = image.size
                if min(width, height) < MIN_SIDE or max(width, height) > MAX_SIDE:
                    raise NotAJpegError(
                        f"TMDB sent a {width}x{height} image; a poster must be {MIN_SIDE} to "
                        f"{MAX_SIDE} px on each side"
                    )
                image.load()
    except NotAJpegError:
        raise
    except (OSError, ValueError, SyntaxError, Image.DecompressionBombError) as exc:
        raise NotAJpegError(f"TMDB sent bytes that do not decode as an image: {exc}") from exc
    return width, height


async def fetch_original(source_file: str, client: httpx.AsyncClient | None = None) -> bytes:
    """The candidate's original-size JPEG from TMDB, decoded and checked off the event loop.

    A network failure is left to the job's retry rule; bytes that are not a usable JPEG raise
    :class:`NotAJpegError`. No image is ever converted.
    """
    url = original_url(source_file)
    own = client is None
    client = client or httpx.AsyncClient(timeout=FETCH_TIMEOUT_S)
    try:
        response = await client.get(url)
        response.raise_for_status()
    finally:
        if own:
            await client.aclose()
    try:
        ensure_image_response(response)
    except ValueError as exc:
        raise NotAJpegError(f"TMDB's answer for {source_file} is not a poster: {exc}") from exc
    await asyncio.to_thread(validate_jpeg, response.content)
    return response.content


# ── The target ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Target:
    boundary: FilesystemBoundary
    folder: ClassifiedPath
    poster: ClassifiedPath
    poster_path: Path


@dataclass(frozen=True)
class OnDisk:
    data: bytes
    sha256: str
    mode: int


def locate(radarr_path: str, title: str) -> Target:
    """Step 1: the movie's folder, which must exist, and its confined ``poster.jpg``."""
    folder = PathMapper.from_settings().to_local(radarr_path)
    if folder.is_symlink() or not folder.is_dir():
        raise RefusedTargetError(
            f"the folder of {title} is missing or not a plain folder; Marquee never creates one"
        )
    poster_path = folder / POSTER_NAME
    if poster_path.is_symlink():
        raise RefusedTargetError(f"{POSTER_NAME} of {title} is a symbolic link; it is left alone")
    boundary = boundary_for_roots(
        {f"movies{i}": Path(root) for i, root in enumerate(settings.MOVIE_ROOTS)},
        access="read_write",
        purpose="movie library",
    )
    try:
        return Target(
            boundary=boundary,
            folder=boundary.classify(folder, write=True),
            poster=boundary.classify(poster_path, require_exists=False, write=True),
            poster_path=poster_path,
        )
    except FilesystemBoundaryError as exc:
        raise RefusedTargetError(
            f"the folder of {title} is not safely inside a movie root: {exc}"
        ) from exc


def read_current(target: Target) -> OnDisk | None:
    """What ``poster.jpg`` holds now, or ``None`` when there is none. Blocking."""
    try:
        st = os.lstat(target.poster_path)
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(st.st_mode):
        raise RefusedTargetError(f"{POSTER_NAME} is not a regular file; it is left alone")
    fd = target.boundary.open_read(target.poster)
    with os.fdopen(fd, "rb") as handle:
        data = handle.read()
    return OnDisk(data=data, sha256=backup.sha256_of(data), mode=stat.S_IMODE(st.st_mode))


def write_file(target: Target, data: bytes, mode: int) -> None:
    """Step 5: a temporary file in the same folder, synced, then renamed over the target."""
    staged, fd = target.boundary.temporary_file(target.folder, prefix=".marquee-poster-")
    try:
        try:
            try:
                os.fchmod(fd, mode)
            except OSError as exc:  # a share may fix modes by its mount options
                logger.warning("DEPLOY | could not set mode %o: %s", mode, exc)
            view = memoryview(data)
            while view:
                view = view[os.write(fd, view) :]
            os.fsync(fd)
        finally:
            os.close(fd)
        target.boundary.atomic_replace(staged, target.poster)
    except BaseException:
        with contextlib.suppress(Exception):
            target.boundary.delete_file(staged, missing_ok=True)
        raise


def remove_file(target: Target) -> None:
    target.boundary.delete_file(target.poster, missing_ok=True)
    target.boundary.fsync_parent(target.poster)


# ── What a write is about to do ───────────────────────────────────────────

INTENT_FORMAT = 1


@dataclass(frozen=True)
class Intent:
    """What a job's write is about to do, kept until the write is recorded.

    A later attempt of the same job that finds the new bytes already in place takes the
    previous poster from here, not from the ``artwork`` row, which a sync may have rewritten
    between the rename and the record.
    """

    job_id: int
    movie_id: int
    new_sha256: str | None
    previous_sha256: str | None
    previous_origin: str | None
    backup_file: str | None


def intent_path(job_id: int) -> Path:
    return settings.deploy_staging_dir / f"{job_id}.json"


def write_intent(intent: Intent) -> None:
    """Keep ``intent`` under a temporary name, synced, then renamed into place. Blocking."""
    folder = settings.deploy_staging_dir
    folder.mkdir(parents=True, exist_ok=True)
    temporary = folder / f".{intent.job_id}.json.part"
    data = json.dumps({"format": INTENT_FORMAT, **asdict(intent)}, sort_keys=True).encode()
    with temporary.open("wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, intent_path(intent.job_id))


def read_intent(job_id: int) -> Intent | None:
    """The intent a job kept, or ``None`` when there is none or it cannot be read. Blocking."""
    try:
        value = json.loads(intent_path(job_id).read_text(encoding="utf-8"))
        if value.pop("format") != INTENT_FORMAT:
            raise ValueError("unknown format")
        intent = Intent(**value)
    except FileNotFoundError:
        return None
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        logger.warning("DEPLOY | the intent of job %d cannot be read: %s", job_id, exc)
        return None
    return intent if intent.job_id == job_id else None


def remove_intent(job_id: int | None) -> None:
    if job_id is not None:
        with contextlib.suppress(OSError):
            intent_path(job_id).unlink(missing_ok=True)


# ── The write ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Write:
    """One write to a movie's ``poster.jpg`` and how to record it."""

    movie: Movie
    new: bytes | None
    """The new poster, or ``None`` to remove it (a restore of "no poster")."""
    new_origin: str
    action: str
    initiated_by: str
    job_id: int | None
    run_id: int | None = None
    candidate_id: int | None = None
    source_file: str | None = None
    restores_deployment_id: int | None = None


@dataclass(frozen=True)
class WriteResult:
    outcome: str
    """``written``, ``removed``, ``already_in_place`` (recorded without writing),
    ``already_deployed`` or ``already_recorded`` (nothing added), or ``skipped``."""
    deployment_id: int | None = None
    new_sha256: str | None = None
    previous_sha256: str | None = None
    previous_origin: str | None = None
    backup_file: str | None = None
    note: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


Guard = Callable[[OnDisk | None, Artwork | None, Deployment | None], str | None]
"""Checked before an automatic write, with what is on disk, the poster row, and the movie's
newest deployment: returns why to skip it, or ``None`` to go ahead."""


async def _rows(
    session: AsyncSession, movie_id: int, job_id: int | None
) -> tuple[Deployment | None, Artwork | None, Deployment | None]:
    """The job's own deployments row, the poster row, and the movie's newest deployment."""
    own = (
        await session.scalar(select(Deployment).where(Deployment.job_id == job_id))
        if job_id is not None
        else None
    )
    row = await session.scalar(
        select(Artwork).where(Artwork.movie_id == movie_id, Artwork.artwork_kind == ARTWORK_POSTER)
    )
    newest = await newest_deployment(session, movie_id)
    await session.commit()
    return own, row, newest


async def newest_deployment(session: AsyncSession, movie_id: int) -> Deployment | None:
    return await session.scalar(
        select(Deployment)
        .where(Deployment.movie_id == movie_id)
        .order_by(Deployment.id.desc())
        .limit(1)
    )


async def already_deployed(
    sessions: async_sessionmaker[AsyncSession], movie: Movie, candidate_id: int
) -> Deployment | None:
    """The movie's newest deployment, if it deployed this candidate and the folder and the
    poster row still hold exactly what it wrote. A deploy asked for again then writes
    nothing, without fetching TMDB's file again (which can arrive as other bytes)."""
    async with sessions() as session:
        _, row, newest = await _rows(session, movie.id, None)
    if (
        newest is None
        or newest.action != "deploy"
        or newest.candidate_id != candidate_id
        or row is None
        or row.sha256 != newest.new_sha256
    ):
        return None
    target = await asyncio.to_thread(locate, movie.radarr_path, movie.title)
    current = await asyncio.to_thread(read_current, target)
    return newest if current is not None and current.sha256 == newest.new_sha256 else None


def recorded(deployment: Deployment, outcome: str, note: str) -> WriteResult:
    return WriteResult(
        outcome=outcome,
        deployment_id=deployment.id,
        new_sha256=deployment.new_sha256,
        previous_sha256=deployment.previous_sha256,
        previous_origin=deployment.previous_origin,
        backup_file=deployment.backup_file,
        note=note,
    )


async def write_poster(
    sessions: async_sessionmaker[AsyncSession], write: Write, *, guard: Guard | None = None
) -> WriteResult:
    """Put ``write.new`` in the movie's folder as ``poster.jpg`` (or remove it), and record it."""
    movie = write.movie
    async with sessions() as session:
        own, row, newest = await _rows(session, movie.id, write.job_id)
    if own is not None:
        await asyncio.to_thread(remove_intent, write.job_id)
        return recorded(own, "already_recorded", "this job already made its write")
    target = await asyncio.to_thread(locate, movie.radarr_path, movie.title)
    current = await asyncio.to_thread(read_current, target)
    new_sha = backup.sha256_of(write.new) if write.new is not None else None
    current_sha = current.sha256 if current is not None else None
    backup_file: str | None = None
    previous_sha: str | None = None
    previous_origin: str | None = None
    new_origin = write.new_origin
    note: str | None = None

    if current_sha == new_sha:  # step 3: the target already holds the new bytes
        row_sha = row.sha256 if row is not None else None
        if (
            newest is not None
            and newest.new_sha256 == new_sha
            and row_sha == new_sha
            and newest.action == write.action
            and newest.restores_deployment_id == write.restores_deployment_id
        ):
            await asyncio.to_thread(remove_intent, write.job_id)
            return recorded(newest, "already_deployed", "the poster was already deployed")
        intent = (
            await asyncio.to_thread(read_intent, write.job_id) if write.job_id is not None else None
        )
        if intent is not None and (intent.movie_id, intent.new_sha256) == (movie.id, new_sha):
            # An earlier attempt of this job renamed the new bytes into place and stopped
            # before recording it. Its intent names the poster it replaced; the row may not
            # (a sync in between records what it found: the new bytes).
            previous_sha, previous_origin = intent.previous_sha256, intent.previous_origin
            backup_file = intent.backup_file
            if previous_sha is not None and not await asyncio.to_thread(
                backup.exists, movie.id, previous_sha
            ):
                raise DeployError(
                    f"{POSTER_NAME} already holds the new bytes but the backup of the poster "
                    "it replaced is missing; nothing recorded"
                )
            note = "the file was already in place; an earlier attempt of this job wrote it"
        elif row is not None and row.sha256 != new_sha:
            previous_sha, previous_origin = row.sha256, row.origin
            backup_file = backup.backup_key(movie.id, row.sha256)
            if not await asyncio.to_thread(backup.exists, movie.id, row.sha256):
                raise DeployError(
                    f"{POSTER_NAME} already holds the new bytes but the backup of the poster "
                    "the database describes is missing; nothing recorded"
                )
            note = "the file was already in place; an earlier attempt wrote it"
        elif row is not None and current is not None:
            # The poster there is already these bytes and no deployment put it there (an
            # earlier attempt would have left the row describing the old poster): the owner's
            # own file is the candidate. Nothing is written, it stays whose it was, and it is
            # its own previous poster, backed up, so undoing this leaves it byte for byte.
            previous_sha, previous_origin = row.sha256, row.origin
            backup_file = await asyncio.to_thread(backup.store, current.data, movie.id)
            new_origin = row.origin
            note = (
                f"the folder already held exactly this poster ({row.origin}'s); nothing was "
                "written and it stays as it was"
            )
        else:
            note = "the file was already in place and no previous poster is recorded"
        outcome = "already_in_place"
    else:
        if guard is not None and (reason := guard(current, row, newest)) is not None:
            return WriteResult(outcome="skipped", note=reason)
        if current is not None:  # step 4
            backup_file = await asyncio.to_thread(backup.store, current.data, movie.id)
            previous_sha = current.sha256
            known = row is not None and row.sha256 == current.sha256
            previous_origin = row.origin if known and row is not None else ORIGIN_LIBRARY
        mode = current.mode if current is not None else NEW_FILE_MODE
        if write.job_id is not None:  # step 5
            await asyncio.to_thread(
                write_intent,
                Intent(
                    job_id=write.job_id,
                    movie_id=movie.id,
                    new_sha256=new_sha,
                    previous_sha256=previous_sha,
                    previous_origin=previous_origin,
                    backup_file=backup_file,
                ),
            )
        if write.new is not None:
            await asyncio.to_thread(write_file, target, write.new, mode)
        else:
            await asyncio.to_thread(remove_file, target)
        after = await asyncio.to_thread(read_current, target)  # step 6
        if (after.sha256 if after is not None else None) != new_sha:
            if current is not None:
                await asyncio.to_thread(write_file, target, current.data, mode)
            else:
                await asyncio.to_thread(remove_file, target)
            raise VerificationError(
                f"{POSTER_NAME} of {movie.title} did not read back as written; the previous "
                "poster was put back"
            )
        outcome = "written" if write.new is not None else "removed"

    async with sessions() as session:  # step 7
        deployment = await _record(
            session, write, target, new_sha, new_origin, previous_sha, previous_origin, backup_file
        )
    await asyncio.to_thread(remove_intent, write.job_id)
    logger.info(
        "DEPLOY | movie %d: %s %s (%s); deployment %d",
        movie.id,
        write.action,
        outcome,
        new_sha or "no poster",
        deployment.id,
    )
    return WriteResult(
        outcome=outcome,
        deployment_id=deployment.id,
        new_sha256=new_sha,
        previous_sha256=previous_sha,
        previous_origin=previous_origin,
        backup_file=backup_file,
        note=note,
    )


async def _record(
    session: AsyncSession,
    write: Write,
    target: Target,
    new_sha: str | None,
    new_origin: str,
    previous_sha: str | None,
    previous_origin: str | None,
    backup_file: str | None,
) -> Deployment:
    """Step 7, in one transaction. The sync's lock is taken first, so a sync in progress
    finishes before the poster row changes and cannot overwrite it with what it saw."""
    await session.execute(select(func.pg_advisory_xact_lock(SYNC_LOCK_KEY)))
    row = await session.scalar(
        select(Artwork)
        .where(Artwork.movie_id == write.movie.id, Artwork.artwork_kind == ARTWORK_POSTER)
        .with_for_update()
    )
    now = datetime.now(UTC)
    if new_sha is None:
        if row is not None:
            await session.delete(row)
    else:
        # The size and time the file has now, as the sync will compare them.
        on_disk = await asyncio.to_thread(stat_poster, target.poster_path)
        if on_disk is None:
            raise DeployError(
                f"{POSTER_NAME} of {write.movie.title} vanished before it was recorded"
            )
        if row is None:
            row = Artwork(
                subject_kind=SUBJECT_MOVIE,
                movie_id=write.movie.id,
                artwork_kind=ARTWORK_POSTER,
                first_seen_at=now,
            )
            session.add(row)
        row.file_name = POSTER_NAME
        row.sha256 = new_sha
        row.size_bytes = on_disk.size_bytes
        row.file_modified_at = on_disk.modified_at
        row.origin = new_origin
        row.last_seen_at = now
    deployment = Deployment(
        subject_kind=SUBJECT_MOVIE,
        movie_id=write.movie.id,
        artwork_kind=ARTWORK_POSTER,
        action=write.action,
        initiated_by=write.initiated_by,
        run_id=write.run_id,
        candidate_id=write.candidate_id,
        source_file=write.source_file,
        job_id=write.job_id,
        file_name=POSTER_NAME,
        new_sha256=new_sha,
        new_size_bytes=len(write.new) if write.new is not None else None,
        previous_sha256=previous_sha,
        previous_origin=previous_origin,
        backup_file=backup_file,
        restores_deployment_id=write.restores_deployment_id,
    )
    session.add(deployment)
    await session.commit()
    return deployment


@dataclass(frozen=True)
class DeploymentPage:
    total: int
    items: list[Deployment]


async def list_deployments(
    session: AsyncSession, *, movie_id: int | None = None, page: int = 1, page_size: int = 50
) -> DeploymentPage:
    """One page of deployments, newest first."""
    conditions = [Deployment.movie_id == movie_id] if movie_id is not None else []
    total = await session.scalar(select(func.count()).select_from(Deployment).where(*conditions))
    rows = await session.scalars(
        select(Deployment)
        .where(*conditions)
        .order_by(Deployment.id.desc())
        .limit(page_size)
        .offset((page - 1) * page_size)
    )
    return DeploymentPage(total=total or 0, items=list(rows))
