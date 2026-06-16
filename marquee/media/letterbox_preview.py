"""Generate before/after preview frames for the inspection UI (design §14.2).

A "before" frame is the full decoded frame with red lines drawn where the crop
would fall; an "after" frame is the same timestamp cropped to the proposed
content area. Both are small webps cached under ``letterbox_preview_path`` and
served by the API. Pure-ffmpeg; no Pillow needed for the crop itself.
"""

from __future__ import annotations

import logging
from pathlib import Path

from marquee.config import settings
from marquee.media import binaries

logger = logging.getLogger(__name__)

_THUMB_WIDTH = 480


def preview_path(movie_id: int, mode: str, minute: int) -> Path:
    return settings.letterbox_preview_path / f"{movie_id}_{mode}_{minute}.webp"


def _timestamp(minute: int) -> str:
    return f"{minute // 60:02d}:{minute % 60:02d}:00"


def generate_preview(
    source: Path | str,
    *,
    movie_id: int,
    minute: int,
    mode: str,
    crop_top: int,
    crop_bottom: int,
    force: bool = False,
) -> Path | None:
    """Render (and cache) a before/after preview webp; return its path or None."""
    if binaries.resolve("ffmpeg") is None:
        return None
    out = preview_path(movie_id, mode, minute)
    if out.is_file() and not force:
        return out
    out.parent.mkdir(parents=True, exist_ok=True)

    if mode == "after" and (crop_top or crop_bottom):
        vf = (
            f"crop=iw:ih-{crop_top + crop_bottom}:0:{crop_top},"
            f"scale={_THUMB_WIDTH}:-2"
        )
    else:  # before: full frame with crop guide-lines
        guides = ""
        if crop_top or crop_bottom:
            guides = (
                f"drawbox=0:{crop_top}:iw:2:color=red@0.9:t=fill,"
                f"drawbox=0:ih-{crop_bottom}-2:iw:2:color=red@0.9:t=fill,"
            )
        vf = f"{guides}scale={_THUMB_WIDTH}:-2"

    result = binaries.run(
        "ffmpeg",
        [
            "-y", "-hide_banner", "-loglevel", "error",
            "-ss", _timestamp(minute),
            "-i", str(source),
            "-frames:v", "1",
            "-vf", vf,
            str(out),
        ],
        timeout=60.0,
    )
    if not result.ok or not out.is_file():
        logger.warning("preview generation failed for movie %s: %s", movie_id, result.stderr.strip()[:200])
        return None
    return out
