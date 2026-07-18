"""Canonical, read-only Dolby Vision analysis for JMC4B.

This module intentionally does not reuse the legacy Dolby Vision job handler or
conversion implementation.  It confines every executable to the JMC3 launcher
and only records a validated derived ``DoviState`` projection.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from marquee.core.jobs.delivery import ExecutionContext, register_execution_handler
from marquee.core.jobs.policies import ClassifiedExecutionError, RetryClassification
from marquee.core.jobs.process_launcher import ProcessLaunchError
from marquee.core.jobs.progress import MeasurementMode, ProgressMeasurementUpdate
from marquee.core.jobs.progress_service import ProgressObservation, progress_writer
from marquee.core.media_files import (
    MediaFileNotFoundError,
    MediaFileUnavailableError,
    ResolvedMediaFile,
    resolve_media_file,
)
from marquee.models import DoviState, EpisodeMediaFile, MediaFile

_FFPROBE_ARGS = (
    "-v",
    "error",
    "-print_format",
    "json",
    "-show_format",
    "-show_streams",
)
_SUMMARY_LIMIT = 12_000
_BIT_DEPTH = re.compile(r"(?:p|yuv)(?:420|422|444)p?(\d{2})(?:le|be)?$", re.IGNORECASE)


class DoviAnalysisError(RuntimeError):
    """Permanent, path-free failure for an invalid Dolby Vision observation."""


def _tool_retry(tool: str) -> ClassifiedExecutionError:
    return ClassifiedExecutionError(
        f"{tool} is temporarily unavailable", RetryClassification.TRANSIENT
    )


async def _progress(context: ExecutionContext, stage: str, ordinal: int) -> None:
    await progress_writer.safe_write(
        job_id=context.delivery.canonical_job_id,
        attempt_id=context.attempt.attempt_id,
        fence_token=context.attempt.fence_token,
        observation=ProgressObservation(
            stage_key=stage,
            overall=ProgressMeasurementUpdate(
                scope_id="dovi-analysis:overall", mode=MeasurementMode.INDETERMINATE
            ),
            current=ProgressMeasurementUpdate(
                scope_id=f"dovi-analysis:{context.subject.get('media_file_id', 'file')}:{stage}",
                mode=MeasurementMode.INDETERMINATE,
            ),
            producer_ordinal=ordinal,
        ),
    )


async def _launch_json(context: ExecutionContext, tool: str, args: list[str]) -> dict[str, Any]:
    try:
        process = await context.process_launcher.launch(tool, args)
    except ProcessLaunchError as exc:
        raise _tool_retry(tool) from exc
    result = await process.wait()
    if result.exit_code != 0:
        raise DoviAnalysisError("Dolby Vision media probe failed")
    try:
        payload = json.loads(result.stdout.captured.decode("utf-8", "replace"))
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise DoviAnalysisError("Dolby Vision media probe returned invalid data") from exc
    if not isinstance(payload, dict):
        raise DoviAnalysisError("Dolby Vision media probe returned invalid data")
    return payload


def _integer(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _boolean(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes"}:
            return True
        if normalized in {"0", "false", "no"}:
            return False
    return None


def _color_base(color_transfer: str | None, compatibility_id: int | None) -> str:
    if compatibility_id == 1:
        return "hdr10"
    if compatibility_id == 2:
        return "sdr"
    if compatibility_id == 4:
        return "hlg"
    normalized = (color_transfer or "").lower()
    if normalized in {"smpte2084", "pq"}:
        return "hdr10"
    if normalized in {"arib-std-b67", "hlg"}:
        return "hlg"
    return "unknown"


def _bit_depth(stream: Mapping[str, Any]) -> int | None:
    direct = _integer(stream.get("bits_per_raw_sample") or stream.get("bits_per_sample"))
    if direct is not None and 1 <= direct <= 32:
        return direct
    pixel_format = str(stream.get("pix_fmt") or "")
    match = _BIT_DEPTH.search(pixel_format)
    return int(match.group(1)) if match else None


def _dovi_record(stream: Mapping[str, Any]) -> Mapping[str, Any] | None:
    side_data = stream.get("side_data_list")
    if not isinstance(side_data, list):
        return None
    for item in side_data:
        if not isinstance(item, Mapping):
            continue
        kind = str(item.get("side_data_type") or "").lower()
        if "dovi configuration record" in kind:
            return item
    return None


def _bounded_summary(raw: bytes) -> str | None:
    text = raw.decode("utf-8", "replace")
    sanitized = "".join(character for character in text if character in "\n\r\t" or character >= " ")
    value = sanitized.strip()[:_SUMMARY_LIMIT]
    return value or None


def _classify_el_type(summary: str | None) -> str | None:
    normalized = (summary or "").upper()
    if "FEL" in normalized:
        return "FEL"
    if "MEL" in normalized:
        return "MEL"
    return None


async def _dovi_tool_summary(
    context: ExecutionContext, path: str
) -> tuple[str | None, str | None]:
    """Ask the allowlisted tool for advisory RPU detail without changing media."""
    try:
        process = await context.process_launcher.launch("dovi_tool", ["info", "-i", path, "--summary"])
    except ProcessLaunchError as exc:
        raise _tool_retry("dovi_tool") from exc
    result = await process.wait()
    summary = _bounded_summary(result.stdout.captured or result.stderr.captured)
    if result.exit_code != 0:
        return None, "dovi_tool_summary_unavailable"
    return summary, None


def _parse_probe(
    probe: Mapping[str, Any], *, analysis_depth: str, tool_summary: str | None, tool_warning: str | None
) -> dict[str, Any]:
    streams = probe.get("streams")
    video = next(
        (stream for stream in streams if isinstance(stream, Mapping) and stream.get("codec_type") == "video"),
        None,
    )
    if not isinstance(video, Mapping):
        raise DoviAnalysisError("media has no video stream")
    record = _dovi_record(video)
    profile = _integer(record.get("dv_profile")) if record else None
    level = _integer(record.get("dv_level")) if record else None
    compatibility_id = _integer(record.get("dv_bl_signal_compatibility_id")) if record else None
    rpu_present = _boolean(record.get("rpu_present")) if record else None
    el_present = _boolean(record.get("el_present")) if record else None
    bl_present = _boolean(record.get("bl_present")) if record else None
    has_dovi = record is not None or profile is not None
    warnings: list[str] = []
    if not has_dovi:
        warnings.append("dolby_vision_not_present")
    if has_dovi and profile is None:
        warnings.append("dolby_vision_profile_missing")
    if tool_warning is not None:
        warnings.append(tool_warning)
    if analysis_depth == "deep" and has_dovi and tool_summary is None:
        warnings.append("deep_rpu_detail_unavailable")
    codec = str(video.get("codec_name") or "") or None
    supported = bool(has_dovi and codec == "hevc")
    if has_dovi and not supported:
        warnings.append("unsupported_dolby_vision_codec")
    el_type = _classify_el_type(tool_summary)
    if profile == 7 and el_present is not True:
        el_present = True
    validation = {
        "ffprobe": "valid",
        "dovi_record": "present" if has_dovi else "absent",
        "rpu_tool": "valid" if tool_summary else "not_run" if analysis_depth == "standard" else "unavailable",
    }
    return {
        "status": "analyzed" if has_dovi else "not_dovi",
        "dovi_profile": profile,
        "dovi_level": level,
        "el_present": el_present,
        "el_type": el_type,
        "bl_signal_compatibility_id": compatibility_id,
        "source_codec": codec,
        "source_hdr_base": _color_base(str(video.get("color_transfer") or "") or None, compatibility_id),
        "source_bit_depth": _bit_depth(video),
        "color_primaries": str(video.get("color_primaries") or "") or None,
        "color_transfer": str(video.get("color_transfer") or "") or None,
        "color_space": str(video.get("color_space") or "") or None,
        "rpu_present": rpu_present,
        "bl_present": bl_present,
        "analysis_depth": analysis_depth,
        "analysis_supported": supported,
        "rpu_summary_json": tool_summary,
        "warnings_json": json.dumps(sorted(set(warnings)), separators=(",", ":")),
        "validation_json": json.dumps(validation, separators=(",", ":"), sort_keys=True),
        "error_reason": None,
    }


async def _load_source(context: ExecutionContext, media_file_id: int) -> ResolvedMediaFile:
    async with context.session_factory() as db:
        media_file = await db.get(MediaFile, media_file_id)
        if media_file is None:
            raise DoviAnalysisError("media file no longer exists")
        if not media_file.is_active or not media_file.is_present:
            raise DoviAnalysisError("media file has been retired")
        try:
            return await resolve_media_file(db, media_file_id)
        except MediaFileNotFoundError as exc:
            raise DoviAnalysisError("media file no longer exists") from exc
        except MediaFileUnavailableError as exc:
            raise DoviAnalysisError("media file is unavailable") from exc


def _state_predicate(movie_id: int | None, episode_id: int | None):
    return DoviState.movie_id == movie_id if movie_id is not None else DoviState.episode_id == episode_id


async def _is_current(
    context: ExecutionContext, *, media_file_id: int, movie_id: int | None, episode_id: int | None, signature: str, depth: str
) -> bool:
    async with context.session_factory() as db:
        state = await db.scalar(select(DoviState).where(_state_predicate(movie_id, episode_id)))
        return bool(
            state is not None
            and state.status in {"analyzed", "not_dovi"}
            and state.source_signature == signature
            and state.analysis_depth == depth
            and media_file_id == int(context.subject.get("media_file_id") or media_file_id)
        )


async def _store_analysis(
    context: ExecutionContext,
    *,
    media_file_id: int,
    movie_id: int | None,
    episode_id: int | None,
    expected_signature: str,
    values: Mapping[str, Any],
) -> tuple[bool, str | None]:
    """Fence a derived state write against both source and canonical attempt ownership."""
    async with context.session_factory() as db, db.begin():
        media_file = await db.get(MediaFile, media_file_id)
        if media_file is None or not media_file.is_active or not media_file.is_present:
            return False, "media_file_retired"
        if episode_id is not None:
            link = await db.get(EpisodeMediaFile, {"episode_id": episode_id, "media_file_id": media_file_id})
            if link is None:
                return False, "episode_file_relationship_changed"
        elif media_file.movie_id != movie_id:
            return False, "movie_file_relationship_changed"
        try:
            current = await resolve_media_file(db, media_file_id)
        except (MediaFileNotFoundError, MediaFileUnavailableError):
            return False, "media_file_unavailable"
        if current.signature != expected_signature:
            return False, "source_changed"
        if not await context.writer.owns_current_attempt(db):
            return False, "stale_fence"
        state = await db.scalar(select(DoviState).where(_state_predicate(movie_id, episode_id)))
        if state is None:
            state = DoviState(
                movie_id=movie_id,
                episode_id=episode_id,
                media_type="movie" if movie_id is not None else "episode",
            )
            db.add(state)
        next_values = {
            **dict(values),
            "source_signature": expected_signature,
            "source_fence_token": context.attempt.fence_token,
        }
        changed = any(getattr(state, key) != value for key, value in next_values.items())
        if not changed:
            return False, "analysis_unchanged"
        for key, value in next_values.items():
            setattr(state, key, value)
        state.last_analyzed_at = datetime.now(UTC)
    return True, None


async def execute_dovi_analyze(context: ExecutionContext) -> dict[str, Any]:
    """Probe one immutable media-file snapshot and persist only derived DoVi state."""
    if context.cancellation.cancel_called:
        raise asyncio.CancelledError
    request = context.request
    media_file_id = int(request.get("media_file_id") or context.subject.get("media_file_id") or 0)
    movie_id = request.get("movie_id")
    episode_id = request.get("episode_id")
    movie_id = int(movie_id) if movie_id is not None else None
    episode_id = int(episode_id) if episode_id is not None else None
    expected_signature = str(request.get("source_signature") or "")
    depth = str(request.get("analysis_depth") or "standard")
    if not media_file_id or not expected_signature or (movie_id is None) == (episode_id is None):
        raise DoviAnalysisError("Dolby Vision analysis identity is incomplete")

    source = await _load_source(context, media_file_id)
    if source.signature != expected_signature:
        return {
            "outcome": "no_change",
            "summary": {"media_file_id": media_file_id, "reason": "source_changed_before_analysis"},
        }
    if await _is_current(
        context,
        media_file_id=media_file_id,
        movie_id=movie_id,
        episode_id=episode_id,
        signature=source.signature,
        depth=depth,
    ):
        return {
            "outcome": "no_change",
            "summary": {"media_file_id": media_file_id, "reason": "analysis_up_to_date"},
        }

    await _progress(context, "probing", 1)
    probe = await _launch_json(context, "ffprobe", [*_FFPROBE_ARGS, str(source.path)])
    if context.cancellation.cancel_called:
        raise asyncio.CancelledError
    await _progress(context, "analyzing", 2)
    tool_summary: str | None = None
    tool_warning: str | None = None
    if depth == "deep":
        tool_summary, tool_warning = await _dovi_tool_summary(context, str(source.path))
    analysis = _parse_probe(
        probe,
        analysis_depth=depth,
        tool_summary=tool_summary,
        tool_warning=tool_warning,
    )
    await _progress(context, "validating", 3)
    if context.cancellation.cancel_called:
        raise asyncio.CancelledError
    changed, reason = await _store_analysis(
        context,
        media_file_id=media_file_id,
        movie_id=movie_id,
        episode_id=episode_id,
        expected_signature=source.signature,
        values=analysis,
    )
    warnings = json.loads(str(analysis["warnings_json"]))
    return {
        "outcome": "succeeded" if changed else "no_change",
        "summary": {
            "media_file_id": media_file_id,
            "status": analysis["status"],
            "profile": analysis["dovi_profile"],
            "level": analysis["dovi_level"],
            "analysis_depth": depth,
            "supported": analysis["analysis_supported"],
            "reason": reason,
            "warnings": warnings,
        },
    }


register_execution_handler("dovi_analyze", execute_dovi_analyze)
