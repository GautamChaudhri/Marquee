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
# Each probe is a full-frame decode (slow on 4K HDR over a busy disk), so keep
# the count and per-probe timeout tight — a "good enough" bright frame beats a
# perfect one that times out and 500s the preview.
_MAX_BRIGHT_PROBES = 3
_LUMA_PROBE_TIMEOUT = 10.0
_YAVG_RE = re.compile(r"YAVG=([0-9.]+)")

# Process-local cache of the bright-minute decision, keyed by (subject_key, minute).
# The before/after pair render at the same minute, so this lets the second
# request reuse the first's pick instead of re-probing, and makes repeat views
# instant. Cleared per-subject by purge_previews.
_bright_minute_cache: dict[tuple[str, int], int] = {}

# Bump when the render recipe changes so stale cached frames are regenerated.
_CACHE_VERSION = "v3"


def movie_subject_key(movie_id: int) -> str:
    return f"movie-{movie_id}"


def episode_subject_key(episode_id: int) -> str:
    return f"episode-{episode_id}"


def _coerce_subject_key(subject_key: str | int) -> str:
    return movie_subject_key(subject_key) if isinstance(subject_key, int) else subject_key


def preview_path(subject_key: str | int, mode: str, minute: int, *, exact: bool = False) -> Path:
    # "exact" frames (the literal sample minute, no brightness substitution) and
    # "bright" frames (the brightness-substituted default view) must never share a
    # cache slot — that collision is what made clicking a sample minute silently
    # show whatever minute the brightness probe had substituted for it.
    policy = "exact" if exact else "bright"
    normalized_subject_key = _coerce_subject_key(subject_key)
    return (
        settings.letterbox_preview_path
        / f"{normalized_subject_key}_{mode}_{minute}_{policy}_{_CACHE_VERSION}.webp"
    )


def _preview_glob(subject_key: str | int) -> str:
    return f"{_coerce_subject_key(subject_key)}_*.webp"


def _timestamp(minute: int) -> str:
    return f"{minute // 60:02d}:{minute % 60:02d}:00"


def _measure_luma(source: Path | str, minute: int) -> float | None:
    """Average luma (YAVG, 0-255) of one frame at *minute*, or None on failure.

    A slow/timed-out probe (common on 4K HDR over a busy disk) returns None
    rather than raising — brightness selection is best-effort, so the caller
    falls back to the requested minute instead of failing the whole preview.
    """
    try:
        result = binaries.run(
            "ffmpeg",
            [
                "-hide_banner",
                "-loglevel",
                "info",
                "-nostats",
                "-ss",
                _timestamp(minute),
                "-i",
                str(source),
                "-frames:v",
                "1",
                "-vf",
                "signalstats,metadata=print",
                "-f",
                "null",
                "-",
            ],
            timeout=_LUMA_PROBE_TIMEOUT,
        )
    except binaries.BinaryError as exc:
        logger.debug("luma probe failed at minute %s: %s", minute, exc)
        return None
    match = _YAVG_RE.search(result.stderr)
    return float(match.group(1)) if match else None


def _pick_bright_minute(
    source: Path | str,
    minute: int,
    candidates: list[int] | None,
    *,
    subject_key: str | int | None = None,
    movie_id: int | None = None,
) -> int:
    """Choose the brightest timestamp so the black bars stay visible.

    Measures the requested minute first; if it's bright enough, keep it.
    Otherwise probe the candidate minutes (capped) and return whichever frame
    has the highest average luma — falling back to the original minute.

    The decision is cached per ``(subject_key, minute)`` so the before/after pair
    (same minute) doesn't probe twice and repeat views are instant.
    """
    normalized_subject_key = _coerce_subject_key(
        subject_key if subject_key is not None else movie_subject_key(movie_id or 0)
    )
    cache_key = (normalized_subject_key, minute)
    cached = _bright_minute_cache.get(cache_key)
    if cached is not None:
        return cached

    base = _measure_luma(source, minute)
    if base is not None and base >= _MIN_LUMA:
        _bright_minute_cache[cache_key] = minute
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
    _bright_minute_cache[cache_key] = best_minute
    return best_minute


def purge_previews(subject_key: str | int) -> int:
    """Delete all cached preview files for one subject (a movie or an episode)."""
    normalized_subject_key = _coerce_subject_key(subject_key)
    # Drop any cached bright-minute decisions for this subject so a re-detect
    # (new crop / new samples) re-probes instead of reusing a stale pick. Done
    # before the directory check since the cache is independent of the files.
    for key in [k for k in _bright_minute_cache if k[0] == normalized_subject_key]:
        del _bright_minute_cache[key]

    root = settings.letterbox_preview_path
    if not root.exists():
        return 0

    removed = 0
    for path in root.glob(_preview_glob(normalized_subject_key)):
        try:
            path.unlink(missing_ok=True)
            removed += 1
        except OSError as exc:
            logger.warning(
                "preview purge failed for %s (%s): %s",
                normalized_subject_key,
                path.name,
                exc,
            )
    return removed


