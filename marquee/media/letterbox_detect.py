"""Letterbox crop detection: per-window measurement + consensus (design §12).

Two interchangeable measurement backends produce, per sampled timestamp, the
top/bottom black-bar thickness in pixels:

  - ``cropdetect`` (default): one ffmpeg invocation per window, parses the
    ``crop=W:H:X:Y`` the filter accumulates over the window. No temp files.
  - ``trim`` (fallback): extract one frame, run ImageMagick ``convert -trim``
    at several fuzz levels, take the median. Needs ImageMagick.

Everything downstream of a ``WindowMeasurement`` is backend-agnostic: the
``consensus`` function maps the spread of per-window bars onto the
status/confidence tiers (Cases A/B/C, §6/§8). Pure functions (parsing,
consensus, labels) are unit-tested without any binary; ``detect`` is the only
function that shells out.
"""

from __future__ import annotations

import contextlib
import logging
import os
import re
import statistics
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path

from marquee.config import Settings, settings
from marquee.media import binaries

logger = logging.getLogger(__name__)

_CROP_RE = re.compile(r"crop=(\d+):(\d+):(\d+):(\d+)")
_TRIM_RE = re.compile(r"^(\d+)x(\d+)\+(-?\d+)\+(-?\d+)")

# Detection verdicts (also the LetterboxState.status values for fresh rows).
STATUS_CANDIDATE = "candidate"  # letterboxed, has a recommended crop
STATUS_NOT_LETTERBOXED = "not_letterboxed"
STATUS_VARIABLE_UNSAFE = "variable_unsafe"
STATUS_ERRORED = "errored"

CONF_HIGH = "high"
CONF_MEDIUM = "medium"
CONF_LOW = "low"
CONF_NONE = "none"

_HDR_TRANSFER_FUNCTIONS = {"smpte2084", "arib-std-b67"}


@dataclass
class WindowMeasurement:
    """One sampled timestamp's measured top/bottom bar thickness (px)."""

    minute: int
    ok: bool
    top_bar: int = 0
    bottom_bar: int = 0
    width: int = 0
    height: int = 0
    error: str | None = None

    @property
    def bar(self) -> int:
        """Symmetric bar estimate for this window (clamped non-negative)."""
        return max(0, round((max(0, self.top_bar) + max(0, self.bottom_bar)) / 2))


@dataclass
class DetectionResult:
    status: str
    confidence: str
    recommended_crop_top: int
    recommended_crop_bottom: int
    source_width: int
    source_height: int
    aspect_label: str | None
    samples: list[dict] = field(default_factory=list)
    method: str = "cropdetect"
    error: str | None = None


# ---------------------------------------------------------------------------
# Parsing (pure)
# ---------------------------------------------------------------------------


def parse_cropdetect(stderr: str) -> tuple[int, int, int, int] | None:
    """Return the LAST ``crop=W:H:X:Y`` in cropdetect stderr (the converged box)."""
    matches = _CROP_RE.findall(stderr)
    if not matches:
        return None
    w, h, x, y = matches[-1]
    return int(w), int(h), int(x), int(y)


def parse_trim(info_line: str) -> tuple[int, int, int, int] | None:
    """Parse ImageMagick ``%wx%h+%X+%Y`` trim output → (w, h, x, y)."""
    match = _TRIM_RE.match(info_line.strip())
    if not match:
        return None
    return tuple(int(g) for g in match.groups())  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Sampling schedule (pure)
# ---------------------------------------------------------------------------


def sample_minutes(
    duration_s: float | None, *, is_tv: bool, config: Settings = settings
) -> list[int]:
    """Minutes to sample, clamped to the file's duration when known."""
    if is_tv:
        minutes = list(config.LETTERBOX_TV_SAMPLES)
    else:
        minutes = list(
            range(
                config.LETTERBOX_MOVIE_SAMPLES_MIN,
                config.LETTERBOX_MOVIE_SAMPLES_MAX + 1,
                max(1, config.LETTERBOX_MOVIE_SAMPLE_STEP),
            )
        )
    if duration_s and duration_s > 0:
        limit = duration_s / 60.0
        kept = [m for m in minutes if m < limit]
        # Very short content (e.g. a < 5-minute clip) still gets a few samples.
        if not kept and minutes:
            kept = [m for m in (1, 2, 3) if m < limit] or [0]
        minutes = kept
    return minutes


# ---------------------------------------------------------------------------
# Measurement backends (shell out)
# ---------------------------------------------------------------------------


def _timestamp(minute: int) -> str:
    return f"{minute // 60:02d}:{minute % 60:02d}:00"


