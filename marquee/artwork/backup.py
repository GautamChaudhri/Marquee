"""Content-addressed backups of posters a write replaced: ``posters/<movie id>/<sha256>.jpg``.

The backups directory is ``DATA_DIR/backups``; a backup's name relative to it is what the
``deployments`` table stores, never a path. A backup is named by the SHA-256 of its bytes,
so storing the same poster twice keeps one file, and reading one back can prove it is the
poster that was there. Every function here is blocking: call it off the event loop.
"""

from __future__ import annotations

import hashlib
import os
import re
import stat
from pathlib import Path

from marquee.config import settings
from marquee.fs.boundary import FilesystemBoundary, FilesystemBoundaryError, boundary_for_roots

ROOT = "backups"
POSTERS = "posters"
_SHA256 = re.compile(r"[0-9a-f]{64}")
_KEY = re.compile(r"posters/[1-9][0-9]*/[0-9a-f]{64}\.jpg")


class BackupError(Exception):
    """A backup is missing, cannot be written, or does not hold the bytes its name says."""


def sha256_of(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def backup_key(movie_id: int, sha256: str) -> str:
    """The backup's name relative to the backups directory."""
    if movie_id < 1 or not _SHA256.fullmatch(sha256):
        raise BackupError(f"no backup name for movie {movie_id} and hash {sha256!r}")
    return f"{POSTERS}/{movie_id}/{sha256}.jpg"


def _boundary(backups_dir: Path) -> FilesystemBoundary:
    backups_dir.mkdir(parents=True, exist_ok=True)
    return boundary_for_roots({ROOT: backups_dir}, access="read_write", purpose="backups")


def _read(boundary: FilesystemBoundary, base: Path, key: str) -> bytes | None:
    """The bytes stored under ``key``, or ``None`` when there is nothing there.

    The boundary resolves a symbolic link that stays inside the backups directory, so the
    file itself is checked first: a backup is a regular file, never a link.
    """
    try:
        if not stat.S_ISREG(os.lstat(base / key).st_mode):
            raise BackupError(f"backup {key} is not a regular file")
    except FileNotFoundError:
        return None
    try:
        fd = boundary.open_read(boundary.from_key(ROOT, key))
    except FilesystemBoundaryError as exc:
        if isinstance(exc.__cause__, FileNotFoundError) or "missing" in str(exc):
            return None
        raise BackupError(f"backup {key} cannot be opened: {exc}") from exc
    with os.fdopen(fd, "rb") as handle:
        return handle.read()


def _exists(backups_dir: Path, key: str) -> bool:
    return (backups_dir / key).exists() or (backups_dir / key).is_symlink()


def store(data: bytes, movie_id: int, backups_dir: Path | None = None) -> str:
    """Keep ``data`` as a backup of a poster of ``movie_id``; returns the backup's name.

    The file is written under a temporary name, flushed and synced, renamed into place, then
    read back and its hash checked. A backup already there with the right hash is left alone;
    one with the wrong hash is an error, never overwritten.
    """
    base = backups_dir or settings.backups_dir
    digest = sha256_of(data)
    key = backup_key(movie_id, digest)
    boundary = _boundary(base)
    if _exists(base, key):
        verify(key, digest, base)
        return key
    folder = boundary.create_directory(
        boundary.from_key(ROOT, f"{POSTERS}/{movie_id}"), mode=0o700, parents=True
    )
    staged, fd = boundary.temporary_file(folder, prefix=".backup-")
    try:
        try:
            view = memoryview(data)
            while view:
                view = view[os.write(fd, view) :]
            os.fsync(fd)
        finally:
            os.close(fd)
        boundary.atomic_replace(staged, boundary.from_key(ROOT, key))
    except BaseException:
        boundary.delete_file(staged, missing_ok=True)
        raise
    verify(key, digest, base)
    return key


def verify(key: str, sha256: str, backups_dir: Path | None = None) -> bytes:
    """Read a backup and prove it holds the poster whose hash is ``sha256``; return its bytes."""
    base = backups_dir or settings.backups_dir
    if not _KEY.fullmatch(key):
        raise BackupError(f"{key!r} is not a backup's name")
    data = _read(_boundary(base), base, key)
    if data is None:
        raise BackupError(f"backup {key} is missing")
    actual = sha256_of(data)
    if actual != sha256 or not key.endswith(f"/{sha256}.jpg"):
        raise BackupError(f"backup {key} holds bytes with hash {actual}, not {sha256}")
    return data


def read_only(key: str, backups_dir: Path | None = None) -> bytes | None:
    """The bytes of the backup ``key``, or ``None`` when it is not there. Unlike
    :func:`verify` it creates nothing, not even the backups directory."""
    base = backups_dir or settings.backups_dir
    if not _KEY.fullmatch(key):
        raise BackupError(f"{key!r} is not a backup's name")
    if not base.is_dir():
        return None
    boundary = boundary_for_roots({ROOT: base}, access="read", purpose="backups")
    return _read(boundary, base, key)


def exists(movie_id: int, sha256: str, backups_dir: Path | None = None) -> bool:
    """Whether a verified backup of this poster is kept."""
    try:
        verify(backup_key(movie_id, sha256), sha256, backups_dir)
    except BackupError:
        return False
    return True


def summary(backups_dir: Path | None = None) -> tuple[int, int]:
    """How many poster backups are kept, and their total size in bytes."""
    base = (backups_dir or settings.backups_dir) / POSTERS
    count = size = 0
    if base.is_dir():
        for path in base.rglob("*.jpg"):
            if path.is_file() and not path.is_symlink():
                count += 1
                size += path.stat().st_size
    return count, size
