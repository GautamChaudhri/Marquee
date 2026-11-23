"""Shared media-file resolution boundary (design §19.4).

Media operations resolve a ``media_file_id`` to
a validated, stat'd ``ResolvedMediaFile`` here — the one place that translates a
source-namespace path, enforces the media-root guard, and computes the cheap
file signature used for stale-plan detection.

The signature is intentionally NOT a full-content hash (files are multi-GB). It
combines size + mtime + a small head/tail block hash, which is enough to detect
"the file changed under us between plan and execute".
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from marquee.core.path_utils import PathValidationError, safe_translate_and_validate
from marquee.models import MediaFile, Movie

logger = logging.getLogger(__name__)

_EDGE_BLOCK = 65536  # bytes hashed from the head and tail of the file


class MediaFileNotFoundError(Exception):
    """No MediaFile row for the given id."""


class MediaFileUnavailableError(Exception):
    """The MediaFile row exists but its path is invalid or missing on disk."""


@dataclass
class ResolvedMediaFile:
    media_file_id: int
    source: str
    path: Path
    size_bytes: int
    mtime_ns: int
    st_nlink: int
    signature: str
    container: str | None
    movie_id: int | None

    @property
    def is_hardlinked(self) -> bool:
        return self.st_nlink > 1


def compute_signature(path: Path, *, size: int | None = None, mtime_ns: int | None = None) -> str:
    """Cheap stale-detection signature: size + mtime + head/tail block hash."""
    stat = path.stat()
    size = stat.st_size if size is None else size
    mtime_ns = stat.st_mtime_ns if mtime_ns is None else mtime_ns
    digest = hashlib.sha1(usedforsecurity=False)
    digest.update(f"{size}:{mtime_ns}".encode())
    try:
        with path.open("rb") as handle:
            digest.update(handle.read(_EDGE_BLOCK))
            if size > _EDGE_BLOCK:
                handle.seek(max(0, size - _EDGE_BLOCK))
                digest.update(handle.read(_EDGE_BLOCK))
    except OSError:
        # Signature falls back to size+mtime only — still useful for detection.
        pass
    return digest.hexdigest()


async def resolve_media_file(db: AsyncSession, media_file_id: int) -> ResolvedMediaFile:
    """Load + translate + validate + stat a MediaFile row."""
    row = (
        await db.execute(select(MediaFile).where(MediaFile.id == media_file_id))
    ).scalar_one_or_none()
    if row is None:
        raise MediaFileNotFoundError(f"No media file id={media_file_id}")
    return await resolve_row(db, row)


async def resolve_row(db: AsyncSession, row: MediaFile) -> ResolvedMediaFile:
    """Resolve an already-loaded MediaFile row (path translate + validate + stat)."""
    try:
        path = safe_translate_and_validate(row.path, source=row.source)
    except PathValidationError as exc:
        raise MediaFileUnavailableError(f"path invalid: {exc}") from exc
    try:
        stat = path.stat()
    except OSError as exc:
        raise MediaFileUnavailableError(f"file not accessible: {exc}") from exc

    # NOTE: intentionally does NOT write `last_resolved_path` here. resolve_row
    # is on the hot read path (every GET /movies/{id}); mutating the row dirties
    # the session and triggers an autoflush UPDATE that contends with the encode
    # worker's write lock → "database is locked". The column is a non-critical
    # diagnostic, so we accept leaving it stale rather than writing on reads.

    return ResolvedMediaFile(
        media_file_id=row.id,
        source=row.source,
        path=path,
        size_bytes=stat.st_size,
        mtime_ns=stat.st_mtime_ns,
        st_nlink=stat.st_nlink,
        signature=compute_signature(path, size=stat.st_size, mtime_ns=stat.st_mtime_ns),
        container=row.container,
        movie_id=row.movie_id,
    )


def _movie_file_path(movie: Movie) -> str | None:
    """Source-namespace path of a movie's file from folder + relative path."""
    if not movie.folder_path or not movie.movie_file_path:
        return None
    return str(Path(movie.folder_path) / movie.movie_file_path)


async def ensure_media_file_for_movie(db: AsyncSession, movie: Movie) -> MediaFile | None:
    """Return the active MediaFile for a movie, creating it from sync data if absent.

    Lets media operations target a movie before a fresh sync has populated
    ``media_files`` (path-derived ``source_key`` until a native id replaces it).
    """
    existing = (
        await db.execute(
            select(MediaFile).where(MediaFile.movie_id == movie.id, MediaFile.is_active.is_(True))
        )
    ).scalar_one_or_none()

    path = _movie_file_path(movie)
    if existing is not None:
        if path and existing.path != path:
            existing.path = path
            existing.container = Path(path).suffix.lstrip(".").lower() or None
            await db.commit()
        return existing

    if path is None:
        return None

    media_file = MediaFile(
        source="radarr",
        source_key=f"radarr:movie:{movie.id}",
        movie_id=movie.id,
        path=path,
        relative_path=movie.movie_file_path,
        container=Path(path).suffix.lstrip(".").lower() or None,
        is_active=True,
    )
    db.add(media_file)
    await db.commit()
    await db.refresh(media_file)
    return media_file