def generate_preview(
    source: Path | str,
    *,
    subject_key: str | int | None = None,
    movie_id: int | None = None,
    minute: int,
    mode: str,
    crop_top: int,
    crop_bottom: int,
    height: int | None = None,
    candidate_minutes: list[int] | None = None,
    exact: bool = False,
    force: bool = False,
) -> Path | None:
    """Render (and cache) a full-resolution before/after preview; return path or None.

    *exact* renders the literal requested minute — used when the user clicks a
    specific sample row, so the frame they see matches the minute they clicked.
    Otherwise the brightest nearby candidate frame is substituted in, which is
    only appropriate for the single auto-picked default/landing preview.
    """
    if binaries.resolve("ffmpeg") is None:
        return None
    normalized_subject_key = _coerce_subject_key(
        subject_key if subject_key is not None else movie_subject_key(movie_id or 0)
    )
    out = preview_path(normalized_subject_key, mode, minute, exact=exact)
    if out.is_file() and not force:
        return out
    out.parent.mkdir(parents=True, exist_ok=True)

    src_minute = (
        minute
        if exact
        else _pick_bright_minute(
            source,
            minute,
            candidate_minutes,
            subject_key=normalized_subject_key,
        )
    )

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
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            _timestamp(src_minute),
            "-i",
            str(source),
            "-frames:v",
            "1",
            "-vf",
            vf,
            "-quality",
            "95",
            str(out),
        ],
        timeout=90.0,
    )
    if not result.ok or not out.is_file():
        logger.warning(
            "preview generation failed for %s: %s",
            normalized_subject_key,
            result.stderr.strip()[:200],
        )
        return None
    return out


def warm_previews(
    source: Path | str,
    *,
    subject_key: str | int,
    samples: list[dict] | None,
    crop_top: int,
    crop_bottom: int,
    height: int | None = None,
) -> list[Path]:
    """Pre-render all preview frames for the sampled minutes of one subject."""
    if binaries.resolve("ffmpeg") is None:
        return []

    normalized_subject_key = _coerce_subject_key(subject_key)
    purge_previews(normalized_subject_key)

    minutes: list[int] = []
    for sample in samples or []:
        if not sample.get("ok"):
            continue
        minute = sample.get("minute")
        if isinstance(minute, int) and minute not in minutes:
            minutes.append(minute)

    generated: list[Path] = []
    if not minutes:
        return generated

    for minute in minutes:
        for mode in ("before", "after"):
            for exact in (False, True):
                out = generate_preview(
                    source,
                    subject_key=normalized_subject_key,
                    minute=minute,
                    mode=mode,
                    crop_top=crop_top,
                    crop_bottom=crop_bottom,
                    height=height,
                    candidate_minutes=minutes,
                    exact=exact,
                    force=True,
                )
                if out is not None:
                    generated.append(out)
    return generated


def warm_clear_preview(
    source: Path | str,
    *,
    subject_key: str | int,
    minute: int,
    candidate_minutes: list[int] | None,
) -> list[Path]:
    """Render one bright before-frame for a scanned-clear episode."""
    if binaries.resolve("ffmpeg") is None:
        return []

    normalized_subject_key = _coerce_subject_key(subject_key)
    purge_previews(normalized_subject_key)
    out = generate_preview(
        source,
        subject_key=normalized_subject_key,
        minute=minute,
        mode="before",
        crop_top=0,
        crop_bottom=0,
        candidate_minutes=candidate_minutes,
        exact=False,
        force=True,
    )
    return [out] if out is not None else []


def purge_movie_previews(movie_id: int) -> int:
    for key in [
        cache_key
        for cache_key in _bright_minute_cache
        if cache_key[0] in {movie_id, movie_subject_key(movie_id)}
    ]:
        del _bright_minute_cache[key]
    removed = purge_previews(movie_subject_key(movie_id))
    root = settings.letterbox_preview_path
    if root.exists():
        for path in root.glob(f"{movie_id}_*.webp"):
            try:
                path.unlink(missing_ok=True)
                removed += 1
            except OSError as exc:
                logger.warning("legacy preview purge failed for %s (%s): %s", movie_id, path.name, exc)
    return removed


def purge_all_episode_previews() -> int:
    """Delete all cached episode preview files and bright-minute decisions."""
    for key in [
        cache_key
        for cache_key in _bright_minute_cache
        if isinstance(cache_key[0], str) and cache_key[0].startswith("episode-")
    ]:
        del _bright_minute_cache[key]

    root = settings.letterbox_preview_path
    if not root.exists():
        return 0

    removed = 0
    for path in root.glob("episode-*_*.webp"):
        try:
            path.unlink(missing_ok=True)
            removed += 1
        except OSError as exc:
            logger.warning("episode preview purge failed for %s: %s", path.name, exc)
    return removed


def warm_movie_previews(
    source: Path | str,
    *,
    movie_id: int,
    samples: list[dict] | None,
    crop_top: int,
    crop_bottom: int,
    height: int | None = None,
) -> list[Path]:
    if binaries.resolve("ffmpeg") is None:
        return []

    purge_movie_previews(movie_id)

    minutes: list[int] = []
    for sample in samples or []:
        if not sample.get("ok"):
            continue
        minute = sample.get("minute")
        if isinstance(minute, int) and minute not in minutes:
            minutes.append(minute)

    generated: list[Path] = []
    if not minutes:
        return generated

    for minute in minutes:
        for mode in ("before", "after"):
            for exact in (False, True):
                out = generate_preview(
                    source,
                    movie_id=movie_id,
                    minute=minute,
                    mode=mode,
                    crop_top=crop_top,
                    crop_bottom=crop_bottom,
                    height=height,
                    candidate_minutes=minutes,
                    exact=exact,
                    force=True,
                )
                if out is not None:
                    generated.append(out)
    return generated
