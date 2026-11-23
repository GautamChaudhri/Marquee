"""Serving-side thumbnail derivatives of deployed posters.

Deployed posters are stored at TMDB original resolution (commonly 0.5-3 MB)
while the library grids render them at a few hundred CSS pixels. One w400
JPEG derivative per poster version keeps first paints light; the fullscreen
viewer keeps requesting the original.

Invalidation is structural: the filename embeds the poster's version token,
so a redeploy misses the old derivative and builds a fresh one, deleting
stale siblings for the same subject as it goes. Posters without a version
token (sync-adopted, never hashed) are not thumbnailed — the caller serves
the original instead.
"""

from __future__ import annotations

import contextlib
import logging
import os
from io import BytesIO

from PIL import Image, UnidentifiedImageError

from marquee.core.filesystem import (
    ClassifiedPath,
    FilesystemBoundary,
    FilesystemBoundaryError,
    boundary_for_roots,
)
from marquee.core.runtime_settings import effective_settings as settings

# The only widths a route may serve: one derivative covers 2x DPR of the
# largest grid cell, and the whitelist keeps the endpoint from becoming a
# free-form resizing service.
THUMB_WIDTHS = frozenset({400})

_JPEG_QUALITY = 82

logger = logging.getLogger(__name__)


def thumbs_boundary() -> FilesystemBoundary:
    root = settings.poster_thumbs_path
    root.mkdir(parents=True, exist_ok=True)
    return boundary_for_roots({"thumbs": root}, access="read_write", purpose="poster-thumb-cache")


def _thumb_key(kind: str, subject_id: int, version: str, width: int) -> str:
    return f"{kind}/{subject_id}-{version}-w{width}.jpg"


def _render_thumbnail(source_bytes: bytes, width: int) -> bytes:
    with Image.open(BytesIO(source_bytes)) as image:
        # draft() lets the JPEG decoder skip DCT coefficients it will not
        # need — decoding a 2000px original at roughly the target scale.
        image.draft("RGB", (width, width * 3 // 2))
        image.thumbnail((width, width * 3), Image.Resampling.LANCZOS)
        rendered = image.convert("RGB")
        out = BytesIO()
        rendered.save(out, format="JPEG", quality=_JPEG_QUALITY, optimize=True)
        return out.getvalue()


def _drop_stale_siblings(
    boundary: FilesystemBoundary, kind: str, subject_id: int, keep_name: str
) -> None:
    kind_dir = settings.poster_thumbs_path / kind
    prefix = f"{subject_id}-"
    try:
        entries = os.scandir(kind_dir)
    except OSError:
        return
    with entries:
        for entry in entries:
            if entry.name == keep_name or not entry.name.startswith(prefix):
                continue
            try:
                boundary.delete_file(boundary.from_key("thumbs", f"{kind}/{entry.name}"))
            except FilesystemBoundaryError:
                logger.warning("could not remove stale poster thumbnail %s", entry.name)


def get_or_build_thumbnail(
    source_boundary: FilesystemBoundary,
    source_classified: ClassifiedPath,
    *,
    kind: str,
    subject_id: int,
    version: str,
    width: int,
) -> tuple[FilesystemBoundary, ClassifiedPath] | None:
    """Return the cached derivative for one poster version, building it once.

    Returns ``None`` when the source cannot be decoded — the caller falls
    back to serving the original, which is a correctness decision: a grid
    showing a heavy poster beats a grid showing a hole.
    """
    if width not in THUMB_WIDTHS:
        raise FilesystemBoundaryError(f"unsupported thumbnail width {width!r}")
    boundary = thumbs_boundary()
    key = _thumb_key(kind, subject_id, version, width)
    target = boundary.from_key("thumbs", key)
    if (settings.poster_thumbs_path / key).is_file():
        return boundary, target

    source_fd = source_boundary.open_read(source_classified)
    try:
        chunks: list[bytes] = []
        while chunk := os.read(source_fd, 1024 * 1024):
            chunks.append(chunk)
    finally:
        os.close(source_fd)
    try:
        rendered = _render_thumbnail(b"".join(chunks), width)
    except (OSError, UnidentifiedImageError, ValueError):
        logger.warning("poster %s/%s could not be thumbnailed; serving original", kind, subject_id)
        return None

    kind_dir = boundary.from_key("thumbs", kind)
    with contextlib.suppress(FileExistsError):
        boundary.create_directory(kind_dir, parents=True)
    staged, staged_fd = boundary.temporary_file(kind_dir)
    try:
        os.write(staged_fd, rendered)
        os.fsync(staged_fd)
    finally:
        os.close(staged_fd)
    try:
        boundary.atomic_replace(staged, target)
    except FilesystemBoundaryError:
        boundary.delete_file(staged, missing_ok=True)
        raise
    _drop_stale_siblings(boundary, kind, subject_id, keep_name=f"{subject_id}-{version}-w{width}.jpg")
    return boundary, target