def _bars_from_box(full_height: int, h: int, y: int) -> tuple[int, int]:
    """Top/bottom bar px from a content box of height *h* at offset *y*."""
    top = max(0, y)
    bottom = max(0, full_height - h - y)
    return top, bottom


def cropdetect_limit_for(
    color_transfer: str | None, *, config: Settings = settings
) -> int:
    """Return the cropdetect threshold for SDR vs HDR transfer functions."""
    transfer = color_transfer.lower().strip(" ,") if color_transfer else None
    if transfer in _HDR_TRANSFER_FUNCTIONS:
        return config.LETTERBOX_CROPDETECT_HDR_LIMIT
    return config.LETTERBOX_CROPDETECT_LIMIT


def measure_window_cropdetect(
    path: str,
    minute: int,
    full_height: int,
    *,
    config: Settings = settings,
    cropdetect_limit: int | None = None,
) -> WindowMeasurement:
    limit = cropdetect_limit or config.LETTERBOX_CROPDETECT_LIMIT
    rnd = config.LETTERBOX_CROPDETECT_ROUND
    window = config.LETTERBOX_WINDOW_SECONDS
    vf = f"cropdetect=limit={limit}:round={rnd}:reset=1"
    args = [
        "-hide_banner", "-nostats",
        "-ss", _timestamp(minute),
        "-i", path,
        "-an", "-sn",
        "-t", str(window),
        "-vf", vf,
        "-f", "null", "-",
    ]
    try:
        result = binaries.run("ffmpeg", args, timeout=60.0)
    except binaries.BinaryError as exc:
        return WindowMeasurement(minute=minute, ok=False, error=str(exc))

    crop = parse_cropdetect(result.stderr)
    # Some ffmpeg builds reject the ``reset`` option name — retry minimally.
    if crop is None and "reset" in result.stderr.lower() and "option" in result.stderr.lower():
        args[args.index(vf)] = f"cropdetect=limit={limit}:round={rnd}"
        result = binaries.run("ffmpeg", args, timeout=60.0)
        crop = parse_cropdetect(result.stderr)
    if crop is None:
        return WindowMeasurement(
            minute=minute, ok=False, error="no cropdetect output"
        )

    w, h, _x, y = crop
    top, bottom = _bars_from_box(full_height, h, y)
    return WindowMeasurement(
        minute=minute, ok=True, top_bar=top, bottom_bar=bottom, width=w, height=h
    )


def measure_window_trim(
    path: str, minute: int, full_height: int, *, config: Settings = settings
) -> WindowMeasurement:
    """ImageMagick fallback: extract a frame and trim it at several fuzz levels."""
    fd, tmp = tempfile.mkstemp(suffix=".png")
    os.close(fd)
    try:
        extract = binaries.run(
            "ffmpeg",
            [
                "-y", "-hide_banner", "-loglevel", "error",
                "-ss", _timestamp(minute),
                "-i", path,
                "-frames:v", "1", "-q:v", "2", tmp,
            ],
            timeout=60.0,
        )
        if not extract.ok or not os.path.getsize(tmp):
            return WindowMeasurement(minute=minute, ok=False, error="frame extract failed")

        tops: list[int] = []
        bottoms: list[int] = []
        for fuzz in config.LETTERBOX_TRIM_FUZZ:
            trimmed = binaries.run(
                "convert",
                [tmp, "-fuzz", f"{fuzz}%", "-trim", "+repage",
                 "-format", "%wx%h+%X+%Y", "info:"],
                timeout=30.0,
            )
            box = parse_trim(trimmed.stdout)
            if box is None:
                continue
            _w, h, _x, y = box
            top, bottom = _bars_from_box(full_height, h, y)
            tops.append(top)
            bottoms.append(bottom)

        if not tops:
            return WindowMeasurement(minute=minute, ok=False, error="trim produced no box")
        return WindowMeasurement(
            minute=minute,
            ok=True,
            top_bar=round(statistics.median(tops)),
            bottom_bar=round(statistics.median(bottoms)),
            height=full_height - round(statistics.median(tops)) - round(statistics.median(bottoms)),
        )
    except binaries.BinaryError as exc:
        return WindowMeasurement(minute=minute, ok=False, error=str(exc))
    finally:
        with contextlib.suppress(OSError):
            os.unlink(tmp)


# ---------------------------------------------------------------------------
# Consensus (pure)
# ---------------------------------------------------------------------------


def aspect_label(width: int, effective_height: int) -> str | None:
    if width <= 0 or effective_height <= 0:
        return None
    return f"{width / effective_height:.2f}:1"


