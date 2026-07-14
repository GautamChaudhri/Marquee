"""Canonical JMC4B read-only letterbox observations.

This module deliberately does not call the legacy letterbox manager.  That
manager owns job lifecycle/progress and includes crop-application-era behavior;
these handlers only run tracked probes and persist derived observations.
"""

from __future__ import annotations

import asyncio
import json
import os
import statistics
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

from pgqueuer import RetryRequested
from sqlalchemy import select

from marquee.core.jobs.delivery import ExecutionContext, register_execution_handler
from marquee.core.jobs.process_launcher import ProcessLaunchError
from marquee.core.jobs.progress import MeasurementMode, ProgressMeasurementUpdate
from marquee.core.jobs.progress_service import ProgressObservation, progress_writer
from marquee.core.media_files import MediaFileUnavailableError, resolve_media_file
from marquee.media.letterbox_detect import (
    WindowMeasurement,
    _bars_from_box,
    _sample_offsets,
    consensus,
    cropdetect_limit_for,
    parse_cropdetect,
    parse_trim,
)
from marquee.models import Episode, EpisodeMediaFile, LetterboxEvent, LetterboxState

_FFPROBE_ARGS = (
    "-v",
    "error",
    "-print_format",
    "json",
    "-show_format",
    "-show_streams",
)


class LetterboxObservationError(RuntimeError):
    """Path-free permanent failure in read-only observation."""


def _tool_retry(tool: str) -> RetryRequested:
    """Use the definition retry budget for a missing or temporarily unavailable tool."""
    return RetryRequested(timedelta(seconds=5), reason=f"{tool} is temporarily unavailable")


def _frozen_detection_config(context: ExecutionContext) -> SimpleNamespace:
    """Return the enqueue-time detector settings, never live application settings."""
    values = context.request.get("detection_config")
    if not isinstance(values, Mapping):
        values = {}

    def value(key: str, default: Any) -> Any:
        return values.get(key, default)

    return SimpleNamespace(
        LETTERBOX_DETECT_METHOD=value("method", "cropdetect"),
        LETTERBOX_TRIM_FUZZ=tuple(value("trim_fuzz", (5, 15, 25))),
        LETTERBOX_MOVIE_SAMPLES_MIN=value("movie_samples_min", 5),
        LETTERBOX_MOVIE_SAMPLES_MAX=value("movie_samples_max", 60),
        LETTERBOX_MOVIE_SAMPLE_STEP=value("movie_sample_step", 5),
        LETTERBOX_TV_QUICK_WINDOWS=value("tv_quick_windows", 3),
        LETTERBOX_TV_THOROUGH_WINDOWS=value("tv_thorough_windows", 8),
        LETTERBOX_TV_HEAD_SKIP_PCT=value("tv_head_skip_pct", 12),
        LETTERBOX_TV_TAIL_SKIP_PCT=value("tv_tail_skip_pct", 12),
        LETTERBOX_WINDOW_SECONDS=value("window_seconds", 2),
        LETTERBOX_CROPDETECT_LIMIT=value("cropdetect_limit", 24),
        LETTERBOX_CROPDETECT_HDR_LIMIT=value("cropdetect_hdr_limit", 80),
        LETTERBOX_CROPDETECT_ROUND=value("cropdetect_round", 2),
        LETTERBOX_NOISE_PX=value("noise_px", 4),
        LETTERBOX_MIN_BAR_PX=value("min_bar_px", 8),
        LETTERBOX_AGREE_PX=value("agree_px", 2),
        LETTERBOX_MEDIUM_SPREAD_PX=value("medium_spread_px", 20),
        LETTERBOX_VARIABLE_GAP_PX=value("variable_gap_px", 40),
        LETTERBOX_VARIABLE_MIN_FRACTION=value("variable_min_fraction", 0.2),
        LETTERBOX_ASYM_PX=value("asym_px", 2),
        LETTERBOX_ASYMMETRIC=value("asymmetric", False),
        LETTERBOX_EARLY_STOP_WINDOWS=value("early_stop_windows", 3),
    )


