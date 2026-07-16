"""Pure permanent letterbox re-encode planning and validation helpers."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import shutil
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from marquee.config import settings
from marquee.core.media_files import ResolvedMediaFile
from marquee.media import binaries
from marquee.media.concurrency import gated
from marquee.models import MediaFile

logger = logging.getLogger(__name__)

_STDERR_TAIL_MAX_LINES = 200
_STDERR_TAIL_MAX_BYTES = 64 * 1024
_PROCESS_TERMINATE_GRACE_SECONDS = 5.0


class ReencodePlanError(Exception):
    """A permanent re-encode plan cannot be created."""

    def __init__(self, code: str, message: str, warnings: list[dict] | None = None):
        self.code = code
        self.warnings = warnings or []
        super().__init__(message)











@dataclass
class SourceVideo:
    codec: str | None
    width: int
    height: int
    pix_fmt: str | None
    color_transfer: str | None
    color_primaries: str | None
    color_space: str | None
    duration_s: float | None
    has_hdr: bool
    has_dovi: bool
    dovi_profile: int | None
    dovi_level: int | None
    dovi_el_present: bool | None
    dovi_bl_signal_compatibility_id: int | None
    video_streams: int
    audio_streams: int
    subtitle_streams: int
    attachment_streams: int


def _ffprobe_json(path: Path | str) -> dict | None:
    if binaries.resolve("ffprobe") is None:
        return None
    result = binaries.run(
        "ffprobe",
        [
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            "-show_chapters",
            str(path),
        ],
        timeout=60.0,
    )
    if not result.ok:
        return None
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return None


def _walk_values(obj):
    if isinstance(obj, dict):
        yield obj
        for value in obj.values():
            yield from _walk_values(value)
    elif isinstance(obj, list):
        for item in obj:
            yield from _walk_values(item)


def _find_int(obj, *keys: str) -> int | None:
    for node in _walk_values(obj):
        for key in keys:
            if key not in node:
                continue
            try:
                return int(node[key])
            except (TypeError, ValueError):
                continue
    return None


def _find_bool(obj, *keys: str) -> bool | None:
    value = _find_int(obj, *keys)
    if value is None:
        return None
    return bool(value)


def inspect_source(path: Path | str) -> SourceVideo | None:
    data = _ffprobe_json(path)
    if data is None:
        return None
    streams = data.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if video is None:
        return None
    transfer = video.get("color_transfer")
    pix_fmt = video.get("pix_fmt")
    side_data = json.dumps(video.get("side_data_list", []), sort_keys=True).lower()
    whole_video = json.dumps(video, sort_keys=True).lower()
    has_dovi = "dovi" in whole_video or "dolby vision" in whole_video
    dovi_profile = _find_int(video, "dv_profile", "dovi_profile")
    dovi_level = _find_int(video, "dv_level", "dovi_level")
    dovi_el_present = _find_bool(video, "el_present_flag", "dv_el_present_flag")
    dovi_bl_signal_compatibility_id = _find_int(
        video,
        "dv_bl_signal_compatibility_id",
        "bl_signal_compatibility_id",
    )
    has_hdr = (
        transfer in {"smpte2084", "arib-std-b67"}
        or bool(video.get("mastering_display_metadata"))
        or "mastering display" in side_data
        or "content light" in side_data
        or has_dovi
    )
    duration = None
    raw_duration = data.get("format", {}).get("duration") or video.get("duration")
    if raw_duration is not None:
        try:
            duration = float(raw_duration)
        except (TypeError, ValueError):
            duration = None
    return SourceVideo(
        codec=video.get("codec_name"),
        width=int(video.get("width") or 0),
        height=int(video.get("height") or 0),
        pix_fmt=pix_fmt,
        color_transfer=transfer,
        color_primaries=video.get("color_primaries"),
        color_space=video.get("color_space"),
        duration_s=duration,
        has_hdr=has_hdr,
        has_dovi=has_dovi,
        dovi_profile=dovi_profile,
        dovi_level=dovi_level,
        dovi_el_present=dovi_el_present,
        dovi_bl_signal_compatibility_id=dovi_bl_signal_compatibility_id,
        video_streams=sum(1 for s in streams if s.get("codec_type") == "video"),
        audio_streams=sum(1 for s in streams if s.get("codec_type") == "audio"),
        subtitle_streams=sum(1 for s in streams if s.get("codec_type") == "subtitle"),
        attachment_streams=sum(1 for s in streams if s.get("codec_type") == "attachment"),
    )


def ffmpeg_encoders() -> set[str]:
    if binaries.resolve("ffmpeg") is None:
        return set()
    result = binaries.run("ffmpeg", ["-hide_banner", "-encoders"], timeout=30.0)
    if not result.ok:
        return set()
    encoders: set[str] = set()
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0].startswith("V"):
            encoders.add(parts[1])
    return encoders


def ffmpeg_hwaccels() -> set[str]:
    """Return hardware acceleration methods advertised by the FFmpeg build."""
    if binaries.resolve("ffmpeg") is None:
        return set()
    result = binaries.run("ffmpeg", ["-hide_banner", "-hwaccels"], timeout=30.0)
    if not result.ok:
        return set()
    return {
        line.strip()
        for line in result.stdout.splitlines()
        if line.strip() and not line.lower().startswith("hardware acceleration methods")
    }


def ffmpeg_decoders() -> set[str]:
    """Return video decoder names advertised by the FFmpeg build."""
    if binaries.resolve("ffmpeg") is None:
        return set()
    result = binaries.run("ffmpeg", ["-hide_banner", "-decoders"], timeout=30.0)
    if not result.ok:
        return set()
    decoders: set[str] = set()
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0].startswith("V"):
            decoders.add(parts[1])
    return decoders


def _clean_ffmpeg_error(stderr: str, source: Path | str) -> str:
    """Produce a concise, frontend-safe CUDA failure explanation."""
    lines = [line.strip() for line in stderr.splitlines() if line.strip()]
    message = lines[-1] if lines else "FFmpeg could not initialize NVIDIA acceleration."
    return message.replace(str(source), "<source>")[:300]


def nvidia_acceleration_plan(source: Path, source_codec: str | None, encoder: dict) -> dict:
    """Preflight a zero-copy NVDEC -> NVENC path for one concrete source."""
    disabled = {
        "enabled": False,
        "mode": "cpu_decode_crop",
        "decoder": None,
        "reason": None,
    }
    if settings.LETTERBOX_REENCODE_NVIDIA_ACCELERATION == "off":
        return {**disabled, "reason": "NVIDIA acceleration is disabled by configuration."}
    if encoder.get("family") != "nvidia":
        return {**disabled, "reason": "Selected encoder does not use NVIDIA NVENC."}
    decoder = _NVDEC_DECODERS.get(source_codec or "")
    if decoder is None:
        return {
            **disabled,
            "reason": f"No NVIDIA decoder mapping is available for source codec {source_codec or 'unknown'}.",
        }
    if "cuda" not in ffmpeg_hwaccels():
        return {**disabled, "reason": "This FFmpeg build does not advertise CUDA acceleration."}
    if decoder not in ffmpeg_decoders():
        return {**disabled, "reason": f"This FFmpeg build does not provide {decoder}."}

    # Listing a decoder does not prove it can decode this profile on the active
    # GPU. Decode one frame into a CUDA frame before committing a job plan.
    try:
        result = binaries.run(
            "ffmpeg",
            [
                "-hide_banner",
                "-loglevel",
                "error",
                "-hwaccel",
                "cuda",
                "-hwaccel_output_format",
                "cuda",
                "-c:v:0",
                decoder,
                "-i",
                str(source),
                "-map",
                "0:v:0",
                "-frames:v",
                "1",
                "-f",
                "null",
                "-",
            ],
            timeout=30.0,
        )
    except binaries.BinaryError as exc:
        return {**disabled, "reason": f"NVIDIA acceleration preflight failed: {exc}"}
    if not result.ok:
        logger.info("NVIDIA acceleration preflight unavailable: %s", result.stderr)
        return {**disabled, "reason": _clean_ffmpeg_error(result.stderr, source)}
    return {
        "enabled": True,
        "mode": "nvidia_zero_copy",
        "decoder": decoder,
        "reason": None,
    }


def _target_codec(source_codec: str | None) -> str:
    if source_codec in {"h264", "avc1"}:
        return "h264"
    return "hevc"


# Known video encoders → (family, default CQ/CRF, output codec). Drives both the
# auto-selection preference order and validation of an explicit user override.
_ENCODER_TABLE: dict[str, tuple[str, int, str]] = {
    "h264_nvenc": ("nvidia", 16, "h264"),
    "hevc_nvenc": ("nvidia", 16, "hevc"),
    "h264_qsv": ("intel_qsv", 18, "h264"),
    "hevc_qsv": ("intel_qsv", 18, "hevc"),
    "h264_vaapi": ("intel_vaapi", 18, "h264"),
    "hevc_vaapi": ("intel_vaapi", 18, "hevc"),
    "libx264": ("cpu", 16, "h264"),
    "libx265": ("cpu", 16, "hevc"),
}

# Per-family default speed/quality preset (None = encoder applies its own default).
_FAMILY_DEFAULT_PRESET: dict[str, str | None] = {
    "nvidia": "p7",
    "cpu": "slow",
    "intel_qsv": None,
    "intel_vaapi": None,
}

# Preference order per target codec (best hardware first, CPU last).
_CANDIDATES: dict[str, list[str]] = {
    "h264": ["h264_nvenc", "h264_qsv", "h264_vaapi", "libx264"],
    "hevc": ["hevc_nvenc", "hevc_qsv", "hevc_vaapi", "libx265"],
}

# FFmpeg's CUDA decoder names do not always match the codec name verbatim.
# Keep this deliberately small and capability-gated: a listed decoder is still
# preflighted against the selected source before it is used for a real encode.
_NVDEC_DECODERS: dict[str, str] = {
    "h264": "h264_cuvid",
    "hevc": "hevc_cuvid",
    "av1": "av1_cuvid",
    "vp9": "vp9_cuvid",
    "vp8": "vp8_cuvid",
    "mpeg2video": "mpeg2_cuvid",
    "mpeg4": "mpeg4_cuvid",
    "vc1": "vc1_cuvid",
    "mjpeg": "mjpeg_cuvid",
}


def choose_encoder(
    source_codec: str | None,
    *,
    allow_cpu: bool,
    requested_encoder: str | None = None,
    requested_quality: int | None = None,
    requested_codec: str | None = None,
    requested_preset: str | None = None,
) -> dict:
    available = ffmpeg_encoders()

    def _build(encoder: str, family: str, default_quality: int, codec: str) -> dict:
        return {
            "codec": codec,
            "encoder": encoder,
            "family": family,
            "quality": requested_quality if requested_quality is not None else default_quality,
            "preset": requested_preset or _FAMILY_DEFAULT_PRESET.get(family),
            "available_encoders": sorted(available),
            "used_cpu_fallback": family == "cpu",
        }

    # Explicit encoder override: honor it directly if known and available.
    if requested_encoder and requested_encoder != "auto":
        spec = _ENCODER_TABLE.get(requested_encoder)
        if spec is None:
            raise ReencodePlanError(
                "encoder_unavailable", f"Unknown encoder {requested_encoder!r}."
            )
        family, default_quality, codec = spec
        if requested_encoder not in available:
            raise ReencodePlanError(
                "encoder_unavailable",
                f"Encoder {requested_encoder!r} is not available in this FFmpeg build.",
            )
        if family == "cpu" and not allow_cpu:
            raise ReencodePlanError(
                "encoder_unavailable",
                "CPU encoding is disabled; enable CPU fallback to use a software encoder.",
            )
        return _build(requested_encoder, family, default_quality, codec)

    # Auto-select by preference. Target codec follows an explicit codec override,
    # else preserves the source codec.
    target = requested_codec if requested_codec in {"h264", "hevc"} else _target_codec(source_codec)
    for encoder in _CANDIDATES[target]:
        family, default_quality, codec = _ENCODER_TABLE[encoder]
        if encoder in available and (family != "cpu" or allow_cpu):
            return _build(encoder, family, default_quality, codec)
    raise ReencodePlanError(
        "encoder_unavailable",
        "No supported FFmpeg encoder was found for permanent letterbox re-encode.",
    )


def _source_key(db_row: MediaFile | None, resolved: ResolvedMediaFile) -> str:
    raw = db_row.source_key if db_row else f"file-{resolved.media_file_id}"
    return raw.replace(":", "_").replace("/", "_").replace("\\", "_")


def _managed_root(source: Path) -> Path:
    resolved = source.resolve()
    for candidate in settings.effective_media_roots:
        candidate = Path(candidate).resolve()
        if resolved.is_relative_to(candidate):
            return candidate / ".marquee"
    return source.parent.parent / ".marquee"








async def _mkdir_with_retry(
    path: Path, *, boundary: Path, attempts: int = 3, delay_s: float = 0.3
) -> None:
    """Create a directory tree under ``boundary``, tolerating cross-account ownership.

    The candidates/backups tree lives on the same mount as the source media
    (often a mergerfs union of multiple disks) so the final commit can be a
    same-filesystem rename instead of a multi-GB copy, and it can be written
    to by more than one OS account that share a common group (e.g. a human
    dev account and an automation account). The retry loop covers genuinely
    transient OSErrors (NFS hiccups, disk contention); it does *not* help
    when a directory is owned by the other account with no group-write bit,
    so on success we best-effort chmod every level we just touched, up to
    ``boundary``, to setgid + group-write. We can only chmod directories we
    own — pre-existing ones owned by the other account are left alone here
    and need a one-time manual fix (chmod/chgrp) outside the app.
    """
    for attempt in range(1, attempts + 1):
        try:
            path.mkdir(parents=True, exist_ok=True)
            break
        except (PermissionError, OSError):
            if attempt == attempts:
                raise ReencodePlanError(
                    "candidate_dir_unavailable",
                    f"could not create working directory {path} "
                    "(storage mount issue — check media volume permissions/mounts)",
                ) from None
            await asyncio.sleep(delay_s)
    current = path
    while True:
        with contextlib.suppress(PermissionError, FileNotFoundError):
            current.chmod(0o2775)
        if current == boundary or current.parent == current:
            break
        current = current.parent


def dovi_info(source: SourceVideo | None) -> dict:
    if source is None:
        return {
            "present": False,
            "profile": None,
            "level": None,
            "el_present": None,
            "bl_signal_compatibility_id": None,
            "preservation": {"status": "unknown", "supported": False, "reason": "probe_failed"},
        }
    preservation = _dovi_preservation(source, _target_codec(source.codec))
    return {
        "present": source.has_dovi,
        "profile": source.dovi_profile,
        "level": source.dovi_level,
        "el_present": source.dovi_el_present,
        "bl_signal_compatibility_id": source.dovi_bl_signal_compatibility_id,
        "preservation": preservation,
    }


def _dovi_preservation(source: SourceVideo, target_codec: str) -> dict:
    if not source.has_dovi:
        return {"status": "not_present", "supported": False, "reason": None}
    if source.codec != "hevc" or target_codec != "hevc":
        return {
            "status": "unsupported_codec",
            "supported": False,
            "reason": "Dolby Vision preservation requires HEVC source and HEVC output.",
        }
    if source.dovi_profile in {5, 8}:
        if binaries.resolve("dovi_tool") is None:
            return {
                "status": "tool_missing",
                "supported": False,
                "reason": "dovi_tool is required to extract and inject Dolby Vision RPU metadata.",
            }
        return {
            "status": "preserve_planned",
            "supported": True,
            "reason": "Dolby Vision RPU will be extracted with dovi_tool, cropped, and injected into the encoded HEVC stream.",
        }
    if source.dovi_profile == 7:
        return {
            "status": "unsupported_profile",
            "supported": False,
            "reason": "Dolby Vision profile 7 cannot be fully preserved by this single-layer re-encode path.",
        }
    return {
        "status": "unsupported_profile",
        "supported": False,
        "reason": "Dolby Vision profile could not be identified as a safely preservable profile.",
    }


def _dovi_plan(source: SourceVideo, target_codec: str) -> tuple[dict, list[dict]]:
    preservation = _dovi_preservation(source, target_codec)
    if not source.has_dovi:
        return preservation, []
    if preservation["supported"]:
        return preservation, [
            {
                "code": "dovi_preserve_planned",
                "message": "Dolby Vision was detected and dovi_tool will be used to preserve RPU metadata in the re-encoded output.",
                "requires_confirmation": False,
            }
        ]
    warning = {
        "code": preservation["status"],
        "message": preservation["reason"]
        or "Dolby Vision cannot be safely preserved for this file.",
        "requires_confirmation": True,
    }
    if settings.LETTERBOX_REENCODE_STRICT_DOVI:
        raise ReencodePlanError("dovi_unsupported", warning["message"], [warning])
    return preservation, [warning]


async def build_plan(
    db: AsyncSession,
    resolved: ResolvedMediaFile,
    *,
    top: int,
    bottom: int,
    allow_cpu_fallback: bool | None = None,
    encoder: str | None = None,
    quality: int | None = None,
    preset: str | None = None,
    codec: str | None = None,
) -> dict:
    if resolved.path.suffix.lower() != ".mkv":
        raise ReencodePlanError(
            "not_mkv", "Permanent letterbox re-encode supports MKV files only for now."
        )
    if top < 0 or bottom < 0 or (top == 0 and bottom == 0):
        raise ReencodePlanError("missing_crop", "A non-zero crop recommendation is required.")
    if quality is not None and not 0 <= quality <= 51:
        raise ReencodePlanError("invalid_quality", "Quality (CQ/CRF) must be between 0 and 51.")
    if codec is not None and codec not in {"preserve", "h264", "hevc"}:
        raise ReencodePlanError("invalid_codec", "Codec must be 'preserve', 'h264', or 'hevc'.")
    # ffprobe/ffmpeg are blocking subprocesses — offload so planning never
    # freezes the event loop (the API is single-worker).
    source = await gated(inspect_source, resolved.path)
    if source is None:
        raise ReencodePlanError("probe_failed", "Could not inspect the source video.")
    if source.height - top - bottom <= 0:
        raise ReencodePlanError("invalid_crop", "Crop values remove the full video height.")

    allow_cpu = (
        settings.LETTERBOX_REENCODE_ALLOW_CPU_FALLBACK
        if allow_cpu_fallback is None
        else allow_cpu_fallback
    )
    requested_codec = None if codec in {None, "preserve"} else codec
    encoder_plan = await gated(
        choose_encoder,
        source.codec,
        allow_cpu=allow_cpu,
        requested_encoder=encoder,
        requested_quality=quality,
        requested_codec=requested_codec,
        requested_preset=preset,
    )
    acceleration = await gated(
        nvidia_acceleration_plan,
        resolved.path,
        source.codec,
        encoder_plan,
    )
    dovi, warnings = _dovi_plan(source, encoder_plan["codec"])
    if encoder_plan["used_cpu_fallback"]:
        warnings.append(
            {
                "code": "cpu_fallback",
                "message": "No supported GPU encoder was selected; this job will use CPU encoding and may take much longer.",
                "requires_confirmation": False,
            }
        )
    if encoder_plan["family"] == "nvidia" and not acceleration["enabled"]:
        warnings.append(
            {
                "code": "nvidia_acceleration_unavailable",
                "message": (
                    "NVIDIA decode/crop acceleration is unavailable; this encode will use "
                    f"CPU decode/crop. {acceleration['reason']}"
                ),
                "requires_confirmation": False,
            }
        )
    free = shutil.disk_usage(resolved.path.parent).free
    estimated_temp = resolved.size_bytes
    if free < estimated_temp:
        warnings.append(
            {
                "code": "low_disk_space",
                "message": "Free space is lower than the source file size; the encode may fail before producing a candidate.",
                "requires_confirmation": True,
            }
        )
    return {
        "operation": "letterbox_reencode",
        "method": "permanent_reencode",
        "crop": {"top": top, "bottom": bottom, "output_height": source.height - top - bottom},
        "source": {
            "size_bytes": resolved.size_bytes,
            "signature": resolved.signature,
            "codec": source.codec,
            "width": source.width,
            "height": source.height,
            "duration_seconds": source.duration_s,
            "pix_fmt": source.pix_fmt,
            "color_transfer": source.color_transfer,
            "color_primaries": source.color_primaries,
            "color_space": source.color_space,
            "has_hdr": source.has_hdr,
            "has_dovi": source.has_dovi,
            "dovi_profile": source.dovi_profile,
            "dovi_level": source.dovi_level,
            "dovi_el_present": source.dovi_el_present,
            "dovi_bl_signal_compatibility_id": source.dovi_bl_signal_compatibility_id,
            "video_streams": source.video_streams,
            "audio_streams": source.audio_streams,
            "subtitle_streams": source.subtitle_streams,
            "attachment_streams": source.attachment_streams,
        },
        "encoder": encoder_plan,
        "acceleration": acceleration,
        "hdr": {"status": "preserve_required" if source.has_hdr else "not_present"},
        "dovi": {
            **dovi,
            "profile": source.dovi_profile,
            "level": source.dovi_level,
            "el_present": source.dovi_el_present,
            "bl_signal_compatibility_id": source.dovi_bl_signal_compatibility_id,
        },
        "storage": {
            "estimated_temp_bytes": estimated_temp,
            "free_bytes": free,
            "original_preserved_by_default": True,
            "replace_original_after_review": True,
        },
        "warnings": warnings,
        "confirmation_required": True,
        "input_signature": resolved.signature,
    }


def build_ffmpeg_args(source: Path, output: Path, plan: dict) -> list[str]:
    encoder = plan["encoder"]["encoder"]
    family = plan["encoder"]["family"]
    top = int(plan["crop"]["top"])
    bottom = int(plan["crop"]["bottom"])
    acceleration = plan.get("acceleration") or {}
    use_nvidia_zero_copy = bool(acceleration.get("enabled")) and family == "nvidia"

    args = ["-y", "-hide_banner", "-nostdin"]
    if use_nvidia_zero_copy:
        # CUVID crops while producing CUDA frames, so NVENC receives GPU-resident
        # frames directly. Do not add a CPU filter or format conversion here.
        args.extend(
            [
                "-hwaccel",
                "cuda",
                "-hwaccel_output_format",
                "cuda",
                "-c:v:0",
                str(acceleration["decoder"]),
                "-crop",
                f"{top}x{bottom}x0x0",
            ]
        )
    args.extend(
        [
            "-i",
            str(source),
            "-map",
            "0",
            "-map_metadata",
            "0",
            "-map_chapters",
            "0",
            "-copy_unknown",
            "-max_muxing_queue_size",
            "4096",
            "-c",
            "copy",
            "-c:v:0",
            encoder,
        ]
    )
    if not use_nvidia_zero_copy:
        crop_filter = f"crop=iw:ih-{top + bottom}:0:{top}"
        if plan["source"].get("has_hdr") or "10" in str(plan["source"].get("pix_fmt") or ""):
            crop_filter = f"{crop_filter},format=p010le"
        args.extend(["-filter:v:0", crop_filter])
    quality = str(plan["encoder"]["quality"])
    preset = plan["encoder"].get("preset")
    if family == "nvidia":
        args.extend(["-preset", preset or "p7", "-tune", "hq", "-rc", "vbr", "-cq", quality])
    elif family in {"intel_qsv", "intel_vaapi"}:
        args.extend(["-global_quality", quality])
    elif encoder in {"libx265", "libx264"}:
        args.extend(["-preset", preset or "slow", "-crf", quality])
    if plan["source"].get("color_primaries"):
        args.extend(["-color_primaries", str(plan["source"]["color_primaries"])])
    if plan["source"].get("color_transfer"):
        args.extend(["-color_trc", str(plan["source"]["color_transfer"])])
    if plan["source"].get("color_space"):
        args.extend(["-colorspace", str(plan["source"]["color_space"])])
    args.extend(["-progress", "pipe:1", "-nostats", str(output)])
    return args


def build_hevc_extract_args(source: Path, output: Path) -> list[str]:
    return [
        "-y",
        "-hide_banner",
        "-nostdin",
        "-i",
        str(source),
        "-map",
        "0:v:0",
        "-c:v",
        "copy",
        "-bsf:v",
        "hevc_mp4toannexb",
        "-f",
        "hevc",
        str(output),
    ]


def build_dovi_remux_args(encoded_mkv: Path, injected_hevc: Path, output: Path) -> list[str]:
    return [
        "-y",
        "-hide_banner",
        "-nostdin",
        "-i",
        str(encoded_mkv),
        "-i",
        str(injected_hevc),
        "-map",
        "1:v:0",
        "-map",
        "0:a?",
        "-map",
        "0:s?",
        "-map",
        "0:t?",
        "-map",
        "0:d?",
        "-map_metadata",
        "0",
        "-map_chapters",
        "0",
        "-copy_unknown",
        "-c",
        "copy",
        str(output),
    ]


def _parse_progress(line: str, duration_s: float | None, values: dict[str, str]) -> dict | None:
    if "=" not in line:
        return None
    key, value = line.strip().split("=", 1)
    values[key] = value
    if key != "progress":
        return None
    try:
        out_seconds = int(values["out_time_ms"]) / 1_000_000
    except (KeyError, ValueError):
        return None
    percent = None
    if duration_s and duration_s > 0:
        percent = max(0, min(100, round((out_seconds / duration_s) * 100, 1)))
    progress = {"out_time_seconds": round(out_seconds, 1), "percent": percent}
    if values.get("fps") not in {None, "N/A"}:
        with contextlib.suppress(ValueError):
            progress["fps"] = round(float(values["fps"]), 2)
    if values.get("speed") not in {None, "N/A"}:
        with contextlib.suppress(ValueError):
            progress["speed"] = round(float(values["speed"].removesuffix("x")), 3)
    return progress























async def _run_checked(
    binary_name: str,
    args: list[str],
    *,
    db: AsyncSession | None = None,
    job: object | None = None,
    timeout: float | None = 3600,
) -> None:
    raise RuntimeError("legacy re-encode process execution is retired")


async def _piped_ffmpeg_to_dovi(
    ffmpeg_args: list[str],
    dovi_args: list[str],
    *,
    timeout: float = 3600,
) -> None:
    raise RuntimeError("legacy re-encode Dolby Vision piping is retired")


async def _extract_rpu_piped(
    source_mkv: Path, rpu_out: Path, *, timeout: float = 3600
) -> None:
    raise RuntimeError("legacy re-encode RPU extraction is retired")








def validate_candidate(
    source_path: Path, out_path: Path, plan: dict, source: SourceVideo
) -> list[str]:
    problems: list[str] = []
    out = inspect_source(out_path)
    if out is None:
        return ["output not probeable"]
    expected_height = int(plan["crop"]["output_height"])
    if out.width != source.width or out.height != expected_height:
        problems.append(
            f"unexpected dimensions: {out.width}x{out.height}, expected {source.width}x{expected_height}"
        )
    if out.audio_streams != source.audio_streams:
        problems.append(
            f"audio stream count changed: {source.audio_streams} -> {out.audio_streams}"
        )
    if out.subtitle_streams != source.subtitle_streams:
        problems.append(
            f"subtitle stream count changed: {source.subtitle_streams} -> {out.subtitle_streams}"
        )
    if out.attachment_streams != source.attachment_streams:
        problems.append(
            f"attachment stream count changed: {source.attachment_streams} -> {out.attachment_streams}"
        )
    if source.duration_s and out.duration_s and abs(source.duration_s - out.duration_s) > 2.0:
        problems.append(f"duration drifted: {source.duration_s:.1f}s -> {out.duration_s:.1f}s")
    if source.has_hdr and not out.has_hdr:
        problems.append("HDR markers were not detected in the output")
    if plan.get("dovi", {}).get("supported") and not out.has_dovi:
        problems.append("Dolby Vision metadata was not detected in the output")
    return problems