def consensus(
    measurements: list[WindowMeasurement],
    *,
    width: int,
    height: int,
    method: str = "cropdetect",
    config: Settings = settings,
) -> DetectionResult:
    """Map per-window bar measurements onto status/confidence (§12.3)."""
    noise = config.LETTERBOX_NOISE_PX
    min_bar = config.LETTERBOX_MIN_BAR_PX
    agree = config.LETTERBOX_AGREE_PX
    medium_spread = config.LETTERBOX_MEDIUM_SPREAD_PX
    asym_tol = config.LETTERBOX_ASYM_PX

    samples = [
        {
            "minute": m.minute,
            "ok": m.ok,
            "top_bar": m.top_bar,
            "bottom_bar": m.bottom_bar,
            "bar": m.bar,
            "error": m.error,
        }
        for m in measurements
    ]

    ok = [m for m in measurements if m.ok]
    if not ok:
        return DetectionResult(
            status=STATUS_ERRORED, confidence=CONF_NONE,
            recommended_crop_top=0, recommended_crop_bottom=0,
            source_width=width, source_height=height, aspect_label=None,
            samples=samples, method=method, error="no frames could be measured",
        )

    bars = [m.bar for m in ok]
    mn, mx = min(bars), max(bars)
    med = round(statistics.median(bars))
    spread = mx - mn
    zero_present = any(b <= noise for b in bars)
    nonzero_present = any(b > min_bar for b in bars)

    def result(status, conf, top, bottom) -> DetectionResult:
        eff = height - top - bottom
        return DetectionResult(
            status=status, confidence=conf,
            recommended_crop_top=top, recommended_crop_bottom=bottom,
            source_width=width, source_height=height,
            aspect_label=aspect_label(width, eff) if top or bottom else None,
            samples=samples, method=method,
        )

    if med <= noise:
        return result(STATUS_NOT_LETTERBOXED, CONF_NONE, 0, 0)
    if zero_present and nonzero_present:
        # Some scenes fill the 16:9 frame — any crop would clip them (Case A).
        return result(STATUS_VARIABLE_UNSAFE, CONF_LOW, 0, 0)

    # Letterboxed. Conservative recommendation when ratios vary (Case B);
    # the consensus median when they agree (Case C).
    if spread > agree:
        rec = mn  # narrowest bars → preserves the most content, never clips
        conf = CONF_MEDIUM if spread <= medium_spread else CONF_LOW
    else:
        rec = med
        conf = CONF_HIGH

    top_med = round(statistics.median([max(0, m.top_bar) for m in ok]))
    bottom_med = round(statistics.median([max(0, m.bottom_bar) for m in ok]))
    asymmetric = abs(top_med - bottom_med) > asym_tol

    if asymmetric and config.LETTERBOX_ASYMMETRIC:
        return result(STATUS_CANDIDATE, conf, top_med, bottom_med)
    if asymmetric:
        # Bars are genuinely uneven but we're forcing symmetry — trust it less.
        conf = {CONF_HIGH: CONF_MEDIUM, CONF_MEDIUM: CONF_LOW}.get(conf, conf)
    return result(STATUS_CANDIDATE, conf, rec, rec)


# ---------------------------------------------------------------------------
# Orchestration (shells out)
# ---------------------------------------------------------------------------


def detect(
    path: str | Path,
    *,
    width: int,
    height: int,
    duration_s: float | None = None,
    color_transfer: str | None = None,
    is_tv: bool = False,
    method: str | None = None,
    config: Settings = settings,
) -> DetectionResult:
    """Sample the file, measure each window, and build the consensus verdict."""
    path = str(path)
    method = (method or config.LETTERBOX_DETECT_METHOD).lower()
    cropdetect_limit = cropdetect_limit_for(color_transfer, config=config)

    minutes = sample_minutes(duration_s, is_tv=is_tv, config=config)
    measurements: list[WindowMeasurement] = []
    consecutive_clear = 0
    for minute in minutes:
        if method == "trim":
            m = measure_window_trim(path, minute, height, config=config)
        else:
            m = measure_window_cropdetect(
                path,
                minute,
                height,
                config=config,
                cropdetect_limit=cropdetect_limit,
            )
        measurements.append(m)
        if m.ok and m.bar <= config.LETTERBOX_NOISE_PX:
            consecutive_clear += 1
            if consecutive_clear >= config.LETTERBOX_EARLY_STOP_WINDOWS:
                break
        elif m.ok:
            consecutive_clear = 0

    return consensus(
        measurements, width=width, height=height, method=method, config=config
    )


def result_to_dict(result: DetectionResult) -> dict:
    """Plain dict for JSON persistence / API responses."""
    return asdict(result)
