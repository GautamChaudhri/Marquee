"""Generate before/after preview frames for the inspection UI (design §14.2).

A "before" frame is the full decoded frame with red lines drawn where the crop
would fall; an "after" frame is the same timestamp cropped to the proposed
content area. Both are rendered at the media's **native resolution** and high
webp quality, cached under ``letterbox_preview_path`` and served by the API.

The source timestamp is chosen for brightness: a too-dark frame hides the
black-bar boundary, so we probe a few candidate timestamps and render the
brightest one (deterministic, so before/after stay in sync). Pure-ffmpeg.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from marquee.config import settings
from marquee.media import binaries

logger = logging.getLogger(__name__)

# A frame whose average luma (YAVG, 0-255) is below this is too dark to show the
# black-bar boundary clearly, so we look for a brighter timestamp instead.
_MIN_LUMA = 60.0
_MAX_BRIGHT_PROBES = 6
_YAVG_RE = re.compile(r"YAVG=([0-9.]+)")

# Bump when the render recipe changes so stale cached frames are regenerated.
_CACHE_VERSION = "v2"


def preview_path(movie_id: int, mode: str, minute: int) -> Path:
    return settings.letterbox_preview_path / f"{movie_id}_{mode}_{minute}_{_CACHE_VERSION}.webp"


def _timestamp(minute: int) -> str:
    return f"{minute // 60:02d}:{minute % 60:02d}:00"


def _measure_luma(source: Path | str, minute: int) -> float | None:
    """Average luma (YAVG, 0-255) of one frame at *minute*, or None on failure."""
    result = binaries.run(
        "ffmpeg",
        [
            "-hide_banner", "-loglevel", "info", "-nostats",
            "-ss", _timestamp(minute),
            "-i", str(source),
            "-frames:v", "1",
            "-vf", "signalstats,metadata=print",
            "-f", "null", "-",
        ],
        timeout=30.0,
    )
    match = _YAVG_RE.search(result.stderr)
    return float(match.group(1)) if match else None


def _pick_bright_minute(
    source: Path | str, minute: int, candidates: list[int] | None
) -> int:
    """Choose the brightest timestamp so the black bars stay visible.

    Measures the requested minute first; if it's bright enough, keep it.
    Otherwise probe the candidate minutes (capped) and return whichever frame
    has the highest average luma — falling back to the original minute.
    """
    base = _measure_luma(source, minute)
    if base is not None and base >= _MIN_LUMA:
        return minute

    options: list[int] = []
    for m in [minute, *(candidates or [])]:
        if m not in options:
            options.append(m)
    options = options[:_MAX_BRIGHT_PROBES]

    best_minute, best_luma = minute, base if base is not None else -1.0
    for m in options:
        luma = base if m == minute else _measure_luma(source, m)
        if luma is not None and luma > best_luma:
            best_minute, best_luma = m, luma
    return best_minute


def generate_preview(
    source: Path | str,
    *,
    movie_id: int,
    minute: int,
    mode: str,
    crop_top: int,
    crop_bottom: int,
    height: int | None = None,
    candidate_minutes: list[int] | None = None,
    force: bool = False,
) -> Path | None:
    """Render (and cache) a full-resolution before/after preview; return path or None."""
    if binaries.resolve("ffmpeg") is None:
        return None
    out = preview_path(movie_id, mode, minute)
    if out.is_file() and not force:
        return out
    out.parent.mkdir(parents=True, exist_ok=True)

    src_minute = _pick_bright_minute(source, minute, candidate_minutes)

    if mode == "after" and (crop_top or crop_bottom):
        vf = f"crop=iw:ih-{crop_top + crop_bottom}:0:{crop_top}"
    elif crop_top or crop_bottom:  # before: full frame with crop guide-lines
        # Scale the guide thickness with resolution so it stays visible at 4K.
        t = max(2, round((height or 1080) / 270))
        parts = []
        if crop_top:
            parts.append(f"drawbox=0:{crop_top}:iw:{t}:color=red@0.9:t=fill")
        if crop_bottom:
            parts.append(f"drawbox=0:ih-{crop_bottom}-{t}:iw:{t}:color=red@0.9:t=fill")
        vf = ",".join(parts)
    else:
        vf = "null"

    result = binaries.run(
        "ffmpeg",
        [
            "-y", "-hide_banner", "-loglevel", "error",
            "-ss", _timestamp(src_minute),
            "-i", str(source),
            "-frames:v", "1",
            "-vf", vf,
            "-quality", "95",
            str(out),
        ],
        timeout=90.0,
    )
    if not result.ok or not out.is_file():
        logger.warning("preview generation failed for movie %s: %s", movie_id, result.stderr.strip()[:200])
        return None
    return out
