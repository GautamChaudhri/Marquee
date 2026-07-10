"""Permanent letterbox re-encode planning, execution, and artifact lifecycle."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import shutil
import time
from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from marquee.config import settings
from marquee.core.jobs import cancel_registry
from marquee.core.jobs.cancel_registry import JobCancelledError
from marquee.core.jobs.child_tracking import clear_child_pid, record_child_pid
from marquee.core.media_files import (
    ResolvedMediaFile,
    compute_signature,
    resolve_media_file,
)
from marquee.database import _get_session_factory
from marquee.media import binaries, letterbox_detect
from marquee.media.concurrency import gated
from marquee.models import (
    EpisodeMediaFile,
    LetterboxReencodeArtifact,
    LetterboxState,
    MediaFile,
    MediaJob,
)

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


async def _episode_ids_for_media_file(db: AsyncSession, media_file_id: int | None) -> list[int]:
    if media_file_id is None:
        return []
    return list(
        (
            await db.execute(
                select(EpisodeMediaFile.episode_id)
                .where(EpisodeMediaFile.media_file_id == media_file_id)
                .order_by(EpisodeMediaFile.episode_id)
            )
        ).scalars()
    )


async def _states_for_artifact(
    db: AsyncSession, artifact: LetterboxReencodeArtifact
) -> list[LetterboxState]:
    if artifact.media_type == "episode":
        return list(
            (
                await db.execute(
                    select(LetterboxState)
                    .join(EpisodeMediaFile, EpisodeMediaFile.episode_id == LetterboxState.episode_id)
                    .where(
                        LetterboxState.media_type == "episode",
                        EpisodeMediaFile.media_file_id == artifact.media_file_id,
                    )
                    .order_by(LetterboxState.episode_id)
                )
            ).scalars()
        )
    state = (
        await db.execute(
            select(LetterboxState).where(
                LetterboxState.media_type == artifact.media_type,
                LetterboxState.movie_id == artifact.movie_id,
                LetterboxState.episode_id == artifact.episode_id,
            )
        )
    ).scalar_one_or_none()
    return [state] if state is not None else []


async def _raise_if_cancel_requested(
    db: AsyncSession,
    job: MediaJob,
    *,
    rpu_task: asyncio.Task | None = None,
    cleanup_paths: list[Path] | None = None,
) -> None:
    await db.refresh(job, ["cancel_requested"])
    cancel_event = cancel_registry.get(job.job_id)
    registry_cancelled = cancel_event is not None and cancel_event.is_set()
    if not job.cancel_requested and not registry_cancelled:
        return
    if rpu_task is not None and not rpu_task.done():
        rpu_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await rpu_task
    for path in cleanup_paths or []:
        path.unlink(missing_ok=True)
    if registry_cancelled and not job.cancel_requested:
        raise JobCancelledError("letterbox re-encode interrupted")
    raise ReencodePlanError("cancelled", "letterbox re-encode cancelled")


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
    resolved = str(source.resolve())
    for candidate in settings.effective_media_roots:
        if resolved.startswith(str(candidate)):
            return candidate / ".marquee"
    return source.parent.parent / ".marquee"


def candidate_output_path(source: Path, source_key: str, job_id: str) -> Path:
    return _managed_root(source) / "letterbox" / "candidates" / source_key / job_id / source.name


def saved_original_path(source: Path, source_key: str, job_id: str) -> Path:
    return _managed_root(source) / "backups" / source_key / job_id / source.name


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
            "path": str(resolved.path),
            "size_bytes": resolved.size_bytes,
            "signature": resolved.signature,
            "codec": source.codec,
            "width": source.width,
            "height": source.height,
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


async def _drain_stderr(stream: asyncio.StreamReader) -> str:
    """Continuously drain stderr while retaining a bounded diagnostic tail."""
    tail: deque[str] = deque()
    tail_bytes = 0
    pending = ""

    def append(chunk: str) -> None:
        nonlocal tail_bytes
        tail.append(chunk)
        tail_bytes += len(chunk.encode(errors="replace"))
        while len(tail) > _STDERR_TAIL_MAX_LINES or tail_bytes > _STDERR_TAIL_MAX_BYTES:
            tail_bytes -= len(tail.popleft().encode(errors="replace"))

    while chunk := await stream.read(4096):
        pending += chunk.decode(errors="replace")
        lines = pending.splitlines(keepends=True)
        pending = lines.pop() if lines and not lines[-1].endswith(("\n", "\r")) else ""
        for line in lines:
            append(line)
    if pending:
        append(pending)
    return "".join(tail)


async def _terminate_process(
    proc: asyncio.subprocess.Process,
    *,
    grace_seconds: float = _PROCESS_TERMINATE_GRACE_SECONDS,
) -> None:
    """Terminate a child process without allowing cleanup to wait forever."""
    if proc.returncode is not None:
        return
    with contextlib.suppress(ProcessLookupError):
        proc.terminate()
    try:
        await asyncio.wait_for(proc.wait(), timeout=grace_seconds)
        return
    except TimeoutError:
        logger.warning("child process %s ignored SIGTERM; sending SIGKILL", proc.pid)
    with contextlib.suppress(ProcessLookupError):
        proc.kill()
    with contextlib.suppress(TimeoutError):
        await asyncio.wait_for(proc.wait(), timeout=grace_seconds)


async def _finish_stderr_drain(stderr_task: asyncio.Task[str]) -> str:
    try:
        return await stderr_task
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 - diagnostics must not mask the encode result
        logger.warning("failed to drain re-encode stderr", exc_info=True)
        return ""


async def _cancelled_encode_cleanup(
    proc: asyncio.subprocess.Process,
    output: Path | None,
    stderr_task: asyncio.Task[str],
) -> None:
    """Complete critical child cleanup after the encode task is cancelled."""
    await _terminate_process(proc)
    if output is not None:
        output.unlink(missing_ok=True)
    await _finish_stderr_drain(stderr_task)
    await clear_child_pid(proc.pid)


async def _media_job_cancel_requested(job_id: str) -> bool:
    """Read cancellation through a fresh, short-lived session."""
    factory = _get_session_factory()
    async with factory() as db:
        job = await db.get(MediaJob, job_id)
        return bool(job and job.cancel_requested)


async def _run_encode_attempt(
    db: AsyncSession,
    job: MediaJob,
    emit,
    source: Path,
    output: Path,
    plan: dict,
    source_info: SourceVideo,
) -> tuple[bool, str]:
    """Run one FFmpeg encode attempt and return its failure diagnostic, if any."""
    proc = await asyncio.create_subprocess_exec(
        binaries.resolve("ffmpeg") or "ffmpeg",
        *build_ffmpeg_args(source, output, plan),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    await record_child_pid(proc.pid)
    assert proc.stderr is not None
    stderr_task = asyncio.create_task(_drain_stderr(proc.stderr))
    cancellation_cleanup_started = False
    try:
        assert proc.stdout is not None
        # ffmpeg emits a progress packet several times per second. Publish every
        # packet live, but only persist it (and poll cancellation) about once/sec.
        last_persist = 0.0
        last_progress = time.monotonic()
        progress_values: dict[str, str] = {}
        while True:
            try:
                line = await asyncio.wait_for(
                    proc.stdout.readline(),
                    timeout=max(0.0, settings.JOB_ENCODE_STALL_SECONDS - (time.monotonic() - last_progress)),
                )
            except TimeoutError:
                await _terminate_process(proc)
                output.unlink(missing_ok=True)
                diagnostic = await _finish_stderr_drain(stderr_task)
                message = "FFmpeg stopped emitting progress"
                if diagnostic:
                    message = f"{message}: {diagnostic[-1000:]}"
                await emit(db, job.job_id, "encode", "stalled", message=message)
                raise ReencodePlanError("encode_stalled", diagnostic or message) from None
            if not line:
                break
            progress = _parse_progress(
                line.decode(errors="replace"), source_info.duration_s, progress_values
            )
            if progress is None:
                continue
            now = time.monotonic()
            last_progress = now
            if now - last_persist < 1.0:
                await emit(db, job.job_id, "encode", "running", progress=progress, persist=False)
                continue
            last_persist = now
            await emit(db, job.job_id, "encode", "running", progress=progress, persist=True)
            if await _media_job_cancel_requested(job.job_id):
                await _terminate_process(proc)
                output.unlink(missing_ok=True)
                raise ReencodePlanError("cancelled", "letterbox re-encode cancelled")
        await proc.wait()
        diagnostic = await _finish_stderr_drain(stderr_task)
        if proc.returncode != 0:
            output.unlink(missing_ok=True)
            return False, diagnostic
        return True, diagnostic
    except asyncio.CancelledError:
        cancellation_cleanup_started = True
        with contextlib.suppress(asyncio.CancelledError, TimeoutError):
            await asyncio.shield(
                asyncio.wait_for(
                    _cancelled_encode_cleanup(proc, output, stderr_task),
                    timeout=2 * _PROCESS_TERMINATE_GRACE_SECONDS + 2,
                )
            )
        raise
    finally:
        if not cancellation_cleanup_started:
            if proc.returncode is None:
                await _terminate_process(proc)
            await _finish_stderr_drain(stderr_task)
            await clear_child_pid(proc.pid)


async def execute_job(db: AsyncSession, job: MediaJob, emit) -> dict:
    request = json.loads(job.request_json) if job.request_json else {}
    plan = json.loads(job.plan_json) if job.plan_json else {}
    resolved = await resolve_media_file(db, job.media_file_id)
    if job.input_signature and resolved.signature != job.input_signature:
        raise ReencodePlanError("plan_stale", "file changed since the re-encode plan was created")

    media_row = await db.get(MediaFile, resolved.media_file_id)
    source_key = _source_key(media_row, resolved)
    out = candidate_output_path(resolved.path, source_key, job.job_id)
    await _mkdir_with_retry(out.parent, boundary=_managed_root(resolved.path))
    out.unlink(missing_ok=True)
    dovi_preserve = bool(plan.get("dovi", {}).get("supported"))
    ffmpeg_out = out
    encoded_out = out.with_name(f".{out.stem}.encoded{out.suffix}") if dovi_preserve else out
    if dovi_preserve:
        encoded_out.unlink(missing_ok=True)
        ffmpeg_out = encoded_out

    source_info = await gated(inspect_source, resolved.path)
    if source_info is None:
        raise ReencodePlanError("probe_failed", "could not inspect source before encoding")
    await _raise_if_cancel_requested(db, job, cleanup_paths=[out, encoded_out])

    # Start DOVI RPU extraction in parallel with the main encode (Fix C).
    # Both operations read the source file; RPU extraction is I/O-bound and
    # finishes well before the encode does, so the RPU will be ready by the
    # time the encode completes — making RPU extraction effectively free.
    rpu_task: asyncio.Task | None = None
    rpu_work: Path | None = None
    if dovi_preserve:
        rpu_work = encoded_out.parent / f".dovi-{job.job_id}"
        if rpu_work.exists():
            shutil.rmtree(rpu_work)
        rpu_work.mkdir(parents=True, exist_ok=True)
        rpu_path = rpu_work / "RPU.bin"
        rpu_task = asyncio.create_task(
            _extract_rpu_piped(resolved.path, rpu_path),
            name=f"dovi-rpu-{job.job_id}",
        )
        logger.info("DOVI RPU extraction started in parallel for %s", job.job_id)
    await _raise_if_cancel_requested(db, job, rpu_task=rpu_task, cleanup_paths=[out, encoded_out])

    acceleration = plan.get("acceleration") or {}
    acceleration_active = bool(acceleration.get("enabled"))
    pipeline_label = (
        "NVIDIA NVDEC \u2192 GPU crop \u2192 NVENC" if acceleration_active else "CPU decode/crop"
    )
    crop = plan.get("crop") or {}
    crop_label = f"crop top={crop.get('top')} bottom={crop.get('bottom')} \u2014 " if crop else ""
    await emit(
        db,
        job.job_id,
        "encode",
        "start",
        message=f"Re-encoding with {crop_label}{plan['encoder']['encoder']} ({pipeline_label})",
    )

    succeeded, diagnostic = await _run_encode_attempt(
        db, job, emit, resolved.path, ffmpeg_out, plan, source_info
    )
    await _raise_if_cancel_requested(db, job, rpu_task=rpu_task, cleanup_paths=[out, encoded_out])
    execution_acceleration = dict(acceleration)
    if not succeeded and acceleration_active:
        logger.warning(
            "NVIDIA zero-copy encode failed; retrying with CPU decode/crop: %s", diagnostic
        )
        fallback_reason = _clean_ffmpeg_error(diagnostic, resolved.path)
        fallback_plan = json.loads(json.dumps(plan))
        fallback_plan["acceleration"] = {
            "enabled": False,
            "mode": "cpu_decode_crop_fallback",
            "decoder": acceleration.get("decoder"),
            "reason": fallback_reason,
        }
        execution_acceleration = fallback_plan["acceleration"]
        await emit(
            db,
            job.job_id,
            "encode",
            "fallback",
            message=f"NVIDIA acceleration failed; retrying with CPU decode/crop. {fallback_reason}",
            progress={"percent": 0},
        )
        succeeded, diagnostic = await _run_encode_attempt(
            db, job, emit, resolved.path, ffmpeg_out, fallback_plan, source_info
        )
    if not succeeded:
        # Clean up the parallel RPU task if the encode itself failed.
        if rpu_task is not None and not rpu_task.done():
            rpu_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await rpu_task
        raise ReencodePlanError("ffmpeg_failed", diagnostic[:500])
    await _raise_if_cancel_requested(db, job, rpu_task=rpu_task, cleanup_paths=[out, encoded_out])

    dovi_status = plan.get("dovi", {}).get("status", "not_present")
    if dovi_preserve:
        await emit(db, job.job_id, "dovi", "start", message="Preserving Dolby Vision RPU")
        try:
            await _preserve_dovi(
                resolved.path,
                encoded_out,
                out,
                job.job_id,
                emit,
                db,
                job,
                rpu_task=rpu_task,
            )
            dovi_status = "preserved"
        except Exception as exc:  # noqa: BLE001
            out.unlink(missing_ok=True)
            raise ReencodePlanError("dovi_preservation_failed", str(exc)) from exc
        finally:
            encoded_out.unlink(missing_ok=True)
    await _raise_if_cancel_requested(db, job, cleanup_paths=[out])

    await emit(db, job.job_id, "validate", "start")
    validation = await gated(validate_candidate, resolved.path, out, plan, source_info)
    if validation:
        out.unlink(missing_ok=True)
        raise ReencodePlanError("validation_failed", "; ".join(validation))
    await _raise_if_cancel_requested(db, job, cleanup_paths=[out])

    candidate_stat = out.stat()
    episode_ids = await _episode_ids_for_media_file(db, resolved.media_file_id)
    artifact_subject = (
        {"media_type": "episode", "episode_id": min(episode_ids), "movie_id": None}
        if resolved.movie_id is None and episode_ids
        else {"media_type": "movie", "movie_id": resolved.movie_id or int(request.get("movie_id") or 0)}
    )
    artifact = LetterboxReencodeArtifact(
        job_id=job.job_id,
        **artifact_subject,
        media_file_id=resolved.media_file_id,
        original_path=str(resolved.path),
        candidate_path=str(out),
        original_size_bytes=resolved.size_bytes,
        candidate_size_bytes=candidate_stat.st_size,
        original_signature=resolved.signature,
        candidate_signature=compute_signature(
            out, size=candidate_stat.st_size, mtime_ns=candidate_stat.st_mtime_ns
        ),
        encoder=plan["encoder"]["encoder"],
        encoder_family=plan["encoder"]["family"],
        codec=plan["encoder"]["codec"],
        crop_top=int(plan["crop"]["top"]),
        crop_bottom=int(plan["crop"]["bottom"]),
        hdr_status="preserved" if plan["source"].get("has_hdr") else "not_present",
        dovi_status=dovi_status,
        detail_json=json.dumps(
            {
                "plan": plan,
                "warnings": plan.get("warnings", []),
                "execution": {"acceleration": execution_acceleration},
            },
            default=str,
        ),
        status="candidate_ready",
    )
    db.add(artifact)
    await db.commit()
    await db.refresh(artifact)
    await emit(db, job.job_id, "done", "complete")
    return {
        "artifact_id": artifact.id,
        "candidate_path": str(out),
        "candidate_size_bytes": candidate_stat.st_size,
        "acceleration": execution_acceleration,
    }


async def _run_checked(
    binary_name: str,
    args: list[str],
    *,
    db: AsyncSession | None = None,
    job: MediaJob | None = None,
    timeout: float | None = 3600,
) -> None:
    proc = await asyncio.create_subprocess_exec(
        binaries.resolve(binary_name) or binary_name,
        *args,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    await record_child_pid(proc.pid)
    assert proc.stderr is not None
    stderr_task = asyncio.create_task(_drain_stderr(proc.stderr))
    cancellation_cleanup_started = False
    try:
        loop = asyncio.get_running_loop()
        started = loop.time()
        while proc.returncode is None:
            try:
                await asyncio.wait_for(proc.wait(), timeout=1.0)
            except TimeoutError:
                if timeout is not None and (loop.time() - started) > timeout:
                    await _terminate_process(proc)
                    raise RuntimeError(f"{binary_name} timed out") from None
                if db is not None and job is not None and await _media_job_cancel_requested(job.job_id):
                    await _terminate_process(proc)
                    raise ReencodePlanError("cancelled", "letterbox re-encode cancelled") from None
        stderr = await _finish_stderr_drain(stderr_task)
    except asyncio.CancelledError:
        cancellation_cleanup_started = True
        with contextlib.suppress(asyncio.CancelledError, TimeoutError):
            await asyncio.shield(
                asyncio.wait_for(
                    _cancelled_encode_cleanup(proc, None, stderr_task),
                    timeout=2 * _PROCESS_TERMINATE_GRACE_SECONDS + 2,
                )
            )
        raise
    finally:
        if not cancellation_cleanup_started:
            if proc.returncode is None:
                await _terminate_process(proc)
            await _finish_stderr_drain(stderr_task)
            await clear_child_pid(proc.pid)
    if proc.returncode != 0:
        message = stderr[:500]
        raise RuntimeError(message or f"{binary_name} exited {proc.returncode}")


async def _piped_ffmpeg_to_dovi(
    ffmpeg_args: list[str],
    dovi_args: list[str],
    *,
    timeout: float = 3600,
) -> None:
    """Run an FFmpeg demux piped into dovi_tool via stdin.

    FFmpeg writes raw HEVC to stdout; dovi_tool reads it from stdin.
    This avoids dovi_tool parsing the MKV container (which is much slower
    than FFmpeg's demuxer) and eliminates intermediate files on disk.
    """
    ffmpeg_bin = binaries.resolve("ffmpeg") or "ffmpeg"
    dovi_bin = binaries.resolve("dovi_tool") or "dovi_tool"

    ffmpeg_proc = await asyncio.create_subprocess_exec(
        ffmpeg_bin,
        *ffmpeg_args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    await record_child_pid(ffmpeg_proc.pid)
    assert ffmpeg_proc.stdout is not None
    dovi_proc = await asyncio.create_subprocess_exec(
        dovi_bin,
        *dovi_args,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    await record_child_pid(dovi_proc.pid)
    assert dovi_proc.stdin is not None

    try:
        # asyncio subprocess streams aren't real file descriptors, so FFmpeg's
        # stdout can't be handed to dovi_tool's stdin= directly (that's what
        # crashed: Popen calls .fileno() on it). Pump bytes through ourselves.
        async def _pump() -> None:
            try:
                while True:
                    chunk = await ffmpeg_proc.stdout.read(1 << 20)  # type: ignore[union-attr]
                    if not chunk:
                        break
                    try:
                        dovi_proc.stdin.write(chunk)  # type: ignore[union-attr]
                        await dovi_proc.stdin.drain()  # type: ignore[union-attr]
                    except (BrokenPipeError, ConnectionResetError):
                        break
            finally:
                with contextlib.suppress(Exception):
                    dovi_proc.stdin.close()  # type: ignore[union-attr]

        _, (_, dovi_stderr) = await asyncio.wait_for(
            asyncio.gather(_pump(), dovi_proc.communicate()), timeout=timeout
        )
        await ffmpeg_proc.wait()
    finally:
        await clear_child_pid(ffmpeg_proc.pid)
        await clear_child_pid(dovi_proc.pid)

    if ffmpeg_proc.returncode != 0:
        raise RuntimeError(
            f"FFmpeg HEVC extraction exited {ffmpeg_proc.returncode} during DOVI pipe"
        )
    if dovi_proc.returncode != 0:
        message = (dovi_stderr or b"").decode(errors="replace")[:500]
        raise RuntimeError(message or f"dovi_tool exited {dovi_proc.returncode}")


async def _extract_rpu_piped(source_mkv: Path, rpu_out: Path, *, timeout: float = 3600) -> None:
    """Pipe FFmpeg HEVC demux → dovi_tool extract-rpu.

    FFmpeg handles MKV demuxing (fast), dovi_tool only sees raw HEVC NALs
    which is much faster than having dovi_tool parse the MKV itself.
    """
    ffmpeg_args = [
        "-hide_banner",
        "-nostdin",
        "-i",
        str(source_mkv),
        "-map",
        "0:v:0",
        "-c:v",
        "copy",
        "-bsf:v",
        "hevc_mp4toannexb",
        "-f",
        "hevc",
        "pipe:1",
    ]
    dovi_args = ["--crop", "extract-rpu", "-", "-o", str(rpu_out)]
    await _piped_ffmpeg_to_dovi(ffmpeg_args, dovi_args, timeout=timeout)


async def _inject_rpu_piped(
    encoded_mkv: Path, rpu: Path, injected_hevc: Path, *, timeout: float = 3600
) -> None:
    """Pipe encoded MKV's HEVC stream → dovi_tool inject-rpu.

    Eliminates the intermediate encoded.hevc file entirely — FFmpeg streams
    the HEVC bitstream directly to dovi_tool via stdin.
    """
    ffmpeg_args = [
        "-hide_banner",
        "-nostdin",
        "-i",
        str(encoded_mkv),
        "-map",
        "0:v:0",
        "-c:v",
        "copy",
        "-bsf:v",
        "hevc_mp4toannexb",
        "-f",
        "hevc",
        "pipe:1",
    ]
    dovi_args = [
        "inject-rpu",
        "-i",
        "-",
        "--rpu-in",
        str(rpu),
        "-o",
        str(injected_hevc),
    ]
    await _piped_ffmpeg_to_dovi(ffmpeg_args, dovi_args, timeout=timeout)


async def _preserve_dovi(
    source_mkv: Path,
    encoded_mkv: Path,
    final_mkv: Path,
    job_id: str,
    emit,
    db: AsyncSession,
    job: MediaJob,
    rpu_task: asyncio.Task | None = None,
) -> None:
    """Dolby Vision RPU preservation with piped architecture.

    If *rpu_task* is provided it should be an ``asyncio.Task`` wrapping
    ``_extract_rpu_piped()`` that was launched in parallel with the main
    encode.  When the encode finishes faster than RPU extraction (unlikely
    for long files) we simply await it here.
    """
    work = encoded_mkv.parent / f".dovi-{job_id}"
    work.mkdir(parents=True, exist_ok=True)
    try:
        rpu = work / "RPU.bin"
        injected_hevc = work / "injected.hevc"

        # Step 1: Await parallel RPU extraction (started during encode) or run it now.
        await emit(
            db,
            job.job_id,
            "dovi",
            "running",
            message="Extracting Dolby Vision RPU from source (1/3)\u2026",
        )
        await _raise_if_cancel_requested(db, job, rpu_task=rpu_task, cleanup_paths=[final_mkv])
        if rpu_task is not None:
            await rpu_task
        else:
            await _extract_rpu_piped(source_mkv, rpu)
        await _raise_if_cancel_requested(db, job, cleanup_paths=[final_mkv])

        # Step 2: Pipe encoded HEVC → inject RPU (no intermediate file).
        await emit(
            db,
            job.job_id,
            "dovi",
            "running",
            message="Injecting RPU into encoded video (2/3)\u2026",
        )
        await _inject_rpu_piped(encoded_mkv, rpu, injected_hevc)
        await _raise_if_cancel_requested(db, job, cleanup_paths=[final_mkv, injected_hevc])

        # Step 3: Final remux — encoded MKV audio/subs + injected HEVC video.
        await emit(
            db,
            job.job_id,
            "dovi",
            "running",
            message="Remuxing final output with Dolby Vision (3/3)\u2026",
        )
        await _run_checked(
            "ffmpeg",
            build_dovi_remux_args(encoded_mkv, injected_hevc, final_mkv),
            db=db,
            job=job,
            timeout=3600,
        )
    finally:
        with contextlib.suppress(OSError):
            shutil.rmtree(work)


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


async def artifact_summary(db: AsyncSession) -> dict:
    rows = (
        await db.execute(
            select(
                LetterboxReencodeArtifact.status,
                func.count(),
                func.coalesce(func.sum(LetterboxReencodeArtifact.candidate_size_bytes), 0),
                func.coalesce(func.sum(LetterboxReencodeArtifact.saved_original_size_bytes), 0),
            ).group_by(LetterboxReencodeArtifact.status)
        )
    ).all()
    total_candidates = 0
    total_saved = 0
    counts = {}
    for status, count, candidate_bytes, saved_bytes in rows:
        counts[status] = count
        total_candidates += int(candidate_bytes or 0)
        total_saved += int(saved_bytes or 0)
    return {
        "counts": counts,
        "candidate_bytes": total_candidates,
        "saved_original_bytes": total_saved,
        "total_bytes": total_candidates + total_saved,
    }


def artifact_to_dict(artifact: LetterboxReencodeArtifact) -> dict:
    return {
        "id": artifact.id,
        "job_id": artifact.job_id,
        "media_type": artifact.media_type,
        "movie_id": artifact.movie_id,
        "episode_id": artifact.episode_id,
        "media_file_id": artifact.media_file_id,
        "status": artifact.status,
        "original_path": artifact.original_path,
        "candidate_path": artifact.candidate_path,
        "saved_original_path": artifact.saved_original_path,
        "original_size_bytes": artifact.original_size_bytes,
        "candidate_size_bytes": artifact.candidate_size_bytes,
        "saved_original_size_bytes": artifact.saved_original_size_bytes,
        "encoder": artifact.encoder,
        "encoder_family": artifact.encoder_family,
        "codec": artifact.codec,
        "crop_top": artifact.crop_top,
        "crop_bottom": artifact.crop_bottom,
        "hdr_status": artifact.hdr_status,
        "dovi_status": artifact.dovi_status,
        "detail": json.loads(artifact.detail_json) if artifact.detail_json else None,
        "created_at": artifact.created_at.isoformat() if artifact.created_at else None,
        "updated_at": artifact.updated_at.isoformat() if artifact.updated_at else None,
    }


async def replace_original(db: AsyncSession, artifact: LetterboxReencodeArtifact) -> dict:
    if artifact.status not in {"candidate_ready", "kept"}:
        raise ReencodePlanError(
            "invalid_status", f"artifact is not replaceable from status {artifact.status}"
        )
    original = Path(artifact.original_path)
    candidate = Path(artifact.candidate_path or "")
    if not original.is_file() or not candidate.is_file():
        artifact.status = "missing"
        await db.commit()
        raise ReencodePlanError("missing_file", "original or candidate file is missing")
    media_row = await db.get(MediaFile, artifact.media_file_id) if artifact.media_file_id else None
    source_key = (
        (media_row.source_key if media_row else f"movie-{artifact.movie_id}")
        .replace(":", "_")
        .replace("/", "_")
        .replace("\\", "_")
    )
    saved = (
        Path(artifact.saved_original_path)
        if artifact.saved_original_path
        else saved_original_path(original, source_key, artifact.job_id or uuid4().hex)
    )
    saved.parent.mkdir(parents=True, exist_ok=True)
    os.replace(original, saved)
    os.replace(candidate, original)
    stat = saved.stat()
    artifact.saved_original_path = str(saved)
    artifact.saved_original_size_bytes = stat.st_size
    artifact.saved_original_signature = compute_signature(
        saved, size=stat.st_size, mtime_ns=stat.st_mtime_ns
    )
    resolved_at = datetime.now(UTC)
    artifact.status = "replaced"
    artifact.updated_at = resolved_at
    if media_row is not None:
        media_row.size_bytes = original.stat().st_size
    states = await _states_for_artifact(db, artifact)
    for state in states:
        aspect_label = state.aspect_label
        if aspect_label is None and artifact.detail_json:
            try:
                detail = json.loads(artifact.detail_json)
            except json.JSONDecodeError:
                detail = {}
            plan = detail.get("plan") if isinstance(detail, dict) else {}
            source = plan.get("source") if isinstance(plan, dict) else {}
            crop = plan.get("crop") if isinstance(plan, dict) else {}
            try:
                width = int(source.get("width") or 0)
                output_height = int(crop.get("output_height") or 0)
            except (TypeError, ValueError):
                width = output_height = 0
            if width > 0 and output_height > 0:
                aspect_label = letterbox_detect.aspect_label(width, output_height)
        state.status = "reencoded"
        state.applied_crop_top = artifact.crop_top
        state.applied_crop_bottom = artifact.crop_bottom
        state.last_applied_at = resolved_at
        state.error = None
        state.resolved_by = "reencode"
        state.resolved_at = resolved_at
        state.original_crop_top = artifact.crop_top
        state.original_crop_bottom = artifact.crop_bottom
        state.original_aspect_label = aspect_label
    await db.commit()
    return artifact_to_dict(artifact)


async def restore_original(
    db: AsyncSession, artifact: LetterboxReencodeArtifact, *, keep_candidate: bool = False
) -> dict:
    if artifact.status != "replaced":
        raise ReencodePlanError("invalid_status", "only replaced artifacts can restore an original")
    original = Path(artifact.original_path)
    saved = Path(artifact.saved_original_path or "")
    if not saved.is_file():
        artifact.status = "missing"
        await db.commit()
        raise ReencodePlanError("missing_file", "saved original is missing")
    if keep_candidate:
        candidate = Path(artifact.candidate_path or "")
        if not candidate:
            candidate = original.with_name(f".{original.name}.letterbox.{artifact.job_id}.mkv")
        candidate.parent.mkdir(parents=True, exist_ok=True)
        if original.is_file():
            os.replace(original, candidate)
        artifact.candidate_path = str(candidate)
        artifact.candidate_size_bytes = candidate.stat().st_size if candidate.is_file() else None
    os.replace(saved, original)
    artifact.status = "restored"
    artifact.saved_original_path = None
    artifact.updated_at = datetime.now(UTC)
    media_row = await db.get(MediaFile, artifact.media_file_id) if artifact.media_file_id else None
    if media_row is not None:
        media_row.size_bytes = original.stat().st_size
    states = await _states_for_artifact(db, artifact)
    for state in states:
        state.status = "candidate"
        state.applied_crop_top = None
        state.applied_crop_bottom = None
        state.resolved_by = None
        state.resolved_at = None
        state.original_crop_top = None
        state.original_crop_bottom = None
        state.original_aspect_label = None
    await db.commit()
    return artifact_to_dict(artifact)


async def delete_artifact_files(db: AsyncSession, artifact: LetterboxReencodeArtifact) -> dict:
    deleted: list[str] = []
    for raw in (artifact.candidate_path, artifact.saved_original_path):
        if not raw:
            continue
        path = Path(raw)
        if path == Path(artifact.original_path):
            continue
        if path.exists():
            path.unlink()
            deleted.append(str(path))
    artifact.status = "deleted"
    artifact.updated_at = datetime.now(UTC)
    await db.commit()
    return {"id": artifact.id, "deleted": deleted, "status": artifact.status}
