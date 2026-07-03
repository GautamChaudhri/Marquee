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
import time
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
CONF_VARIABLE = "variable"
CONF_NONE = "none"

_HDR_TRANSFER_FUNCTIONS = {"smpte2084", "arib-std-b67"}
_ASYM_LOW_FRACTION = 0.25
_NVDEC_DECODERS = {
    "h264": "h264_cuvid",
    "hevc": "hevc_cuvid",
    "av1": "av1_cuvid",
    "vp9": "vp9_cuvid",
}
_NVDEC_BENCHMARKS: dict[tuple[str, str, int, int], bool] = {}
_NVDEC_CAPABILITIES: dict[str, bool] = {}


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
    backend: str = "cpu"
    elapsed_ms: int | None = None

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
    variable_ar: bool = False
    variable_ar_note: str | None = None


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
    duration_s: float | None,
    *,
    is_tv: bool,
    thorough: bool = False,
    config: Settings = settings,
) -> list[int]:
    """Minutes to sample, clamped to the file's duration when known."""
    if is_tv:
        minutes = list(config.LETTERBOX_TV_SAMPLES)
    else:
        step = max(1, config.LETTERBOX_MOVIE_SAMPLE_STEP)
        if thorough:
            step = max(1, step // 3)
        minutes = list(
            range(
                config.LETTERBOX_MOVIE_SAMPLES_MIN,
                config.LETTERBOX_MOVIE_SAMPLES_MAX + 1,
                step,
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


def cropdetect_limit_for(color_transfer: str | None, *, config: Settings = settings) -> int:
    """Return the cropdetect threshold for SDR vs HDR transfer functions."""
    transfer = color_transfer.lower().strip(" ,") if color_transfer else None
    if transfer in _HDR_TRANSFER_FUNCTIONS:
        return config.LETTERBOX_CROPDETECT_HDR_LIMIT
    return config.LETTERBOX_CROPDETECT_LIMIT


def _nvdec_decoder_for(codec: str | None, *, config: Settings = settings) -> str | None:
    """Return a usable CUDA decoder name, without treating it as a requirement."""
    if config.LETTERBOX_DETECT_NVIDIA_ACCELERATION == "off":
        return None
    decoder = _NVDEC_DECODERS.get(codec or "")
    if decoder is None:
        return None
    if decoder in _NVDEC_CAPABILITIES:
        return decoder if _NVDEC_CAPABILITIES[decoder] else None
    try:
        hwaccels = binaries.run("ffmpeg", ["-hide_banner", "-hwaccels"], timeout=30.0)
        decoders = binaries.run("ffmpeg", ["-hide_banner", "-decoders"], timeout=30.0)
    except binaries.BinaryError:
        _NVDEC_CAPABILITIES[decoder] = False
        return None
    available = (
        hwaccels.ok
        and decoders.ok
        and "cuda" in hwaccels.stdout.split()
        and any(line.split()[1:2] == [decoder] for line in decoders.stdout.splitlines())
    )
    _NVDEC_CAPABILITIES[decoder] = available
    return decoder if available else None


def measure_window_cropdetect(
    path: str,
    minute: int,
    full_height: int,
    *,
    config: Settings = settings,
    cropdetect_limit: int | None = None,
    nvdec_decoder: str | None = None,
    pix_fmt: str | None = None,
) -> WindowMeasurement:
    limit = cropdetect_limit or config.LETTERBOX_CROPDETECT_LIMIT
    rnd = config.LETTERBOX_CROPDETECT_ROUND
    window = config.LETTERBOX_WINDOW_SECONDS
    vf = f"cropdetect=limit={limit}:round={rnd}:reset=1"
    backend = "cpu"
    args = ["-hide_banner", "-nostats"]
    if nvdec_decoder:
        backend = "nvdec"
        download_format = "p010le" if pix_fmt and "10" in pix_fmt else "nv12"
        vf = f"hwdownload,format={download_format},{vf}"
        args.extend(
            [
                "-hwaccel",
                "cuda",
                "-hwaccel_output_format",
                "cuda",
                "-c:v:0",
                nvdec_decoder,
            ]
        )
    args.extend(
        [
            "-ss",
            _timestamp(minute),
            "-i",
            path,
            "-an",
            "-sn",
            "-t",
            str(window),
            "-vf",
            vf,
            "-f",
            "null",
            "-",
        ]
    )
    started = time.monotonic()
    try:
        result = binaries.run("ffmpeg", args, timeout=60.0)
    except binaries.BinaryError as exc:
        return WindowMeasurement(
            minute=minute,
            ok=False,
            error=str(exc),
            backend=backend,
            elapsed_ms=round((time.monotonic() - started) * 1000),
        )

    crop = parse_cropdetect(result.stderr)
    # Some ffmpeg builds reject the ``reset`` option name — retry minimally.
    if crop is None and "reset" in result.stderr.lower() and "option" in result.stderr.lower():
        retry_filter = f"cropdetect=limit={limit}:round={rnd}"
        if nvdec_decoder:
            download_format = "p010le" if pix_fmt and "10" in pix_fmt else "nv12"
            retry_filter = f"hwdownload,format={download_format},{retry_filter}"
        args[args.index(vf)] = retry_filter
        result = binaries.run("ffmpeg", args, timeout=60.0)
        crop = parse_cropdetect(result.stderr)
    if crop is None:
        return WindowMeasurement(
            minute=minute,
            ok=False,
            error="no cropdetect output",
            backend=backend,
            elapsed_ms=round((time.monotonic() - started) * 1000),
        )

    w, h, _x, y = crop
    top, bottom = _bars_from_box(full_height, h, y)
    return WindowMeasurement(
        minute=minute,
        ok=True,
        top_bar=top,
        bottom_bar=bottom,
        width=w,
        height=h,
        backend=backend,
        elapsed_ms=round((time.monotonic() - started) * 1000),
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
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-ss",
                _timestamp(minute),
                "-i",
                path,
                "-frames:v",
                "1",
                "-q:v",
                "2",
                tmp,
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
                [tmp, "-fuzz", f"{fuzz}%", "-trim", "+repage", "-format", "%wx%h+%X+%Y", "info:"],
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


def _cluster_bars(bars: list[int], gap: int) -> list[list[int]]:
    """Group sorted bar values into clusters; a jump > *gap* starts a new cluster."""
    ordered = sorted(bars)
    clusters: list[list[int]] = [[ordered[0]]]
    for b in ordered[1:]:
        if b - clusters[-1][-1] <= gap:
            clusters[-1].append(b)
        else:
            clusters.append([b])
    return clusters


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
            "backend": m.backend,
            "elapsed_ms": m.elapsed_ms,
        }
        for m in measurements
    ]

    ok = [m for m in measurements if m.ok]
    if not ok:
        return DetectionResult(
            status=STATUS_ERRORED,
            confidence=CONF_NONE,
            recommended_crop_top=0,
            recommended_crop_bottom=0,
            source_width=width,
            source_height=height,
            aspect_label=None,
            samples=samples,
            method=method,
            error="no frames could be measured",
        )

    def result(
        status, conf, top, bottom, *, variable_ar: bool = False, variable_ar_note: str | None = None
    ) -> DetectionResult:
        eff = height - top - bottom
        return DetectionResult(
            status=status,
            confidence=conf,
            recommended_crop_top=top,
            recommended_crop_bottom=bottom,
            source_width=width,
            source_height=height,
            aspect_label=aspect_label(width, eff) if top or bottom else None,
            samples=samples,
            method=method,
            variable_ar=variable_ar,
            variable_ar_note=variable_ar_note,
        )

    def is_zero(m: WindowMeasurement) -> bool:
        return max(0, m.top_bar) <= noise and max(0, m.bottom_bar) <= noise

    def is_symmetric(m: WindowMeasurement) -> bool:
        return abs(max(0, m.top_bar) - max(0, m.bottom_bar)) <= asym_tol

    def is_one_sided(m: WindowMeasurement) -> bool:
        top = max(0, m.top_bar)
        bottom = max(0, m.bottom_bar)
        return (top <= noise and bottom > min_bar) or (bottom <= noise and top > min_bar)

    def downgrade(conf: str) -> str:
        return {CONF_HIGH: CONF_MEDIUM, CONF_MEDIUM: CONF_LOW}.get(conf, conf)

    zero_present = any(is_zero(m) for m in ok)
    one_sided_count = sum(1 for m in ok if is_one_sided(m))
    asymmetric_count = sum(1 for m in ok if not is_zero(m) and not is_symmetric(m))
    asymmetric_fraction = asymmetric_count / len(ok)
    symmetric_nonzero = [m for m in ok if is_symmetric(m) and m.bar > min_bar]

    if not symmetric_nonzero:
        if one_sided_count / len(ok) >= 0.5:
            return result(STATUS_NOT_LETTERBOXED, CONF_NONE, 0, 0)
        if all(is_zero(m) for m in ok):
            return result(STATUS_NOT_LETTERBOXED, CONF_NONE, 0, 0)
        if config.LETTERBOX_ASYMMETRIC:
            top = round(statistics.median([max(0, m.top_bar) for m in ok]))
            bottom = round(statistics.median([max(0, m.bottom_bar) for m in ok]))
            return result(STATUS_CANDIDATE, CONF_LOW, top, bottom)
        rec = round(statistics.median([m.bar for m in ok]))
        return result(STATUS_CANDIDATE, CONF_LOW, rec, rec)

    bars = [m.bar for m in symmetric_nonzero]
    mn = min(bars)
    med = round(statistics.median(bars))

    if med <= noise:
        return result(STATUS_NOT_LETTERBOXED, CONF_NONE, 0, 0)
    if zero_present:
        # Some scenes fill the 16:9 frame — any crop would clip them (Case A).
        other_bar = round(statistics.median(bars))
        other_ar = aspect_label(width, height - 2 * other_bar)
        note = (
            "This movie alternates between full-frame (16:9) scenes and "
            f"{other_ar or f'{other_bar}px-bar'} scenes — no safe crop exists, so it's "
            "treated as not letterboxed."
        )
        return result(
            STATUS_VARIABLE_UNSAFE, CONF_LOW, 0, 0, variable_ar=True, variable_ar_note=note
        )

    # Bimodal/variable aspect-ratio check: two or more well-supported, mutually
    # disagreeing bar clusters mean genuinely different theatrical ARs in one
    # file (e.g. IMAX 1.90:1 expansion scenes vs 2.40:1 scope) — not just
    # measurement noise around a single bar size. Small/outlier clusters are
    # filtered out so a single anomalous frame can't trigger this (Case D).
    clusters = _cluster_bars(bars, config.LETTERBOX_VARIABLE_GAP_PX)
    min_cluster_size = max(2, round(config.LETTERBOX_VARIABLE_MIN_FRACTION * len(bars)))
    significant = [c for c in clusters if len(c) >= min_cluster_size]
    if len(significant) >= 2:
        reps = sorted((round(statistics.median(c)), len(c)) for c in significant)
        mn_rep = reps[0][0]
        parts = [
            f"{aspect_label(width, height - 2 * bar) or f'{bar}px'} ({count} samples)"
            for bar, count in reps
        ]
        note = (
            "Variable aspect ratio detected: alternates between "
            + ", ".join(parts)
            + f". Defaulting to the smaller crop ({mn_rep}px) to avoid clipping the "
            "wider-aspect-ratio scenes."
        )
        return result(
            STATUS_CANDIDATE,
            CONF_VARIABLE,
            mn_rep,
            mn_rep,
            variable_ar=True,
            variable_ar_note=note,
        )

    # Single-cluster letterboxed content. Base confidence on how many samples agree with the median
    # on both top and bottom (robust to single outlier frames) rather than
    # averaging asymmetric frames into plausible-looking symmetric crops.
    n_agree = sum(
        1
        for m in ok
        if is_symmetric(m) and abs(m.top_bar - med) <= agree and abs(m.bottom_bar - med) <= agree
    )
    agreement = n_agree / len(ok)
    if agreement >= 0.8:
        rec = med
        conf = CONF_HIGH
    elif (
        sum(
            1
            for m in ok
            if is_symmetric(m)
            and abs(m.top_bar - med) <= medium_spread
            and abs(m.bottom_bar - med) <= medium_spread
        )
        / len(ok)
        >= 0.5
    ):
        rec = med  # majority wins; use median so the dominant bar size is applied
        conf = CONF_MEDIUM
    else:
        rec = mn
        conf = CONF_LOW

    top_med = round(statistics.median([max(0, m.top_bar) for m in ok]))
    bottom_med = round(statistics.median([max(0, m.bottom_bar) for m in ok]))

    if asymmetric_count and config.LETTERBOX_ASYMMETRIC:
        return result(STATUS_CANDIDATE, conf, top_med, bottom_med)
    if asymmetric_fraction >= _ASYM_LOW_FRACTION:
        conf = CONF_LOW
    elif asymmetric_count:
        conf = downgrade(conf)
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
    codec: str | None = None,
    pix_fmt: str | None = None,
    is_tv: bool = False,
    thorough: bool = False,
    method: str | None = None,
    config: Settings = settings,
) -> DetectionResult:
    """Sample the file, measure each window, and build the consensus verdict."""
    path = str(path)
    method = (method or config.LETTERBOX_DETECT_METHOD).lower()
    cropdetect_limit = cropdetect_limit_for(color_transfer, config=config)

    minutes = sample_minutes(duration_s, is_tv=is_tv, thorough=thorough, config=config)
    measurements: list[WindowMeasurement] = []
    consecutive_clear = 0
    nvdec_decoder = _nvdec_decoder_for(codec, config=config) if method == "cropdetect" else None
    benchmark_key = (codec or "", pix_fmt or "", width, height)
    for minute in minutes:
        if method == "trim":
            m = measure_window_trim(path, minute, height, config=config)
        else:
            use_nvdec = bool(nvdec_decoder and _NVDEC_BENCHMARKS.get(benchmark_key, False))
            if nvdec_decoder and benchmark_key not in _NVDEC_BENCHMARKS:
                cpu = measure_window_cropdetect(
                    path, minute, height, config=config, cropdetect_limit=cropdetect_limit
                )
                gpu = measure_window_cropdetect(
                    path,
                    minute,
                    height,
                    config=config,
                    cropdetect_limit=cropdetect_limit,
                    nvdec_decoder=nvdec_decoder,
                    pix_fmt=pix_fmt,
                )
                matching = (
                    cpu.ok
                    and gpu.ok
                    and abs(cpu.top_bar - gpu.top_bar) <= config.LETTERBOX_AGREE_PX
                    and abs(cpu.bottom_bar - gpu.bottom_bar) <= config.LETTERBOX_AGREE_PX
                )
                faster = bool(
                    cpu.elapsed_ms and gpu.elapsed_ms and gpu.elapsed_ms < cpu.elapsed_ms * 0.9
                )
                _NVDEC_BENCHMARKS[benchmark_key] = matching and faster
                m = gpu if matching and faster else cpu
            else:
                m = measure_window_cropdetect(
                    path,
                    minute,
                    height,
                    config=config,
                    cropdetect_limit=cropdetect_limit,
                    nvdec_decoder=nvdec_decoder if use_nvdec else None,
                    pix_fmt=pix_fmt,
                )
                if use_nvdec and not m.ok:
                    _NVDEC_BENCHMARKS[benchmark_key] = False
                    m = measure_window_cropdetect(
                        path, minute, height, config=config, cropdetect_limit=cropdetect_limit
                    )
        measurements.append(m)
        if not thorough and m.ok and m.bar <= config.LETTERBOX_NOISE_PX:
            consecutive_clear += 1
            if consecutive_clear >= config.LETTERBOX_EARLY_STOP_WINDOWS:
                break
        elif m.ok:
            consecutive_clear = 0

    resolved_method = (
        "cropdetect_nvdec" if any(m.backend == "nvdec" for m in measurements) else method
    )
    return consensus(
        measurements, width=width, height=height, method=resolved_method, config=config
    )


def result_to_dict(result: DetectionResult) -> dict:
    """Plain dict for JSON persistence / API responses."""
    return asdict(result)
