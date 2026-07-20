"""Tracked, candidate-only Dolby Vision conversion execution."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from marquee.core import letterbox_transcode
from marquee.core.jobs.artifact_service import register_physical_artifact
from marquee.core.jobs.delivery import ExecutionContext, register_execution_handler
from marquee.core.jobs.dovi_conversion_documents import (
    DoviConvertRequestV1,
    DoviConvertResultV1,
    DoviProbeV1,
)
from marquee.core.jobs.media_mutation_support import load_media_file, physical


class DoviConversionExecutionError(RuntimeError):
    """Candidate generation failed without publishing source media."""


def _integer(value: object) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _boolean(value: object) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return bool(value)
    if isinstance(value, str) and value.lower() in {"true", "false", "1", "0"}:
        return value.lower() in {"true", "1"}
    return None


def _dovi_record(video: dict[str, Any]) -> dict[str, Any] | None:
    for item in video.get("side_data_list") or ():
        if not isinstance(item, dict):
            continue
        serialized = json.dumps(item, sort_keys=True).lower()
        if "dovi" in serialized or "dolby vision" in serialized or "dv_profile" in item:
            return item
    return None


def _probe_document(payload: dict[str, Any]) -> DoviProbeV1:
    streams = payload.get("streams") if isinstance(payload.get("streams"), list) else []
    video = next(
        (item for item in streams if isinstance(item, dict) and item.get("codec_type") == "video"),
        None,
    )
    if not isinstance(video, dict):
        raise DoviConversionExecutionError("ffprobe found no video stream")
    record = _dovi_record(video)
    duration: float | None = None
    raw_duration = video.get("duration") or (payload.get("format") or {}).get("duration")
    try:
        duration = float(raw_duration) if raw_duration is not None else None
    except (TypeError, ValueError):
        duration = None
    transfer = str(video.get("color_transfer") or "").lower()
    return DoviProbeV1(
        codec=str(video.get("codec_name")) if video.get("codec_name") else None,
        width=int(video.get("width") or 0),
        height=int(video.get("height") or 0),
        duration_seconds=duration if duration and duration > 0 else None,
        has_hdr=transfer in {"smpte2084", "arib-std-b67"},
        has_dolby_vision=record is not None,
        video_streams=sum(1 for item in streams if item.get("codec_type") == "video"),
        audio_streams=sum(1 for item in streams if item.get("codec_type") == "audio"),
        subtitle_streams=sum(1 for item in streams if item.get("codec_type") == "subtitle"),
        attachment_streams=sum(1 for item in streams if item.get("codec_type") == "attachment"),
        dovi_profile=_integer(record.get("dv_profile")) if record else None,
        dovi_level=_integer(record.get("dv_level")) if record else None,
        enhancement_layer_present=_boolean(record.get("el_present")) if record else None,
        bl_signal_compatibility_id=(
            _integer(record.get("dv_bl_signal_compatibility_id")) if record else None
        ),
    )


async def _probe(context: ExecutionContext, path: Path) -> DoviProbeV1:
    process = await context.process_launcher.launch(
        "ffprobe",
        ["-v", "error", "-print_format", "json", "-show_streams", "-show_format", str(path)],
        stdout_limit=8 * 1024 * 1024,
    )
    summary = await process.wait()
    if summary.exit_code != 0 or summary.stdout.truncated:
        raise DoviConversionExecutionError("ffprobe failed or returned truncated evidence")
    try:
        payload = json.loads(summary.stdout.captured)
    except (TypeError, ValueError) as exc:
        raise DoviConversionExecutionError("ffprobe returned invalid JSON") from exc
    return _probe_document(payload)


async def _run(
    context: ExecutionContext,
    tool: str,
    args: list[str],
    *,
    stdout_sink=None,
) -> None:
    process = await context.process_launcher.launch(
        tool, args, stdout_limit=16 * 1024 * 1024, stdout_sink=stdout_sink
    )
    summary = await process.wait()
    if summary.exit_code != 0 or summary.stdout.truncated:
        raise DoviConversionExecutionError(f"{tool} failed while producing the candidate")


def _extract_hevc_args(source: Path, output: Path) -> list[str]:
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


def _p5_base_encode_args(source: Path, output: Path) -> list[str]:
    """Build the sealed Profile 5 base-layer conversion command."""
    return [
        "-y",
        "-hide_banner",
        "-nostdin",
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
        "libx265",
        "-preset",
        "slow",
        "-crf",
        "18",
        "-pix_fmt",
        "yuv420p10le",
        "-vf",
        "format=p010le,zscale=primaries=bt2020:transfer=smpte2084:matrix=bt2020nc,format=yuv420p10le",
        "-color_primaries",
        "bt2020",
        "-color_trc",
        "smpte2084",
        "-colorspace",
        "bt2020nc",
        "-progress",
        "pipe:1",
        "-nostats",
        str(output),
    ]


def _progress_sink(context: ExecutionContext, request: DoviConvertRequestV1):
    pending = ""
    values: dict[str, str] = {}
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
            update = letterbox_transcode._parse_progress(
                line, request.source_probe.duration_seconds, values
            )
            if update is None:
                continue
            duration = request.source_probe.duration_seconds
            completed = float(update.get("out_time_seconds") or 0)
            await context.progress.stage(
                "encoding",
                label="Encoding Dolby Vision base layer",
                completed=min(completed, duration) if duration else None,
                total=duration,
                unit="seconds",
            )

    return sink


def _failed(
    request: DoviConvertRequestV1,
    code: str,
    message: str,
    *,
    source: DoviProbeV1 | None = None,
    output: DoviProbeV1 | None = None,
    validation: tuple[str, ...] = (),
) -> dict[str, object]:
    return DoviConvertResultV1(
        outcome="failed",
        reason_code=code,
        message=message[:500],
        kind=request.kind,
        source_profile=(source or request.source_probe).dovi_profile,
        source_probe=source,
        output_probe=output,
        validation=validation,
    ).model_dump(mode="json")


def _validation_problems(
    request: DoviConvertRequestV1, source: DoviProbeV1, output: DoviProbeV1
) -> tuple[str, ...]:
    problems: list[str] = []
    if output.dovi_profile != 8:
        problems.append("candidate is not Dolby Vision Profile 8")
    if output.enhancement_layer_present is True:
        problems.append("candidate still contains an enhancement layer")
    if (output.width, output.height) != (source.width, source.height):
        problems.append("candidate dimensions changed")
    for field in ("audio_streams", "subtitle_streams", "attachment_streams"):
        if getattr(output, field) != getattr(source, field):
            problems.append(f"candidate {field} count changed")
    if (
        source.duration_seconds
        and output.duration_seconds
        and abs(source.duration_seconds - output.duration_seconds) > 2.0
    ):
        problems.append("candidate duration drift exceeds two seconds")
    if not output.has_hdr or not output.has_dolby_vision:
        problems.append("candidate lost HDR or Dolby Vision metadata")
    if request.kind == "p5_to_p81" and output.bl_signal_compatibility_id not in {1, None}:
        problems.append("Profile 5 candidate lacks the certified HDR10 compatibility identifier")
    return tuple(problems)


async def execute_dovi_convert(context: ExecutionContext) -> dict[str, object]:
    request = DoviConvertRequestV1.model_validate(context.request)
    try:
        resolved = await load_media_file(context, request.media_file_id)
    except Exception as exc:  # noqa: BLE001
        return _failed(request, "media_unavailable", str(exc))
    if resolved.signature != request.source_signature:
        return _failed(request, "stale_plan", "source signature changed after confirmation")
    if resolved.path.suffix.lower() != ".mkv":
        return _failed(request, "unsupported_container", "only Matroska conversion is certified")
    try:
        source_probe = await _probe(context, resolved.path)
    except Exception as exc:  # noqa: BLE001
        return _failed(request, "probe_failed", str(exc))
    if (
        source_probe.dovi_profile != request.source_probe.dovi_profile
        or source_probe.codec != request.source_probe.codec
        or (source_probe.width, source_probe.height)
        != (request.source_probe.width, request.source_probe.height)
    ):
        return _failed(request, "stale_probe", "source media probe changed after confirmation")

    def staging(name: str):
        confined, fd = context.workspace.staging_file(name)
        os.close(fd)
        return confined

    source_hevc_key = staging("source.hevc")
    candidate_key = staging("dovi-candidate.mkv")
    source_hevc = physical(source_hevc_key)
    candidate = physical(candidate_key)
    try:
        await _run(context, "ffmpeg", _extract_hevc_args(resolved.path, source_hevc))
        if request.kind == "p7_strip_el":
            converted = physical(staging("converted-p81.hevc"))
            await _run(
                context,
                "dovi_tool",
                ["--mode", "2", "convert", "--discard", str(source_hevc), "-o", str(converted)],
            )
            await _run(
                context,
                "ffmpeg",
                letterbox_transcode.build_dovi_remux_args(resolved.path, converted, candidate),
            )
        else:
            rpu = physical(staging("RPU.bin"))
            encoded_mkv = physical(staging("base-bt2020.mkv"))
            encoded_hevc = physical(staging("base-bt2020.hevc"))
            injected = physical(staging("injected-p81.hevc"))
            await _run(
                context,
                "dovi_tool",
                ["extract-rpu", "-i", str(source_hevc), "-o", str(rpu)],
            )
            await _run(
                context,
                "ffmpeg",
                _p5_base_encode_args(resolved.path, encoded_mkv),
                stdout_sink=_progress_sink(context, request),
            )
            await _run(context, "ffmpeg", _extract_hevc_args(encoded_mkv, encoded_hevc))
            await _run(
                context,
                "dovi_tool",
                [
                    "--mode",
                    "3",
                    "inject-rpu",
                    "-i",
                    str(encoded_hevc),
                    "--rpu-in",
                    str(rpu),
                    "-o",
                    str(injected),
                ],
            )
            await _run(
                context,
                "ffmpeg",
                letterbox_transcode.build_dovi_remux_args(encoded_mkv, injected, candidate),
            )
        output_probe = await _probe(context, candidate)
    except Exception as exc:  # noqa: BLE001
        return _failed(request, "conversion_failed", str(exc), source=source_probe)
    problems = _validation_problems(request, source_probe, output_probe)
    if problems:
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
        source=candidate_key,
        kind="media_candidate",
        name="dovi-candidate.mkv",
        content_type="video/x-matroska",
        retention_class="extended",
        metadata={
            "operation_family": "dovi",
            "media_file_id": request.media_file_id,
            "movie_id": request.movie_id,
            "conversion_kind": request.kind,
            "source_signature": request.source_signature,
            "source_probe": source_probe.model_dump(mode="json"),
            "output_probe": output_probe.model_dump(mode="json"),
        },
    )
    return DoviConvertResultV1(
        outcome="succeeded",
        reason_code="candidate_ready",
        message="verified Dolby Vision candidate is available; source media was not modified",
        kind=request.kind,
        artifact_id=artifact.id,
        artifact_checksum=artifact.checksum,
        artifact_size_bytes=artifact.size_bytes,
        source_profile=source_probe.dovi_profile,
        source_probe=source_probe,
        output_probe=output_probe,
        validated=True,
        validation=("profile_8", "no_enhancement_layer", "streams", "duration", "hdr"),
    ).model_dump(mode="json")


register_execution_handler("dovi_convert", execute_dovi_convert)
