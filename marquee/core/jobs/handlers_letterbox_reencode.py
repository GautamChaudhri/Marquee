"""Tracked candidate-only permanent letterbox re-encode execution."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from marquee.core import letterbox_reencode
from marquee.core.jobs.artifact_service import register_physical_artifact
from marquee.core.jobs.delivery import ExecutionContext, register_execution_handler
from marquee.core.jobs.letterbox_reencode_documents import (
    LetterboxReencodeRequestV1,
    LetterboxReencodeResultV1,
    ReencodeProbeV1,
)
from marquee.core.jobs.media_mutation_support import load_media_file, physical


class LetterboxReencodeError(RuntimeError):
    """Candidate production or validation failed without publishing source media."""


def _probe_document(payload: dict[str, Any]) -> ReencodeProbeV1:
    streams = payload.get("streams") if isinstance(payload.get("streams"), list) else []
    video = next(
        (item for item in streams if isinstance(item, dict) and item.get("codec_type") == "video"),
        None,
    )
    if not isinstance(video, dict):
        raise LetterboxReencodeError("ffprobe found no video stream")
    duration: float | None = None
    raw_duration = video.get("duration") or (payload.get("format") or {}).get("duration")
    try:
        duration = float(raw_duration) if raw_duration is not None else None
    except (TypeError, ValueError):
        duration = None
    serialized = json.dumps(video, sort_keys=True).lower()
    transfer = str(video.get("color_transfer") or "").lower()
    return ReencodeProbeV1(
        codec=str(video.get("codec_name")) if video.get("codec_name") else None,
        width=int(video.get("width") or 0),
        height=int(video.get("height") or 0),
        duration_seconds=duration if duration and duration > 0 else None,
        has_hdr=transfer in {"smpte2084", "arib-std-b67"},
        has_dolby_vision="dovi" in serialized or "dolby vision" in serialized,
        video_streams=sum(1 for item in streams if item.get("codec_type") == "video"),
        audio_streams=sum(1 for item in streams if item.get("codec_type") == "audio"),
        subtitle_streams=sum(1 for item in streams if item.get("codec_type") == "subtitle"),
        attachment_streams=sum(1 for item in streams if item.get("codec_type") == "attachment"),
    )


async def _probe(context: ExecutionContext, path: Path) -> ReencodeProbeV1:
    process = await context.process_launcher.launch(
        "ffprobe",
        [
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_streams",
            "-show_format",
            str(path),
        ],
        stdout_limit=8 * 1024 * 1024,
    )
    summary = await process.wait()
    if summary.exit_code != 0 or summary.stdout.truncated:
        raise LetterboxReencodeError("ffprobe failed or returned truncated evidence")
    try:
        payload = json.loads(summary.stdout.captured)
    except (TypeError, ValueError) as exc:
        raise LetterboxReencodeError("ffprobe returned invalid JSON") from exc
    return _probe_document(payload)


def _plan_document(request: LetterboxReencodeRequestV1) -> dict[str, object]:
    source = request.source
    return {
        "crop": {
            "top": request.crop_top,
            "bottom": request.crop_bottom,
            "output_height": request.output_height,
        },
        "source": {
            "codec": source.codec,
            "width": source.width,
            "height": source.height,
            "pix_fmt": source.pixel_format,
            "color_transfer": source.color_transfer,
            "color_primaries": source.color_primaries,
            "color_space": source.color_space,
            "has_hdr": source.has_hdr,
            "has_dovi": source.has_dolby_vision,
        },
        "encoder": request.encoder.model_dump(mode="json"),
        "acceleration": {"enabled": False},
    }


def _validation_problems(
    request: LetterboxReencodeRequestV1,
    source: ReencodeProbeV1,
    output: ReencodeProbeV1,
) -> tuple[str, ...]:
    problems: list[str] = []
    if (output.width, output.height) != (source.width, request.output_height):
        problems.append("candidate dimensions do not match the sealed crop")
    for field in ("audio_streams", "subtitle_streams", "attachment_streams"):
        if getattr(output, field) != getattr(source, field):
            problems.append(f"candidate {field} count changed")
    if (
        source.duration_seconds
        and output.duration_seconds
        and abs(source.duration_seconds - output.duration_seconds) > 2.0
    ):
        problems.append("candidate duration drift exceeds two seconds")
    if source.has_hdr and not output.has_hdr:
        problems.append("candidate lost HDR transfer metadata")
    if source.has_dolby_vision and not output.has_dolby_vision:
        problems.append("candidate lost Dolby Vision metadata")
    return tuple(problems)


def _failed(
    request: LetterboxReencodeRequestV1,
    code: str,
    message: str,
    *,
    source: ReencodeProbeV1 | None = None,
    output: ReencodeProbeV1 | None = None,
    validation: tuple[str, ...] = (),
) -> dict[str, object]:
    return LetterboxReencodeResultV1(
        outcome="failed",
        reason_code=code,
        message=message[:500],
        crop_top=request.crop_top,
        crop_bottom=request.crop_bottom,
        encoder=request.encoder.encoder,
        hardware_family=request.encoder.family,
        used_cpu_fallback=request.encoder.used_cpu_fallback,
        source_width=source.width if source else request.source.width,
        source_height=source.height if source else request.source.height,
        output_width=output.width if output else None,
        output_height=output.height if output else None,
        input_bytes=request.source.size_bytes,
        validated=False,
        source_probe=source,
        output_probe=output,
        validation=validation,
    ).model_dump(mode="json")


def _progress_sink(
    context: ExecutionContext, request: LetterboxReencodeRequestV1
) -> tuple[Any, dict[str, object]]:
    pending = ""
    values: dict[str, str] = {}
    latest: dict[str, object] = {}
    ordinal = 0

    async def sink(_source: str, chunk: bytes, final: bool) -> None:
        nonlocal pending, ordinal
        pending += chunk.decode("utf-8", errors="replace")
        lines = pending.splitlines()
        if not final and pending and not pending.endswith(("\n", "\r")):
            pending = lines.pop() if lines else pending
        else:
            pending = ""
        for line in lines:
            update = letterbox_reencode._parse_progress(
                line, request.source.duration_seconds, values
            )
            if update is None:
                continue
            latest.clear()
            latest.update(update)
            completed = float(update.get("out_time_seconds") or 0)
            duration = request.source.duration_seconds
            await context.progress.stage(
                "encoding",
                label="Encoding candidate",
                completed=min(completed, duration) if duration else None,
                total=duration,
                unit="seconds",
                speed=(
                    float(update["speed"])
                    if isinstance(update.get("speed"), (int, float))
                    else None
                ),
                fps=(
                    float(update["fps"])
                    if isinstance(update.get("fps"), (int, float))
                    else None
                ),
            )

    return sink, latest


async def execute_letterbox_reencode(context: ExecutionContext) -> dict[str, object]:
    request = LetterboxReencodeRequestV1.model_validate(context.request)
    try:
        resolved = await load_media_file(context, request.media_file_id)
    except Exception as exc:  # noqa: BLE001 - normalized into typed evidence
        return _failed(request, "media_unavailable", str(exc) or "media unavailable")
    if resolved.signature != request.source.signature:
        return _failed(request, "stale_plan", "source signature changed after confirmation")
    try:
        source_probe = await _probe(context, resolved.path)
    except Exception as exc:  # noqa: BLE001
        return _failed(request, "probe_failed", str(exc))
    if (source_probe.width, source_probe.height) != (
        request.source.width,
        request.source.height,
    ):
        return _failed(request, "stale_dimensions", "source dimensions changed after planning")
    if source_probe.has_dolby_vision:
        return _failed(
            request,
            "dolby_vision_uncertified",
            "Dolby Vision candidate re-encode is unavailable until tracked RPU preservation is certified",
            source=source_probe,
        )

    candidate, fd = context.workspace.staging_file("letterbox-candidate.mkv")
    os.close(fd)
    candidate_path = physical(candidate)
    plan = _plan_document(request)
    args = letterbox_reencode.build_ffmpeg_args(resolved.path, candidate_path, plan)
    stdout_sink, latest = _progress_sink(context, request)
    try:
        process = await context.process_launcher.launch(
            "ffmpeg",
            args,
            stdout_limit=16 * 1024 * 1024,
            stdout_sink=stdout_sink,
        )
        summary = await process.wait()
    except Exception as exc:  # noqa: BLE001
        context.workspace.boundary.delete_file(candidate, missing_ok=True)
        return _failed(request, "encode_launch_failed", str(exc), source=source_probe)
    if summary.exit_code != 0 or summary.stdout.truncated:
        context.workspace.boundary.delete_file(candidate, missing_ok=True)
        return _failed(
            request,
            "encode_failed",
            "FFmpeg did not produce a complete candidate",
            source=source_probe,
        )

    try:
        output_probe = await _probe(context, candidate_path)
    except Exception as exc:  # noqa: BLE001
        context.workspace.boundary.delete_file(candidate, missing_ok=True)
        return _failed(request, "candidate_probe_failed", str(exc), source=source_probe)
    problems = _validation_problems(request, source_probe, output_probe)
    if problems:
        context.workspace.boundary.delete_file(candidate, missing_ok=True)
        return _failed(
            request,
            "candidate_validation_failed",
            "; ".join(problems),
            source=source_probe,
            output=output_probe,
            validation=problems,
        )
    artifact = await register_physical_artifact(
        job_id=context.delivery.canonical_job_id,
        attempt_id=context.attempt.attempt_id,
        fence_token=context.attempt.fence_token,
        source=candidate,
        kind="media_candidate",
        name="letterbox-candidate.mkv",
        content_type="video/x-matroska",
        retention_class="extended",
        metadata={
            "media_file_id": request.media_file_id,
            "subject_kind": request.subject_kind,
            "subject_id": request.subject_id,
            "source_signature": request.source.signature,
            "crop_top": request.crop_top,
            "crop_bottom": request.crop_bottom,
            "encoder": request.encoder.encoder,
            "source_probe": source_probe.model_dump(mode="json"),
            "output_probe": output_probe.model_dump(mode="json"),
        },
    )
    context.workspace.boundary.delete_file(candidate, missing_ok=True)
    elapsed = (summary.finished_at - summary.started_at).total_seconds()
    return LetterboxReencodeResultV1(
        outcome="succeeded",
        reason_code="candidate_ready",
        message="verified re-encode candidate is available; source media was not modified",
        artifact_id=artifact.id,
        artifact_checksum=artifact.checksum,
        artifact_size_bytes=artifact.size_bytes,
        crop_top=request.crop_top,
        crop_bottom=request.crop_bottom,
        encoder=request.encoder.encoder,
        hardware_family=request.encoder.family,
        used_cpu_fallback=request.encoder.used_cpu_fallback,
        source_width=source_probe.width,
        source_height=source_probe.height,
        output_width=output_probe.width,
        output_height=output_probe.height,
        input_bytes=request.source.size_bytes,
        output_bytes=artifact.size_bytes,
        validated=True,
        hdr_preserved=(not source_probe.has_hdr or output_probe.has_hdr),
        elapsed_seconds=elapsed,
        speed=float(latest["speed"]) if isinstance(latest.get("speed"), (int, float)) else None,
        source_probe=source_probe,
        output_probe=output_probe,
        validation=("dimensions", "stream_counts", "duration", "hdr"),
    ).model_dump(mode="json")


register_execution_handler("letterbox_reencode", execute_letterbox_reencode)
