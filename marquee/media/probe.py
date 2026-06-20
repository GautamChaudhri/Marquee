"""ffprobe-backed media inspection + the resolution pre-filter (design §4).

``prefilter_bucket`` is a pure function over encoded pixel dimensions — it
needs no ffprobe and powers the cheap "candidate vs skip" triage from sync
metadata. ``probe_video`` is the on-demand fallback when the DB has no stored
resolution (or for container/duration which sync doesn't always carry).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

from marquee.media import binaries

logger = logging.getLogger(__name__)

# Aspect-ratio thresholds for the pre-filter. 16:9 is 1.7778; a clean 16:9
# container must fall *below* NATIVE_WIDE_AR so it is treated as a candidate,
# while anything genuinely wider (2.00, 2.39, native 1.85 encodes) is skipped.
NATIVE_WIDE_AR = 1.80
PILLARBOX_AR = 1.70
MIN_CANDIDATE_HEIGHT = 1080


@dataclass
class VideoInfo:
    width: int
    height: int
    duration_s: float | None
    container: str | None
    codec: str | None
    color_transfer: str | None = None


@dataclass
class PrefilterResult:
    bucket: str  # "candidate" | "skip"
    reason: str
    aspect_ratio: float | None


def prefilter_bucket(width: int | None, height: int | None) -> PrefilterResult:
    """Triage a file by encoded resolution alone (design §4).

    Candidates are ~16:9 containers at 1080p+ that *might* hide baked-in
    letterbox bars. Everything else is skipped without decoding a frame.
    """
    if not width or not height or width <= 0 or height <= 0:
        return PrefilterResult("skip", "unknown_resolution", None)

    ar = width / height
    if ar >= NATIVE_WIDE_AR:
        # Already wider than 16:9 — native scope / 1.85 / already cropped.
        return PrefilterResult("skip", "native_wide", ar)
    if ar < PILLARBOX_AR:
        # 4:3 or narrower — pillarboxed, not a letterbox candidate.
        return PrefilterResult("skip", "pillarbox_or_4_3", ar)
    if height < MIN_CANDIDATE_HEIGHT:
        # 720p and below — bars too small for the crop to be worthwhile.
        return PrefilterResult("skip", "low_resolution", ar)
    return PrefilterResult("candidate", "sixteen_nine_container", ar)


def probe_video(path: Path | str, retry_count: int = 1) -> VideoInfo | None:
    """Return the first video stream's dimensions + container/duration/codec.

    Returns None if ffprobe is unavailable, the file can't be read, or it has
    no video stream.

    Args:
        path: Path to the video file
        retry_count: Number of retries with progressive timeout (0 = no retry, default 1)
    """
    if binaries.resolve("ffprobe") is None:
        logger.warning("probe_video: ffprobe not available")
        return None

    # Progressive timeouts for network/FUSE storage (mergerfs, NFS, etc.)
    # Increased from 30s base to handle slow network storage under load
    timeouts = [60.0, 120.0, 180.0]
    max_attempts = min(retry_count + 1, len(timeouts))

    result = None
    last_error = None

    for attempt in range(max_attempts):
        timeout = timeouts[attempt]
        try:
            result = binaries.run(
                "ffprobe",
                [
                    "-v", "error",
                    "-print_format", "json",
                    "-show_format",
                    "-show_streams",
                    str(path),
                ],
                timeout=timeout,
            )
            # Success - exit retry loop
            break
        except binaries.BinaryError as exc:
            last_error = exc
            if "timed out" in str(exc).lower() and attempt < max_attempts - 1:
                logger.warning(
                    "ffprobe timeout attempt %d/%d for %s (%.1fs) - retrying with longer timeout",
                    attempt + 1,
                    max_attempts,
                    path,
                    timeout,
                )
                continue
            # Not a timeout or out of retries - return None
            logger.error("ffprobe error for %s after %d attempts: %s", path, attempt + 1, exc)
            return None

    if result is None:
        if last_error:
            logger.error("ffprobe failed for %s after %d attempts: %s", path, max_attempts, last_error)
        return None

    if not result.ok:
        logger.warning("ffprobe failed for %s: %s", path, result.stderr.strip()[:200])
        return None

    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        logger.warning("ffprobe returned non-JSON for %s", path)
        return None

    video = next(
        (s for s in data.get("streams", []) if s.get("codec_type") == "video"), None
    )
    if video is None:
        return None

    fmt = data.get("format", {})
    duration = None
    raw_duration = fmt.get("duration") or video.get("duration")
    if raw_duration is not None:
        try:
            duration = float(raw_duration)
        except (TypeError, ValueError):
            duration = None

    try:
        width = int(video.get("width", 0))
        height = int(video.get("height", 0))
    except (TypeError, ValueError):
        return None
    if width <= 0 or height <= 0:
        return None

    return VideoInfo(
        width=width,
        height=height,
        duration_s=duration,
        container=fmt.get("format_name"),
        codec=video.get("codec_name"),
        color_transfer=video.get("color_transfer"),
    )


def has_video_track(path: Path | str) -> bool:
    """True if the file has at least one video stream (cheap ffprobe check)."""
    info = probe_video(path)
    return info is not None