async def _progress(context: ExecutionContext, stage: str, ordinal: int, scope: str) -> None:
    await progress_writer.safe_write(
        job_id=context.delivery.canonical_job_id,
        attempt_id=context.attempt.attempt_id,
        fence_token=context.attempt.fence_token,
        observation=ProgressObservation(
            stage_key=stage,
            overall=ProgressMeasurementUpdate(
                scope_id="letterbox-observation:overall", mode=MeasurementMode.INDETERMINATE
            ),
            current=ProgressMeasurementUpdate(
                scope_id=f"letterbox-observation:{scope}:{stage}", mode=MeasurementMode.INDETERMINATE
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
        raise LetterboxObservationError("letterbox media probe failed")
    try:
        return json.loads(result.stdout.captured.decode("utf-8", "replace"))
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise LetterboxObservationError("letterbox probe returned invalid data") from exc


def _video_info(probe: dict[str, Any]) -> tuple[int, int, float | None, str | None, str | None]:
    stream = next((item for item in probe.get("streams", []) if item.get("codec_type") == "video"), None)
    if not isinstance(stream, dict):
        raise LetterboxObservationError("media has no video stream")
    try:
        width, height = int(stream["width"]), int(stream["height"])
    except (KeyError, TypeError, ValueError) as exc:
        raise LetterboxObservationError("media dimensions are unavailable") from exc
    if width <= 0 or height <= 0:
        raise LetterboxObservationError("media dimensions are unavailable")
    raw_duration = (probe.get("format") or {}).get("duration") or stream.get("duration")
    try:
        duration = float(raw_duration) if raw_duration is not None else None
    except (TypeError, ValueError):
        duration = None
    return width, height, duration, stream.get("color_transfer"), stream.get("codec_name")


async def _crop_window(
    context: ExecutionContext,
    *,
    path: str,
    minute: int,
    offset_seconds: int,
    full_height: int,
    color_transfer: str | None,
    config: SimpleNamespace,
) -> WindowMeasurement:
    limit = cropdetect_limit_for(color_transfer, config=config)
    args = [
        "-hide_banner",
        "-nostats",
        "-ss",
        str(offset_seconds),
        "-i",
        path,
        "-an",
        "-sn",
        "-t",
        str(config.LETTERBOX_WINDOW_SECONDS),
        "-vf",
        f"cropdetect=limit={limit}:round={config.LETTERBOX_CROPDETECT_ROUND}:reset=1",
        "-f",
        "null",
        "-",
    ]
    try:
        process = await context.process_launcher.launch("ffmpeg", args)
    except ProcessLaunchError as exc:
        raise _tool_retry("ffmpeg") from exc
    result = await process.wait()
    crop = parse_cropdetect(result.stderr.captured.decode("utf-8", "replace"))
    if result.exit_code != 0 or crop is None:
        return WindowMeasurement(minute=minute, ok=False, error="sample could not be measured")
    width, height, _x, y = crop
    top, bottom = _bars_from_box(full_height, height, y)
    return WindowMeasurement(
        minute=minute,
        ok=True,
        top_bar=top,
        bottom_bar=bottom,
        width=width,
        height=height,
        backend="ffmpeg_cropdetect",
    )


def _workspace_file_path(staged: Any) -> str:
    return str(staged.root.resolved().joinpath(*staged.key.parts))


async def _trim_window(
    context: ExecutionContext,
    *,
    path: str,
    minute: int,
    offset_seconds: int,
    full_height: int,
    config: SimpleNamespace,
) -> WindowMeasurement:
    """Measure one frame through the confined workspace and tracked ImageMagick tool."""
    staged, write_fd = context.workspace.staging_file(f"letterbox-frame-{minute}-{offset_seconds}.png")
    frame_path = _workspace_file_path(staged)
    try:
        os.close(write_fd)
        try:
            extract = await context.process_launcher.launch(
                "ffmpeg",
                [
                    "-y",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-ss",
                    str(offset_seconds),
                    "-i",
                    path,
                    "-frames:v",
                    "1",
                    "-q:v",
                    "2",
                    frame_path,
                ],
            )
        except ProcessLaunchError as exc:
            raise _tool_retry("ffmpeg") from exc
        if (await extract.wait()).exit_code != 0:
            return WindowMeasurement(minute=minute, ok=False, error="frame extract failed")
        tops: list[int] = []
        bottoms: list[int] = []
        for fuzz in config.LETTERBOX_TRIM_FUZZ:
            if context.cancellation.cancel_called:
                raise asyncio.CancelledError
            try:
                trimmed = await context.process_launcher.launch(
                    "convert",
                    [
                        frame_path,
                        "-fuzz",
                        f"{fuzz}%",
                        "-trim",
                        "+repage",
                        "-format",
                        "%wx%h+%X+%Y",
                        "info:",
                    ],
                )
            except ProcessLaunchError as exc:
                raise _tool_retry("ImageMagick") from exc
            result = await trimmed.wait()
            box = parse_trim(result.stdout.captured.decode("utf-8", "replace"))
            if result.exit_code != 0 or box is None:
                continue
            _width, height, _x, y = box
            top, bottom = _bars_from_box(full_height, height, y)
            tops.append(top)
            bottoms.append(bottom)
        if not tops:
            return WindowMeasurement(minute=minute, ok=False, error="trim produced no box")
        top = round(statistics.median(tops))
        bottom = round(statistics.median(bottoms))
        return WindowMeasurement(
            minute=minute,
            ok=True,
            top_bar=top,
            bottom_bar=bottom,
            height=full_height - top - bottom,
            backend="imagemagick_trim",
        )
    finally:
        context.workspace.boundary.delete_file(staged, missing_ok=True)


async def _measure_window(
    context: ExecutionContext,
    *,
    path: str,
    minute: int,
    offset_seconds: int,
    full_height: int,
    color_transfer: str | None,
    config: SimpleNamespace,
) -> WindowMeasurement:
    if config.LETTERBOX_DETECT_METHOD == "trim":
        return await _trim_window(
            context,
            path=path,
            minute=minute,
            offset_seconds=offset_seconds,
            full_height=full_height,
            config=config,
        )
    return await _crop_window(
        context,
        path=path,
        minute=minute,
        offset_seconds=offset_seconds,
        full_height=full_height,
        color_transfer=color_transfer,
        config=config,
    )


async def _observe_media(
    context: ExecutionContext, media_file_id: int, *, is_tv: bool, thorough: bool
) -> dict[str, Any]:
    async with context.session_factory() as db:
        try:
            resolved = await resolve_media_file(db, media_file_id)
        except MediaFileUnavailableError as exc:
            raise LetterboxObservationError("media file is unavailable") from exc
        path = str(resolved.path)
    await _progress(context, "probing", 1, f"{media_file_id}:probe")
    probe = await _launch_json(context, "ffprobe", [*_FFPROBE_ARGS, path])
    width, height, duration, color_transfer, codec = _video_info(probe)
    config = _frozen_detection_config(context)

    async def collect(
        *, selected_thorough: bool, pass_name: str, ordinal_start: int
    ) -> tuple[list[WindowMeasurement], int]:
        schedule = _sample_offsets(
            duration, is_tv=is_tv, thorough=selected_thorough, config=config
        )
        measurements: list[WindowMeasurement] = []
        consecutive_clear = 0
        ordinal = ordinal_start
        for minute, absolute_offset in schedule:
            if context.cancellation.cancel_called:
                raise asyncio.CancelledError
            offset_seconds = minute * 60 if absolute_offset is None else absolute_offset
            await _progress(
                context,
                "sampling",
                ordinal,
                f"{media_file_id}:{pass_name}:{offset_seconds}",
            )
            measurement = await _measure_window(
                context,
                path=path,
                minute=minute,
                offset_seconds=offset_seconds,
                full_height=height,
                color_transfer=color_transfer,
                config=config,
            )
            measurements.append(measurement)
            ordinal += 1
            if not selected_thorough and measurement.ok and measurement.bar <= config.LETTERBOX_NOISE_PX:
                consecutive_clear += 1
                if consecutive_clear >= config.LETTERBOX_EARLY_STOP_WINDOWS:
                    break
            elif measurement.ok:
                consecutive_clear = 0
        return measurements, ordinal

    measurements, ordinal = await collect(
        selected_thorough=thorough,
        pass_name="thorough" if thorough else "quick",
        ordinal_start=2,
    )
    if not any(measurement.ok for measurement in measurements):
        raise LetterboxObservationError("no sample windows could be measured")
    result = consensus(
        measurements,
        width=width,
        height=height,
        method=config.LETTERBOX_DETECT_METHOD,
        config=config,
    )
    if (
        is_tv
        and not thorough
        and any(measurement.ok and measurement.bar > config.LETTERBOX_MIN_BAR_PX for measurement in measurements)
    ):
        measurements, ordinal = await collect(
            selected_thorough=True,
            pass_name="thorough",
            ordinal_start=ordinal,
        )
        if not any(measurement.ok for measurement in measurements):
            raise LetterboxObservationError("no sample windows could be measured")
        result = consensus(
            measurements,
            width=width,
            height=height,
            method=config.LETTERBOX_DETECT_METHOD,
            config=config,
        )
        thorough = True
    await _progress(context, "validating", ordinal, f"{media_file_id}:validation")
    warnings = sorted({measurement.error for measurement in measurements if measurement.error})[:20]
    return {
        "status": result.status,
        "confidence": result.confidence,
        "source_width": result.source_width,
        "source_height": result.source_height,
        "source_codec": codec,
        "recommended_crop_top": result.recommended_crop_top,
        "recommended_crop_bottom": result.recommended_crop_bottom,
        "aspect_label": result.aspect_label,
        "detect_method": result.method,
        "samples_json": json.dumps(result.samples, separators=(",", ":"), sort_keys=True),
        "error": result.error,
        "variable_ar": result.variable_ar,
        "variable_ar_note": result.variable_ar_note,
        "sample_count": len(measurements),
        "sampling_scope": "tv_thorough" if is_tv and thorough else "tv_quick" if is_tv else "movie_thorough" if thorough else "movie_standard",
        "warnings": warnings,
    }


async def _store_observation(
    context: ExecutionContext,
    *,
    media_type: str,
    subject_ids: list[int],
    observation: dict[str, Any],
) -> bool:
    """Persist only a derived observation and return whether the projection changed."""
    now = datetime.now(UTC)
    changed_any = False
    projection_keys = (
        "status",
        "confidence",
        "source_width",
        "source_height",
        "recommended_crop_top",
        "recommended_crop_bottom",
        "aspect_label",
        "detect_method",
        "samples_json",
        "error",
        "variable_ar",
        "variable_ar_note",
    )
    async with context.session_factory() as db, db.begin():
        for subject_id in subject_ids:
            predicate = (
                LetterboxState.movie_id == subject_id
                if media_type == "movie"
                else LetterboxState.episode_id == subject_id
            )
            state = await db.scalar(select(LetterboxState).where(predicate))
            if state is None:
                state = (
                    LetterboxState(media_type="movie", movie_id=subject_id)
                    if media_type == "movie"
                    else LetterboxState(media_type="episode", episode_id=subject_id)
                )
                db.add(state)
            next_values = {key: observation[key] for key in projection_keys}
            # A prior crop application is explicitly deferred work.  Detection must never
            # erase its status or touch the applied-crop columns while recording evidence.
            if state.status == "tagged":
                next_values["status"] = "tagged"
            changed = any(getattr(state, key) != value for key, value in next_values.items())
            for key, value in next_values.items():
                setattr(state, key, value)
            state.last_detected_at = now
            if observation["status"] in {"not_letterboxed", "variable_unsafe"}:
                state.reviewed = True
            if changed:
                changed_any = True
                db.add(
                    LetterboxEvent(
                        media_type=media_type,
                        movie_id=subject_id if media_type == "movie" else None,
                        episode_id=subject_id if media_type == "episode" else None,
                        subject_snapshot={
                            "canonical_job_id": str(context.delivery.canonical_job_id),
                            "fence_token": context.attempt.fence_token,
                        },
                        action="detect",
                        source="detect",
                        detail=json.dumps(
                            {
                                "confidence": observation["confidence"],
                                "sample_count": observation["sample_count"],
                                "sampling_scope": observation["sampling_scope"],
                                "status": observation["status"],
                                "warnings": observation["warnings"],
                            },
                            separators=(",", ":"),
                            sort_keys=True,
                        ),
                    )
                )
    return changed_any


async def execute_letterbox_detect(context: ExecutionContext) -> dict[str, Any]:
    """Observe one movie without tagging, applying, re-encoding, or healing media."""
    media_file_id = int(context.subject.get("media_file_id") or context.request.get("media_file_id") or 0)
    movie_id = int(context.subject.get("movie_id") or context.request.get("movie_id") or 0)
    if not media_file_id or not movie_id:
        raise LetterboxObservationError("movie and media-file identity are required")
    observation = await _observe_media(
        context, media_file_id, is_tv=False, thorough=bool(context.request.get("thorough", False))
    )
    changed = await _store_observation(
        context, media_type="movie", subject_ids=[movie_id], observation=observation
    )
    return {
        "outcome": "succeeded" if changed else "no_change",
        "summary": {
            "movie_id": movie_id,
            "media_file_id": media_file_id,
            "status": observation["status"],
            "confidence": observation["confidence"],
            "sample_count": observation["sample_count"],
            "sampling_scope": observation["sampling_scope"],
            "detect_method": observation["detect_method"],
            "reason": None if changed else "observation_unchanged",
            "warnings": observation["warnings"],
        },
    }


async def execute_letterbox_detect_episode(context: ExecutionContext) -> dict[str, Any]:
    """Observe one physical episode file and fan out its derived state to its linked episodes."""
    media_file_id = int(context.subject.get("media_file_id") or context.request.get("media_file_id") or 0)
    episode_ids = [int(value) for value in context.request.get("episode_ids", ())]
    if not media_file_id or not episode_ids:
        raise LetterboxObservationError("episode group identity is required")
    observation = await _observe_media(
        context, media_file_id, is_tv=True, thorough=bool(context.request.get("thorough", False))
    )
    changed = await _store_observation(
        context, media_type="episode", subject_ids=episode_ids, observation=observation
    )
    return {
        "outcome": "succeeded" if changed else "no_change",
        "summary": {
            "episode_ids": episode_ids,
            "media_file_id": media_file_id,
            "status": observation["status"],
            "confidence": observation["confidence"],
            "sample_count": observation["sample_count"],
            "sampling_scope": observation["sampling_scope"],
            "detect_method": observation["detect_method"],
            "reason": None if changed else "observation_unchanged",
            "warnings": observation["warnings"],
        },
    }


async def execute_letterbox_detect_tv_scope(context: ExecutionContext) -> dict[str, Any]:
    """Observe the physical episode-file groups selected by an immutable TV scope."""
    series_id = int(context.request.get("series_id") or context.subject.get("series_id") or 0)
    if not series_id:
        raise LetterboxObservationError("series identity is required")
    season_number = context.request.get("season_number")
    episode_id = context.request.get("episode_id")
    async with context.session_factory() as db:
        statement = (
            select(EpisodeMediaFile.media_file_id, Episode.id)
            .join(Episode, Episode.id == EpisodeMediaFile.episode_id)
            .where(Episode.series_id == series_id)
            .order_by(EpisodeMediaFile.media_file_id, Episode.id)
        )
        if episode_id is not None:
            statement = statement.where(Episode.id == int(episode_id))
        elif season_number is not None:
            statement = statement.where(Episode.season_number == int(season_number))
        rows = (await db.execute(statement)).all()
    groups: dict[int, list[int]] = {}
    for media_file_id, linked_episode_id in rows:
        groups.setdefault(media_file_id, []).append(linked_episode_id)
    if not groups:
        return {"outcome": "no_change", "summary": {"reason": "no_episode_media_files"}}
    changed_any = False
    for media_file_id, episode_ids in groups.items():
        if context.cancellation.cancel_called:
            raise asyncio.CancelledError
        observation = await _observe_media(
            context, media_file_id, is_tv=True, thorough=bool(context.request.get("exhaustive", False))
        )
        changed_any = await _store_observation(
            context, media_type="episode", subject_ids=episode_ids, observation=observation
        ) or changed_any
    return {
        "outcome": "succeeded" if changed_any else "no_change",
        "summary": {
            "series_id": series_id,
            "physical_files_observed": len(groups),
            "reason": None if changed_any else "observations_unchanged",
        },
    }


register_execution_handler("letterbox_detect", execute_letterbox_detect)
register_execution_handler("letterbox_detect_episode", execute_letterbox_detect_episode)
register_execution_handler("letterbox_detect_tv_scope", execute_letterbox_detect_tv_scope)
