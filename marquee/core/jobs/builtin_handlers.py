"""Central handlers for non-media workflows."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import exists, or_, select

from marquee.config import settings
from marquee.core.jobs import cancel_registry, job_manager
from marquee.core.jobs.handlers import register
from marquee.core.media_files import MediaFileUnavailableError, resolve_media_file
from marquee.core.poster_subjects import PosterSubject
from marquee.core.subtitles.config import subtitle_settings
from marquee.core.tv_queries import season_downloaded, series_visible
from marquee.database import _get_session_factory
from marquee.ml.namespaces import TasteNamespace, get_namespace
from marquee.models import (
    ArtworkEvent,
    Episode,
    EpisodeMediaFile,
    Job,
    MediaFile,
    Movie,
    PipelineRun,
    Season,
    Series,
    SubtitleInventory,
)

logger = logging.getLogger(__name__)


async def _update_progress(
    job_id: str, stage: str, done: int, total: int, message: str | None = None, **detail: Any
) -> None:
    factory = _get_session_factory()
    async with factory() as db:
        current = await db.get(Job, job_id)
        if current is None:
            return
        text = message or f"{stage}: {done}/{total}"
        # message rides inside the progress dict so both delivery paths — the
        # polled job.progress snapshot and the SSE event detail — can show it.
        progress = {
            "stage": stage,
            "state": "running",
            "done": done,
            "total": total,
            "message": text,
            **detail,
        }
        current.current_stage = stage
        current.progress = progress
        await job_manager.emit(
            db,
            current,
            state="running",
            stage=stage,
            message=text,
            detail=progress,
        )
        await db.commit()


async def _emit_stage(job_id: str, stage: str, message: str, **detail: Any) -> None:
    """Persist a countless stage transition ("Syncing movies from Radarr…")."""
    factory = _get_session_factory()
    async with factory() as db:
        current = await db.get(Job, job_id)
        if current is None:
            return
        progress = {"stage": stage, "state": "running", "message": message, **detail}
        current.current_stage = stage
        current.progress = progress
        await job_manager.emit(
            db, current, state="running", stage=stage, message=message, detail=progress
        )
        await db.commit()


def _downloaded_condition():
    return or_(
        Movie.movie_file_path.is_not(None),
        exists(
            select(MediaFile.id).where(
                MediaFile.movie_id == Movie.id,
                MediaFile.is_active.is_(True),
            )
        ),
    )


def _subtitle_scan_stmt(
    scope: str,
    *,
    series_id: int | None = None,
    season_number: int | None = None,
):
    if scope == "movies":
        return select(MediaFile).where(MediaFile.is_active.is_(True), MediaFile.movie_id.is_not(None))
    if scope == "tv":
        return (
            select(MediaFile)
            .join(EpisodeMediaFile, EpisodeMediaFile.media_file_id == MediaFile.id)
            .join(Episode, Episode.id == EpisodeMediaFile.episode_id)
            .join(Series, Series.id == Episode.series_id)
            .join(
                Season,
                (Season.series_id == Episode.series_id)
                & (Season.season_number == Episode.season_number),
            )
            .where(MediaFile.is_active.is_(True), series_visible(), season_downloaded())
        )
    if scope == "series":
        if series_id is None:
            raise RuntimeError("series_id is required when scope='series'")
        stmt = (
            select(MediaFile)
            .join(EpisodeMediaFile, EpisodeMediaFile.media_file_id == MediaFile.id)
            .join(Episode, Episode.id == EpisodeMediaFile.episode_id)
            .join(
                Season,
                (Season.series_id == Episode.series_id)
                & (Season.season_number == Episode.season_number),
            )
            .where(
                MediaFile.is_active.is_(True),
                Episode.series_id == series_id,
                season_downloaded(),
            )
        )
        if season_number is not None:
            stmt = stmt.where(Episode.season_number == season_number)
        return stmt
    if scope == "all":
        return select(MediaFile).where(MediaFile.is_active.is_(True))
    raise RuntimeError(f"unsupported subtitle scan scope: {scope}")


async def _stale_or_missing_subtitle_scan_candidates(
    db,
    *,
    scope: str,
    force: bool,
    series_id: int | None = None,
    season_number: int | None = None,
    limit: int | None = None,
) -> list[MediaFile]:
    stmt = _subtitle_scan_stmt(scope, series_id=series_id, season_number=season_number)
    stmt = stmt.outerjoin(SubtitleInventory, SubtitleInventory.media_file_id == MediaFile.id).order_by(
        SubtitleInventory.scanned_at.asc().nullsfirst(),
        MediaFile.id.asc(),
    )
    media_files = (await db.execute(stmt)).scalars().unique().all()

    candidates: list[MediaFile] = []
    for media_file in media_files:
        if force:
            candidates.append(media_file)
        else:
            inventory = (
                await db.execute(
                    select(SubtitleInventory).where(SubtitleInventory.media_file_id == media_file.id)
                )
            ).scalar_one_or_none()
            if inventory is None:
                candidates.append(media_file)
            else:
                try:
                    resolved = await resolve_media_file(db, media_file.id)
                except MediaFileUnavailableError:
                    logger.warning(
                        "subtitle scan candidate media_file_id=%s is unavailable; skipping",
                        media_file.id,
                    )
                    continue
                if inventory.file_signature != resolved.signature:
                    candidates.append(media_file)
        if limit is not None and len(candidates) >= limit:
            break
    return candidates


@register("poster_heal")
async def poster_heal(_job: Job) -> dict[str, Any]:
    from marquee.core.heal import heal_scan  # noqa: PLC0415

    return await heal_scan()


@register("letterbox_heal")
async def letterbox_heal(_job: Job) -> dict[str, Any]:
    from marquee.core.letterbox_heal import letterbox_heal_scan  # noqa: PLC0415

    return await letterbox_heal_scan()


@register("letterbox_detect")
async def letterbox_detect(job: Job) -> dict[str, Any]:
    from marquee.media.binaries import BinaryError  # noqa: PLC0415
    from marquee.media.letterbox_manager import letterbox_manager  # noqa: PLC0415

    factory = _get_session_factory()
    movie_id = int(job.request["movie_id"])

    async with factory() as db:
        movie = await db.get(Movie, movie_id)
        if movie is None:
            raise RuntimeError("movie not found")

        # Emit child_started event if this is part of a batch
        if job.parent_id:
            parent = await db.get(Job, job.parent_id)
            if parent:
                try:
                    await job_manager.emit(
                        db,
                        parent,
                        state="child_progress",
                        message=f"Analyzing {movie.title}",
                        detail={
                            "movie_id": movie_id,
                            "title": movie.title,
                            "stage": "started",
                            "progress": 0,
                        },
                    )
                except Exception:  # noqa: BLE001 - progress must not fail detection
                    logger.exception(
                        "could not emit letterbox child-start progress for movie %d", movie.id
                    )

        try:
            # Pass job context for progress emission
            state = await letterbox_manager.detect_and_store(
                db,
                movie,
                detector=job.request.get("detector", "v2"),
                thorough=bool(job.request.get("thorough", False)),
                parent_job_id=job.parent_id,
            )
            return {"movie_id": movie.id, "status": state.status, "confidence": state.confidence}
        except BinaryError as exc:
            # Soft-fail on ffprobe timeouts to prevent cascading batch failure
            # The movie is marked as errored but the batch continues
            if "timed out" in str(exc).lower():
                logger.warning(
                    "ffprobe timeout for movie %d (%s) - marking as errored", movie.id, movie.title
                )
                return {
                    "movie_id": movie.id,
                    "status": "errored",
                    "error": "Media probe timed out after multiple retries - possible network storage issue",
                    "skipped": True,
                }
            # Re-raise other binary errors (missing ffprobe, etc.)
            raise


@register("letterbox_detect_episode")
async def letterbox_detect_episode(job: Job) -> dict[str, Any]:
    from marquee.media.letterbox_manager import letterbox_manager  # noqa: PLC0415

    factory = _get_session_factory()
    episode_ids = [int(episode_id) for episode_id in job.request.get("episode_ids", [])]
    media_file_id = job.request.get("media_file_id")
    if not episode_ids or media_file_id is None:
        raise RuntimeError("episode_ids and media_file_id are required")

    async with factory() as db:
        episodes = (
            await db.execute(select(Episode).where(Episode.id.in_(episode_ids)).order_by(Episode.id))
        ).scalars().all()
        if not episodes:
            raise RuntimeError("episodes not found")

        parent = await db.get(Job, job.parent_id) if job.parent_id else None
        if parent is not None:
            for episode in episodes:
                try:
                    await job_manager.emit(
                        db,
                        parent,
                        state="child_progress",
                        message=f"Analyzing episode {episode.id}",
                        detail={
                            "episode_id": episode.id,
                            "stage": "started",
                            "progress": 0,
                        },
                    )
                except Exception:  # noqa: BLE001
                    logger.exception(
                        "could not emit letterbox child-start progress for episode %d", episode.id
                    )

        states = await letterbox_manager.detect_episode_group_and_store(
            db,
            episodes,
            media_file_id=int(media_file_id),
            thorough=bool(job.request.get("thorough", False)),
            parent_job_id=job.parent_id,
        )
        first = states[0] if states else None
        return {
            "episode_ids": episode_ids,
            "status": first.status if first is not None else "errored",
            "confidence": first.confidence if first is not None else "none",
        }


def _summarize_tv_letterbox_states(states: list[Any]) -> tuple[str, dict[str, int]]:
    counts: dict[str, int] = {}
    for state in states:
        counts[state.status] = counts.get(state.status, 0) + 1
    for status in (
        "candidate",
        "tagged",
        "reencoded",
        "sampled_clear",
        "not_letterboxed",
        "variable_unsafe",
        "ineligible",
        "errored",
    ):
        if counts.get(status):
            return status, counts
    return "prefilter_candidate", counts


@register("letterbox_detect_tv_scope")
async def letterbox_detect_tv_scope(job: Job) -> dict[str, Any]:
    from marquee.media.letterbox_manager import letterbox_manager  # noqa: PLC0415

    factory = _get_session_factory()
    series_id = int(job.request["series_id"])
    season_number = job.request.get("season_number")
    episode_id = job.request.get("episode_id")
    exhaustive = bool(job.request.get("exhaustive", False))
    force = bool(job.request.get("force", False))
    include_open_matte = bool(job.request.get("include_open_matte", False))

    async with factory() as db:
        series = await db.get(Series, series_id)
        if series is None:
            raise RuntimeError("series not found")

        episode_query = select(Episode).where(
            Episode.series_id == series_id,
            Episode.episode_file_path.is_not(None),
            Episode.episode_file_path != "",
        )
        if episode_id is not None:
            episode_query = episode_query.where(Episode.id == int(episode_id))
        elif season_number is not None:
            episode_query = episode_query.where(Episode.season_number == int(season_number))
        episodes = (
            await db.execute(
                episode_query.order_by(
                    Episode.season_number,
                    Episode.episode_number,
                    Episode.id,
                )
            )
        ).scalars().all()
        if not episodes:
            raise RuntimeError("episodes not found")

        parent = await db.get(Job, job.parent_id) if job.parent_id else None
        if parent is not None:
            for episode in episodes:
                try:
                    await job_manager.emit(
                        db,
                        parent,
                        state="child_progress",
                        message=f"Analyzing S{episode.season_number:02d}E{episode.episode_number:02d}",
                        detail={
                            "episode_id": episode.id,
                            "stage": "started",
                            "progress": 0,
                            "title": episode.title,
                        },
                    )
                except Exception:  # noqa: BLE001
                    logger.exception(
                        "could not emit letterbox child-start progress for episode %d", episode.id
                    )

        states = await letterbox_manager.detect_episode_batch_and_store(
            db,
            episodes,
            exhaustive=exhaustive,
            force=force,
            include_open_matte=include_open_matte,
            use_season_triage=episode_id is None,
            parent_job_id=job.parent_id,
        )
        summary_status, counts = _summarize_tv_letterbox_states(states)
        return {
            "series_id": series_id,
            "season_number": int(season_number) if season_number is not None else None,
            "episode_id": int(episode_id) if episode_id is not None else None,
            "status": summary_status,
            "counts": counts,
            "episodes": len(states),
        }


@register("letterbox_apply")
async def letterbox_apply(job: Job) -> dict[str, Any]:
    from marquee.core.letterbox_service import letterbox_service  # noqa: PLC0415

    factory = _get_session_factory()
    async with factory() as db:
        movie = await db.get(Movie, int(job.request["movie_id"]))
        if movie is None:
            raise RuntimeError("movie not found")
        result = await letterbox_service.apply(
            db, movie, top=int(job.request["top"]), bottom=int(job.request["bottom"]), source="job"
        )
        return {
            "applied": result.applied,
            "top": result.top,
            "bottom": result.bottom,
            "verified": result.verified,
        }


@register("letterbox_remove")
async def letterbox_remove(job: Job) -> dict[str, Any]:
    from marquee.core.letterbox_service import letterbox_service  # noqa: PLC0415

    factory = _get_session_factory()
    async with factory() as db:
        movie = await db.get(Movie, int(job.request["movie_id"]))
        if movie is None:
            raise RuntimeError("movie not found")
        result = await letterbox_service.remove(db, movie, source="job")
        return {"removed": result.removed, "path": result.path}


@register("letterbox_apply_tv_scope")
async def letterbox_apply_tv_scope(job: Job) -> dict[str, Any]:
    from marquee.core.letterbox_tv_scope import apply_tv_scope  # noqa: PLC0415

    factory = _get_session_factory()
    series_id = int(job.request["series_id"])
    season_number = job.request.get("season_number")
    confidence_levels = job.request.get("confidence_levels")

    async def progress(done: int, total: int) -> None:
        await _update_progress(job.id, "apply", done, total)

    async with factory() as db:
        return await apply_tv_scope(
            db,
            series_id,
            season_number=int(season_number) if season_number is not None else None,
            confidence_levels=confidence_levels,
            progress=progress,
        )


@register("letterbox_revert_tv_scope")
async def letterbox_revert_tv_scope(job: Job) -> dict[str, Any]:
    from marquee.core.letterbox_tv_scope import revert_tv_scope  # noqa: PLC0415

    factory = _get_session_factory()
    series_id = int(job.request["series_id"])
    season_number = job.request.get("season_number")

    async def progress(done: int, total: int) -> None:
        await _update_progress(job.id, "revert", done, total)

    async with factory() as db:
        return await revert_tv_scope(
            db,
            series_id,
            season_number=int(season_number) if season_number is not None else None,
            progress=progress,
        )


@register("backup_create")
async def backup_create(_job: Job) -> dict[str, Any]:
    from marquee.core.backup import backup_service  # noqa: PLC0415

    return (await backup_service.create_backup()).to_dict()


@register("taste_rebuild")
async def taste_rebuild(job: Job) -> dict[str, Any]:
    """Rebuild the taste profile outside FastAPI; the worker owns the GPU lease.

    ``payload.source`` selects the exemplar set:
      * ``"training_dir"`` (default) — the curated positive folder(s).
      * ``"library"`` — every currently-deployed poster.
    """
    from marquee.core.pipeline_config import pipeline_settings  # noqa: PLC0415
    from marquee.database import _get_session_factory  # noqa: PLC0415
    from marquee.ml import artifact_registry  # noqa: PLC0415
    from marquee.ml.head_trainer import train_from_labels  # noqa: PLC0415
    from marquee.ml.taste_trainer import rebuild_profile  # noqa: PLC0415
    from marquee.pipeline.progress_bridge import JobProgressBridge  # noqa: PLC0415
    from marquee.pipeline.run_manager import run_manager  # noqa: PLC0415

    cancel_event = cancel_registry.get(job.id)
    source = job.request.get("source", "training_dir")
    library_name = job.request.get("library", "movies")
    ns = get_namespace(library_name)

    training_dir: Path | None = None
    tmp = None
    gathered: int | None = None
    if source == "library":
        cancel_registry.raise_if_cancelled(cancel_event, "taste rebuild cancelled")
        await _emit_stage(job.id, "gather", "Gathering library posters as exemplars…")
        training_dir, tmp, gathered = await _gather_library_posters(ns)
    try:
        # The trainer reports per-batch/per-exemplar progress from its worker
        # thread (clip → calibration substages → dino); the bridge marshals
        # those onto the job stream with throttling.
        async with JobProgressBridge(job.id) as bridge:
            await asyncio.to_thread(
                rebuild_profile,
                training_dir=training_dir,
                progress_callback=bridge.callback,
                cancel_event=cancel_event,
                namespace=ns,
            )
        cancel_registry.raise_if_cancelled(cancel_event, "taste rebuild cancelled")
        if pipeline_settings.HEAD_AUTO_RETRAIN:
            await _emit_stage(job.id, "train_head", "Training learned ranking head…")
            head = await asyncio.to_thread(train_from_labels, cancel_event=cancel_event, namespace=ns)
        else:
            head = None
        async with _get_session_factory()() as db:
            if (await artifact_registry.registry_status(db))["available"]:
                await artifact_registry.register_active_artifact(
                    db,
                    ns.artifact_kind_profile,
                    source_mode=source,
                )
                if head and head[0] is not None:
                    await artifact_registry.register_active_artifact(
                        db,
                        ns.artifact_kind_head,
                        info=head[1],
                    )
    finally:
        if tmp is not None:
            tmp.cleanup()
    # The next pipeline run in this worker must reload the rebuilt profile.
    run_manager.reset_extractor()
    return {
        "rebuild": "completed",
        "source": source,
        "library": library_name,
        "exemplars_gathered": gathered,
        "head": head[1] if head else None,
    }


@register("taste_map")
async def taste_map(job: Job) -> dict[str, Any]:
    from marquee.ml.taste_map import build_map  # noqa: PLC0415
    from marquee.pipeline.progress_bridge import JobProgressBridge  # noqa: PLC0415

    library_name = job.request.get("library", "movies")
    ns = get_namespace(library_name)

    cancel_event = cancel_registry.get(job.id)
    async with JobProgressBridge(job.id) as bridge:
        return await asyncio.to_thread(
            build_map, progress_callback=bridge.callback, cancel_event=cancel_event, namespace=ns
        )


@register("library_sync")
async def library_sync(job: Job) -> dict[str, Any]:
    from marquee.core.arr_clients.radarr_client import RadarrClient  # noqa: PLC0415
    from marquee.core.arr_clients.sonarr_client import SonarrClient  # noqa: PLC0415
    from marquee.core.poster_sources.tmdb import TMDBClient  # noqa: PLC0415
    from marquee.core.sync_service import SyncService  # noqa: PLC0415

    radarr = (
        RadarrClient(settings.RADARR_URL, settings.RADARR_API_KEY)
        if settings.radarr_configured
        else None
    )
    sonarr = (
        SonarrClient(settings.SONARR_URL, settings.SONARR_API_KEY)
        if settings.sonarr_configured
        else None
    )
    tmdb = (
        TMDBClient(read_access_token=settings.TMDB_READ_ACCESS_TOKEN)
        if settings.tmdb_configured
        else None
    )
    for client in (radarr, sonarr, tmdb):
        if client is not None:
            await client.connect()

    cancel_event = cancel_registry.get(job.id)

    async def _phase_progress(stage: str, message: str) -> None:
        cancel_registry.raise_if_cancelled(cancel_event, "library sync cancelled")
        await _emit_stage(job.id, stage, message)

    try:
        factory = _get_session_factory()
        async with factory() as db:
            report = await SyncService(db, radarr=radarr, sonarr=sonarr, tmdb=tmdb).sync_all(
                progress=_phase_progress,
                cancel_event=cancel_event,
            )
        return {
            "duration_seconds": report.duration_seconds,
            "movies": report.movies.__dict__,
            "series": report.series.__dict__,
            "seasons": report.seasons.__dict__,
            "episodes": report.episodes.__dict__,
        }
    finally:
        for client in (radarr, sonarr, tmdb):
            if client is not None:
                await client.disconnect()


@register("subtitle_scan_all")
async def subtitle_scan_all(job: Job) -> dict[str, Any]:
    """Scan subtitle coverage for all active media files in the library."""
    from marquee.core.media_jobs import media_job_manager  # noqa: PLC0415

    cancel_event = cancel_registry.get(job.id)
    force = job.request.get("force", False)
    scope = job.request.get("scope", "movies")
    series_id = job.request.get("series_id")
    season_number = job.request.get("season_number")
    factory = _get_session_factory()
    async with factory() as db:
        media_files = await _stale_or_missing_subtitle_scan_candidates(
            db,
            scope=scope,
            force=force,
            series_id=series_id,
            season_number=season_number,
        )

        count = 0
        total = len(media_files)
        for index, mf in enumerate(media_files, 1):
            cancel_registry.raise_if_cancelled(cancel_event, "subtitle scan-all cancelled")
            if index == 1 or index % 25 == 0 or index == total:
                await _update_progress(
                    job.id, "queue", index, total, message=f"Queueing scan {index}/{total}"
                )
            key = f"manual:subtitle-scan:{mf.id}:{job.id}"
            await media_job_manager.create_job(
                db,
                operation="subtitle_scan",
                media_file_id=mf.id,
                trigger="manual",
                status="queued",
                idempotency_key=key,
                commit=False,
            )
            count += 1

        await db.commit()
        return {
            "queued_scans": count,
            "scope": scope,
            "series_id": series_id,
            "season_number": season_number,
        }


@register("audio_subs_deep_scan")
async def audio_subs_deep_scan(job: Job) -> dict[str, Any]:
    """Queue stale or missing subtitle inventory scans for movies + TV."""
    from marquee.core.media_jobs import media_job_manager  # noqa: PLC0415

    cancel_event = cancel_registry.get(job.id)
    batch_limit = subtitle_settings.AUDIO_SUBS_DEEP_SCAN_BATCH
    factory = _get_session_factory()
    async with factory() as db:
        media_files = await _stale_or_missing_subtitle_scan_candidates(
            db,
            scope="all",
            force=False,
            limit=batch_limit,
        )
        total = len(media_files)
        for index, media_file in enumerate(media_files, 1):
            cancel_registry.raise_if_cancelled(cancel_event, "audio/subs deep scan cancelled")
            if index == 1 or index % 25 == 0 or index == total:
                await _update_progress(
                    job.id,
                    "queue",
                    index,
                    total,
                    message=f"Queueing deep scan {index}/{total}",
                )
            await media_job_manager.create_job(
                db,
                operation="subtitle_scan",
                media_file_id=media_file.id,
                trigger="scheduled",
                status="queued",
                idempotency_key=f"audio-subs-deep-scan:{job.id}:{media_file.id}",
                commit=False,
            )
        await db.commit()
        return {"queued_scans": total, "batch_limit": batch_limit}


@register("radarr_upgrade")
async def radarr_upgrade(job: Job) -> dict[str, Any]:
    """Durably perform all upgrade follow-up after the webhook has ACKed."""
    from marquee.api.routes.webhooks import (  # noqa: PLC0415
        _letterbox_stale_after_upgrade,
        _restore_after_upgrade,
        _schedule_subtitle_scan,
    )

    radarr_id = job.request.get("radarr_id")
    tmdb_id = job.request.get("tmdb_id")
    folder = job.request.get("folder")
    await _restore_after_upgrade(radarr_id, tmdb_id, folder)
    await _letterbox_stale_after_upgrade(radarr_id, tmdb_id)
    await _schedule_subtitle_scan(radarr_id, tmdb_id)
    return {"radarr_id": radarr_id, "tmdb_id": tmdb_id, "reconciled": True}


@register("poster_pipeline")
async def poster_pipeline(job: Job) -> dict[str, Any]:
    """Run one pipeline inside the worker instead of a detached API task."""
    from marquee.core.poster_sources.tmdb import TMDBClient  # noqa: PLC0415
    from marquee.core.text_profiles import OcrGateContext  # noqa: PLC0415
    from marquee.pipeline.run_manager import RunState, run_manager  # noqa: PLC0415

    movie_id = int(job.request["movie_id"])
    factory = _get_session_factory()
    async with factory() as db:
        movie = (await db.execute(select(Movie).where(Movie.id == movie_id))).scalar_one()
        run_id = job.id
        existing = await db.get(PipelineRun, run_id)
        if existing is None:
            db.add(
                PipelineRun(
                    run_id=run_id,
                    movie_id=movie.id,
                    status="running",
                    output_dir=str(settings.runs_work_path / movie.title),
                )
            )
            await db.commit()
        movie_id, title, tmdb_id = movie.id, movie.title, movie.tmdb_id
        ocr_gate = OcrGateContext.from_movie(movie)
    if tmdb_id is None:
        raise RuntimeError("movie has no TMDB ID")
    from marquee.pipeline.progress_bridge import JobProgressBridge  # noqa: PLC0415

    cancel_event = cancel_registry.get(job.id)
    tmdb = TMDBClient(read_access_token=settings.TMDB_READ_ACCESS_TOKEN)
    await tmdb.connect()
    run_manager._active_run_id = run_id
    run_manager._runs[run_id] = RunState(run_id=run_id)
    try:
        async with JobProgressBridge(job.id) as bridge:
            await run_manager._execute(
                run_id=run_id,
                tmdb=tmdb,
                movie_id=movie_id,
                movie_title=title,
                movie_tmdb_id=tmdb_id,
                progress_sink=bridge.callback,
                should_cancel=cancel_event.is_set if cancel_event is not None else None,
                ocr_gate=ocr_gate,
            )
    finally:
        await tmdb.disconnect()
    return {"run_id": run_id}


@register("poster_pipeline_batch")
async def poster_pipeline_batch(job: Job) -> dict[str, Any]:
    """Stage-batched pipeline over many movies — OCR + DINO load once per batch.

    One durable job streams every movie through each stage together (see
    ``marquee.pipeline.batch_runner``); each movie still gets its own ``run_id``
    + ``PipelineRun`` row, tagged ``batch_id=<this job>``.
    """
    from marquee.core.poster_sources.tmdb import TMDBClient  # noqa: PLC0415
    from marquee.core.text_profiles import OcrGateContext  # noqa: PLC0415
    from marquee.pipeline.batch_runner import run_batch  # noqa: PLC0415
    from marquee.pipeline.progress_bridge import JobProgressBridge  # noqa: PLC0415
    from marquee.pipeline.run_manager import run_manager  # noqa: PLC0415

    movie_ids = [int(m) for m in job.request.get("movie_ids", [])]
    factory = _get_session_factory()
    async with factory() as db:
        rows = (
            (await db.execute(select(Movie).where(Movie.id.in_(movie_ids)))).scalars().all()
            if movie_ids
            else []
        )
        by_id = {m.id: m for m in rows}
        # Preserve the requested order; silently drop unknown ids.
        movies = [
            (movie.id, movie.title, movie.tmdb_id)
            for mid in movie_ids
            if (movie := by_id.get(mid)) is not None
        ]
        ocr_meta = {movie.id: OcrGateContext.from_movie(movie) for movie in rows}
    if not movies:
        return {"status": "empty", "movies": 0}

    cancel_event = cancel_registry.get(job.id)
    tmdb = TMDBClient(read_access_token=settings.TMDB_READ_ACCESS_TOKEN)
    await tmdb.connect()
    # Load the model stack once, off the event loop, for the whole batch.
    extractor = await asyncio.to_thread(run_manager._ensure_extractor)
    try:
        async with JobProgressBridge(job.id) as bridge:
            summary = await run_batch(
                job_id=job.id,
                movies=movies,
                tmdb=tmdb,
                extractor=extractor,
                progress=bridge.callback,
                should_cancel=cancel_event.is_set if cancel_event is not None else None,
                ocr_meta=ocr_meta,
            )
    finally:
        await tmdb.disconnect()
        if not settings.PIPELINE_CACHE_EXTRACTOR:
            with contextlib.suppress(Exception):
                run_manager.release_gpu_resources()
    return summary


@register("poster_pipeline_tv_batch")
async def poster_pipeline_tv_batch(job: Job) -> dict[str, Any]:
    """Stage-batched pipeline over TV show/season assets — mirrors
    ``poster_pipeline_batch`` but sources candidates from Sonarr/TMDB series +
    season endpoints and scores against the TV taste namespace.

    Payload: ``{"assets": [{"media_type": "series"|"season", "series_id": int,
    "season_id": int | None}], "scope": str}``.
    """
    from marquee.core.poster_sources.tmdb import TMDBClient  # noqa: PLC0415
    from marquee.core.text_profiles import OcrGateContext  # noqa: PLC0415
    from marquee.pipeline.batch_runner import AssetSpec, run_batch_assets  # noqa: PLC0415
    from marquee.pipeline.progress_bridge import JobProgressBridge  # noqa: PLC0415
    from marquee.pipeline.run_manager import run_manager  # noqa: PLC0415

    requested = job.request.get("assets", [])
    series_ids = {int(a["series_id"]) for a in requested if a.get("series_id") is not None}
    season_ids = {
        int(a["season_id"]) for a in requested if a.get("media_type") == "season" and a.get("season_id") is not None
    }

    factory = _get_session_factory()
    assets: list[AssetSpec] = []
    ocr_meta: dict[tuple[str, int], OcrGateContext] = {}
    skipped_no_tmdb = 0
    async with factory() as db:
        series_rows = (
            (await db.execute(select(Series).where(Series.id.in_(series_ids)))).scalars().all()
            if series_ids
            else []
        )
        series_by_id = {s.id: s for s in series_rows}
        season_rows = (
            (await db.execute(select(Season).where(Season.id.in_(season_ids)))).scalars().all()
            if season_ids
            else []
        )
        season_by_id = {s.id: s for s in season_rows}

        for item in requested:
            media_type = item.get("media_type")
            series = series_by_id.get(int(item["series_id"])) if item.get("series_id") is not None else None
            if series is None:
                continue
            if series.tmdb_id is None:
                skipped_no_tmdb += 1
                continue
            if media_type == "series":
                subject = PosterSubject.from_series(series)
                assets.append(
                    AssetSpec(
                        media_type="series",
                        subject_id=series.id,
                        title=subject.title,
                        tmdb_id=series.tmdb_id,
                        series_id=series.id,
                        ocr_title=series.title,
                    )
                )
                ocr_meta[("series", series.id)] = OcrGateContext.from_series(series)
            elif media_type == "season":
                season_id = item.get("season_id")
                season = season_by_id.get(int(season_id)) if season_id is not None else None
                if season is None:
                    continue
                subject = PosterSubject.from_season(season, series)
                assets.append(
                    AssetSpec(
                        media_type="season",
                        subject_id=season.id,
                        title=subject.title,
                        tmdb_id=series.tmdb_id,
                        series_id=series.id,
                        season_id=season.id,
                        season_number=season.season_number,
                        ocr_title=series.title,
                    )
                )
                ocr_meta[("season", season.id)] = OcrGateContext.from_season(season, series)
            # Unknown media_type entries are silently dropped.

    if not assets:
        return {"status": "empty", "assets": 0, "skipped_no_tmdb": skipped_no_tmdb}

    cancel_event = cancel_registry.get(job.id)
    tmdb = TMDBClient(read_access_token=settings.TMDB_READ_ACCESS_TOKEN)
    await tmdb.connect()
    # Load the model stack once, off the event loop, for the whole batch.
    extractor = await asyncio.to_thread(run_manager._ensure_extractor)
    tv_namespace = get_namespace("tv")
    try:
        async with JobProgressBridge(job.id) as bridge:
            summary = await run_batch_assets(
                job_id=job.id,
                assets=assets,
                tmdb=tmdb,
                extractor=extractor,
                taste_namespace=tv_namespace,
                progress=bridge.callback,
                should_cancel=cancel_event.is_set if cancel_event is not None else None,
                ocr_meta=ocr_meta,
            )
    finally:
        await tmdb.disconnect()
        if not settings.PIPELINE_CACHE_EXTRACTOR:
            with contextlib.suppress(Exception):
                run_manager.release_gpu_resources()
    summary["skipped_no_tmdb"] = skipped_no_tmdb
    return summary


@register("learned_head_train")
async def learned_head_train(_job: Job) -> dict[str, Any]:
    """Train the learned head (the UI's 'Key Art Engine') from accumulated labels.

    Pure-numpy logistic head — cheap, no GPU. Picks accumulate labels +
    exemplars into storage; this is the manual trigger that consumes them.
    """
    from marquee.database import _get_session_factory  # noqa: PLC0415
    from marquee.ml import artifact_registry  # noqa: PLC0415
    from marquee.ml.head_trainer import train_from_labels  # noqa: PLC0415

    library_name = _job.request.get("library", "movies")
    ns = get_namespace(library_name)

    cancel_event = cancel_registry.get(_job.id)
    head, info = await asyncio.to_thread(train_from_labels, cancel_event=cancel_event, namespace=ns)
    if head is not None:
        async with _get_session_factory()() as db:
            if (await artifact_registry.registry_status(db))["available"]:
                await artifact_registry.register_active_artifact(
                    db,
                    ns.artifact_kind_head,
                    info=info,
                )
    return {"trained": head is not None, **info}


@register("pipeline_cache_clear")
async def pipeline_cache_clear(job: Job) -> dict[str, Any]:
    """Clear the downloaded-poster pipeline cache (never learned-head/taste data)."""
    from marquee.core.pipeline_cache import clear_pipeline_cache  # noqa: PLC0415

    include_embeddings = bool(job.request.get("include_embeddings", True))
    include_archives = bool(job.request.get("include_archives", False))
    return await asyncio.to_thread(
        clear_pipeline_cache,
        include_embeddings=include_embeddings,
        include_archives=include_archives,
    )


@register("poster_deploy_reset")
async def poster_deploy_reset(job: Job) -> dict[str, Any]:
    """Delete every deployed poster and reset subjects to missing.

    Walks every subject with a non-NULL ``poster_path``, validates the
    folder, deletes the poster file, and resets deploy-selection ``poster_*`` columns.
    """
    import json as _json

    from marquee.core.path_utils import safe_translate_and_validate

    factory = _get_session_factory()
    async with factory() as db:
        subjects: list[PosterSubject] = []

        movies = (await db.execute(select(Movie).where(Movie.poster_path.is_not(None)))).scalars().all()
        for movie in movies:
            subjects.append(PosterSubject.from_movie(movie))

        series_list = (await db.execute(select(Series).where(Series.poster_path.is_not(None)))).scalars().all()
        for series in series_list:
            subjects.append(PosterSubject.from_series(series))

        seasons_info = (
            await db.execute(
                select(Season, Series)
                .join(Series, Series.id == Season.series_id)
                .where(Season.poster_path.is_not(None))
            )
        ).all()
        for season, series in seasons_info:
            subjects.append(PosterSubject.from_season(season, series))

        if not subjects:
            return {
                "reset": 0,
                "failed": 0,
                "errors": [],
                "by_type": {
                    "movie": {"reset": 0, "failed": 0},
                    "series": {"reset": 0, "failed": 0},
                    "season": {"reset": 0, "failed": 0},
                },
            }

        reset = 0
        failed = 0
        errors: list[dict] = []
        by_type = {
            "movie": {"reset": 0, "failed": 0},
            "series": {"reset": 0, "failed": 0},
            "season": {"reset": 0, "failed": 0},
        }

        for subject in subjects:
            deleted = False
            entity = subject.entity
            stored_path = str(entity.poster_path)  # capture before clearing
            try:
                poster_file = Path(entity.poster_path)
                folder = safe_translate_and_validate(subject.folder_raw, source=subject.path_source)
                if poster_file.parent.resolve() != folder.resolve():
                    raise RuntimeError(f"Poster parent {poster_file.parent} != folder {folder}")
                poster_file.unlink(missing_ok=True)
                deleted = True
            except Exception as exc:
                logger.warning(
                    "DEPLOY RESET | file error for %s subject %d (%s): %s",
                    subject.media_type,
                    entity.id,
                    subject.title,
                    exc,
                )
                with contextlib.suppress(Exception):
                    Path(entity.poster_path).unlink(missing_ok=True)

            # Always reset DB columns — the file is gone or unreachable.
            entity.poster_path = None
            entity.poster_source = None
            entity.poster_source_url = None
            entity.poster_ai_selected = False
            if hasattr(entity, "poster_embedding"):
                entity.poster_embedding = None
            entity.poster_sha256 = None
            entity.poster_phash = None
            entity.poster_user_approved = False
            entity.poster_deployed_filename = None
            entity.poster_deployed_at = None

            cpaths = subject.cache_paths()
            cache_file_str = str(cpaths[0]) if (subject.tmdb_id and cpaths) else None
            detail = _json.dumps(
                {
                    "deleted_path": stored_path,
                    "cache_kept": cache_file_str,
                }
            )
            db.add(
                ArtworkEvent(
                    **subject.event_fk_kwargs(),
                    action="deploy_reset",
                    source="maintenance",
                    detail=detail,
                )
            )
            if deleted:
                reset += 1
                by_type[subject.media_type]["reset"] += 1
            else:
                failed += 1
                by_type[subject.media_type]["failed"] += 1
                errors.append(
                    {
                        "media_type": subject.media_type,
                        "subject_id": entity.id,
                        "title": subject.title,
                        "error": "file unavailable — DB state cleared",
                    }
                )

        await db.commit()

    logger.info("DEPLOY RESET | reset=%d | failed=%d", reset, failed)
    return {"reset": reset, "failed": failed, "errors": errors, "by_type": by_type}


@register("poster_rescan")
async def poster_rescan(job: Job) -> dict[str, Any]:
    """Re-stat expected poster files after filename/path settings change."""
    from marquee.core.path_utils import safe_translate_and_validate
    from marquee.core.tv_queries import season_downloaded, series_visible

    factory = _get_session_factory()
    async with factory() as db:
        subjects: list[PosterSubject] = []

        movies = (await db.execute(select(Movie).where(_downloaded_condition()).order_by(Movie.id))).scalars().all()
        for movie in movies:
            subjects.append(PosterSubject.from_movie(movie))

        series_list = (await db.execute(select(Series).where(series_visible()).order_by(Series.id))).scalars().all()
        for series in series_list:
            subjects.append(PosterSubject.from_series(series))

        seasons_info = (
            await db.execute(
                select(Season, Series)
                .join(Series, Series.id == Season.series_id)
                .where(season_downloaded())
                .order_by(Season.id)
            )
        ).all()
        for season, series in seasons_info:
            subjects.append(PosterSubject.from_season(season, series))

        total = len(subjects)
        updated = missing = unchanged = failed = 0
        errors: list[dict] = []
        by_type = {
            "movie": {"updated": 0, "missing": 0, "unchanged": 0, "failed": 0},
            "series": {"updated": 0, "missing": 0, "unchanged": 0, "failed": 0},
            "season": {"updated": 0, "missing": 0, "unchanged": 0, "failed": 0},
        }

        for index, subject in enumerate(subjects, 1):
            if index == 1 or index % 25 == 0 or index == total:
                await _update_progress(job.id, "rescan", index, total)
                current = await db.get(Job, job.id)
                if current is not None and current.cancel_requested:
                    return {
                        "updated": updated,
                        "missing": missing,
                        "unchanged": unchanged,
                        "failed": failed,
                        "cancelled": True,
                        "errors": errors,
                        "by_type": by_type,
                    }
            entity = subject.entity
            try:
                folder = safe_translate_and_validate(subject.folder_raw, source=subject.path_source)
                expected = folder / subject.render_filename()
                exists_on_disk = await asyncio.to_thread(expected.is_file)
                expected_str = str(expected)
                if exists_on_disk:
                    if entity.poster_path != expected_str:
                        entity.poster_path = expected_str
                        updated += 1
                        by_type[subject.media_type]["updated"] += 1
                    else:
                        unchanged += 1
                        by_type[subject.media_type]["unchanged"] += 1
                elif entity.poster_path is not None:
                    entity.poster_path = None
                    missing += 1
                    by_type[subject.media_type]["missing"] += 1
                else:
                    unchanged += 1
                    by_type[subject.media_type]["unchanged"] += 1
            except Exception as exc:  # noqa: BLE001
                failed += 1
                by_type[subject.media_type]["failed"] += 1
                errors.append(
                    {
                        "media_type": subject.media_type,
                        "subject_id": entity.id,
                        "title": subject.title,
                        "error": str(exc),
                    }
                )

        await db.commit()
        return {
            "updated": updated,
            "missing": missing,
            "unchanged": unchanged,
            "failed": failed,
            "errors": errors,
            "by_type": by_type,
        }


@register("poster_backup_all")
async def poster_backup_all(job: Job) -> dict[str, Any]:
    """Copy all deployed poster bytes into the local backup directory."""
    from marquee.core.poster_service import _atomic_copy, _sha256

    factory = _get_session_factory()
    async with factory() as db:
        subjects: list[PosterSubject] = []

        movies = (await db.execute(select(Movie).where(Movie.poster_path.is_not(None)))).scalars().all()
        for movie in movies:
            subjects.append(PosterSubject.from_movie(movie))

        series_list = (await db.execute(select(Series).where(Series.poster_path.is_not(None)))).scalars().all()
        for series in series_list:
            subjects.append(PosterSubject.from_series(series))

        seasons_info = (
            await db.execute(
                select(Season, Series)
                .join(Series, Series.id == Season.series_id)
                .where(Season.poster_path.is_not(None))
            )
        ).all()
        for season, series in seasons_info:
            subjects.append(PosterSubject.from_season(season, series))

        total = len(subjects)
        copied = skipped = failed = 0
        errors: list[dict] = []
        by_type = {
            "movie": {"copied": 0, "skipped": 0, "failed": 0},
            "series": {"copied": 0, "skipped": 0, "failed": 0},
            "season": {"copied": 0, "skipped": 0, "failed": 0},
        }

        for index, subject in enumerate(subjects, 1):
            if index == 1 or index % 25 == 0 or index == total:
                await _update_progress(job.id, "backup", index, total)
                current = await db.get(Job, job.id)
                if current is not None and current.cancel_requested:
                    return {
                        "checked": index,
                        "copied": copied,
                        "skipped": skipped,
                        "failed": failed,
                        "cancelled": True,
                        "errors": errors,
                        "by_type": by_type,
                    }

            dest = subject.backup_file()
            entity = subject.entity
            try:
                if await asyncio.to_thread(dest.is_file):
                    if entity.poster_sha256:
                        if await asyncio.to_thread(_sha256, dest) == entity.poster_sha256:
                            skipped += 1
                            by_type[subject.media_type]["skipped"] += 1
                            entity.poster_local_backup_path = str(dest)
                            continue
                    elif entity.poster_deployed_at:
                        mtime = datetime.fromtimestamp(dest.stat().st_mtime, UTC)
                        if mtime >= entity.poster_deployed_at:
                            skipped += 1
                            by_type[subject.media_type]["skipped"] += 1
                            entity.poster_local_backup_path = str(dest)
                            continue
                    else:
                        skipped += 1
                        by_type[subject.media_type]["skipped"] += 1
                        entity.poster_local_backup_path = str(dest)
                        continue

                source: Path | None = None
                if subject.tmdb_id is not None:
                    cpaths = subject.cache_paths()
                    if cpaths:
                        cache_file, _ = cpaths
                        if await asyncio.to_thread(cache_file.is_file):
                            source = cache_file
                if source is None and entity.poster_path:
                    poster_file = Path(entity.poster_path)
                    if await asyncio.to_thread(poster_file.is_file):
                        source = poster_file
                if source is None:
                    raise FileNotFoundError("no deployed poster file or cache copy found")

                await asyncio.to_thread(_atomic_copy, source, dest)
                entity.poster_local_backup_path = str(dest)
                copied += 1
                by_type[subject.media_type]["copied"] += 1
            except Exception as exc:  # noqa: BLE001
                failed += 1
                by_type[subject.media_type]["failed"] += 1
                errors.append(
                    {
                        "media_type": subject.media_type,
                        "subject_id": entity.id,
                        "title": subject.title,
                        "error": str(exc),
                    }
                )

        await db.commit()
        return {
            "checked": total,
            "copied": copied,
            "skipped": skipped,
            "failed": failed,
            "errors": errors,
            "by_type": by_type,
        }


def _confined_existing_path(path: str | None, root: Path) -> Path | None:
    if not path:
        return None
    resolved = Path(path).resolve()
    root_resolved = root.resolve()
    if not str(resolved).startswith(str(root_resolved)):
        raise RuntimeError(f"path {resolved} is outside {root_resolved}")
    return resolved


def _delete_path(path: Path) -> int:
    if path.is_dir():
        shutil.rmtree(path)
        return 1
    if path.exists():
        path.unlink()
        return 1
    return 0


@register("poster_maintenance")
async def poster_maintenance(job: Job) -> dict[str, Any]:
    """Reconcile Radarr/Sonarr-deleted media and prune orphaned poster caches/backups.

    Job result/events are the audit trail. ArtworkEvents cascade when Movie/Series/Season
    rows are deleted; taste/feedback training data is intentionally left alone.
    """
    from marquee.core.arr_clients.radarr_client import RadarrClient
    from marquee.core.arr_clients.sonarr_client import SonarrClient

    dry_run = bool(job.request.get("dry_run", False))
    force = bool(job.request.get("force", False))

    radarr_movies = None
    if settings.radarr_configured:
        radarr = RadarrClient(settings.RADARR_URL, settings.RADARR_API_KEY)
        try:
            await radarr.connect()
            try:
                radarr_movies = await radarr.get_movies()
            except Exception as exc:  # noqa: BLE001
                logger.warning("radarr fetch failed during maintenance: %s", exc)
        finally:
            await radarr.disconnect()

    sonarr_series = None
    if settings.sonarr_configured:
        sonarr = SonarrClient(settings.SONARR_URL, settings.SONARR_API_KEY)
        try:
            await sonarr.connect()
            try:
                sonarr_series = await sonarr.get_series()
            except Exception as exc:  # noqa: BLE001
                logger.warning("sonarr fetch failed during maintenance: %s", exc)
        finally:
            await sonarr.disconnect()

    if not settings.radarr_configured and not settings.sonarr_configured:
        return {"skipped": "neither radarr nor sonarr configured", "dry_run": dry_run}

    factory = _get_session_factory()
    async with factory() as db:
        deleted_manifest: list[dict] = []
        file_paths: list[Path] = []
        candidates_movies: list[Movie] = []
        candidates_series: list[Series] = []

        current_movie_ids = set((await db.execute(select(Movie.id))).scalars().all())
        current_tmdb_ids = {
            tmdb_id
            for tmdb_id in (await db.execute(select(Movie.tmdb_id))).scalars().all()
            if tmdb_id is not None
        }

        # 1. Reconcile movies (Radarr)
        if radarr_movies is not None:
            radarr_ids = {int(row["id"]) for row in radarr_movies if row.get("id") is not None}
            candidates_movies = (
                (
                    await db.execute(
                        select(Movie)
                        .where(Movie.radarr_id.is_not(None), Movie.radarr_id.not_in(radarr_ids))
                        .order_by(Movie.id)
                    )
                )
                .scalars()
                .all()
            )
            threshold_movies = max(5, int(0.1 * max(len(radarr_ids), 1)))
            if len(candidates_movies) > threshold_movies and not force:
                raise RuntimeError(
                    f"refusing to delete {len(candidates_movies)} movies; exceeds threshold {threshold_movies}"
                )

            for movie in candidates_movies:
                deleted_manifest.append(
                    {
                        "media_type": "movie",
                        "movie_id": movie.id,
                        "title": movie.title,
                        "tmdb_id": movie.tmdb_id,
                        "radarr_id": movie.radarr_id,
                    }
                )
                runs = (
                    await db.execute(
                        select(PipelineRun.archive_path, PipelineRun.output_dir).where(
                            PipelineRun.movie_id == movie.id
                        )
                    )
                ).all()
                for archive_path, output_dir in runs:
                    path = _confined_existing_path(archive_path, settings.runs_archive_path)
                    if path is not None:
                        file_paths.append(path)
                    path = _confined_existing_path(output_dir, settings.runs_work_path)
                    if path is not None:
                        file_paths.append(path)
                subject = PosterSubject.from_movie(movie)
                cpaths = subject.cache_paths()
                if cpaths:
                    file_paths.extend(cpaths)
                file_paths.append(subject.backup_file())

        # 2. Reconcile series (Sonarr)
        current_series_ids = set((await db.execute(select(Series.id))).scalars().all())
        current_series_tmdb_ids = {
            tmdb_id
            for tmdb_id in (await db.execute(select(Series.tmdb_id))).scalars().all()
            if tmdb_id is not None
        }
        current_season_ids = set((await db.execute(select(Season.id))).scalars().all())

        if sonarr_series is not None:
            sonarr_ids = {int(row["id"]) for row in sonarr_series if row.get("id") is not None}
            candidates_series = (
                (
                    await db.execute(
                        select(Series)
                        .where(Series.sonarr_id.is_not(None), Series.sonarr_id.not_in(sonarr_ids))
                        .order_by(Series.id)
                    )
                )
                .scalars()
                .all()
            )
            threshold_series = max(5, int(0.1 * max(len(sonarr_ids), 1)))
            if len(candidates_series) > threshold_series and not force:
                raise RuntimeError(
                    f"refusing to delete {len(candidates_series)} series; exceeds threshold {threshold_series}"
                )

            for series in candidates_series:
                deleted_manifest.append(
                    {
                        "media_type": "series",
                        "series_id": series.id,
                        "title": series.title,
                        "tmdb_id": series.tmdb_id,
                        "sonarr_id": series.sonarr_id,
                    }
                )
                # Find all runs for series or its seasons
                runs = (
                    await db.execute(
                        select(PipelineRun.archive_path, PipelineRun.output_dir).where(
                            or_(
                                PipelineRun.series_id == series.id,
                                PipelineRun.season_id.in_(
                                    select(Season.id).where(Season.series_id == series.id)
                                ),
                            )
                        )
                    )
                ).all()
                for archive_path, output_dir in runs:
                    path = _confined_existing_path(archive_path, settings.runs_archive_path)
                    if path is not None:
                        file_paths.append(path)
                    path = _confined_existing_path(output_dir, settings.runs_work_path)
                    if path is not None:
                        file_paths.append(path)

                # Series cache + backup
                subject_series = PosterSubject.from_series(series)
                cpaths = subject_series.cache_paths()
                if cpaths:
                    file_paths.extend(cpaths)
                file_paths.append(subject_series.backup_file())

                # All seasons cache + backups
                seasons = (
                    (await db.execute(select(Season).where(Season.series_id == series.id)))
                    .scalars()
                    .all()
                )
                for season in seasons:
                    subject_season = PosterSubject.from_season(season, series)
                    cpaths = subject_season.cache_paths()
                    if cpaths:
                        file_paths.extend(cpaths)
                    file_paths.append(subject_season.backup_file())

        # 3. Prune orphaned backups & caches
        backups_pruned = cache_pruned = files_deleted = 0
        backup_dir = settings.poster_backup_path
        movie_cache_dir = settings.poster_cache_path / "movies"
        tv_cache_dir = settings.poster_cache_path / "tv"

        orphan_backups = []
        if backup_dir.is_dir():
            # Movie backups (*.jpg, numeric stem)
            orphan_backups.extend(
                [
                    entry
                    for entry in backup_dir.glob("*.jpg")
                    if entry.stem.isdigit() and int(entry.stem) not in current_movie_ids
                ]
            )
            # Series backups (series-*.jpg)
            orphan_backups.extend(
                [
                    entry
                    for entry in backup_dir.glob("series-*.jpg")
                    if entry.stem.removeprefix("series-").isdigit()
                    and int(entry.stem.removeprefix("series-")) not in current_series_ids
                ]
            )
            # Season backups (season-*.jpg)
            orphan_backups.extend(
                [
                    entry
                    for entry in backup_dir.glob("season-*.jpg")
                    if entry.stem.removeprefix("season-").isdigit()
                    and int(entry.stem.removeprefix("season-")) not in current_season_ids
                ]
            )

        orphan_cache = []
        if movie_cache_dir.is_dir():
            orphan_cache.extend(
                [
                    entry
                    for entry in movie_cache_dir.glob("*")
                    if entry.suffix in {".jpg", ".json"}
                    and entry.stem.removesuffix(".meta").isdigit()
                    and int(entry.stem.removesuffix(".meta")) not in current_tmdb_ids
                ]
            )

        current_seasons_tv = {
            (season_number, tmdb_id)
            for season_number, tmdb_id in (
                await db.execute(
                    select(Season.season_number, Series.tmdb_id).join(
                        Series, Series.id == Season.series_id
                    )
                )
            ).all()
            if tmdb_id is not None
        }

        if tv_cache_dir.is_dir():
            for entry in tv_cache_dir.glob("*"):
                if entry.suffix in {".jpg", ".json"}:
                    stem = entry.stem.removesuffix(".meta")
                    if "-s" in stem:
                        parts = stem.split("-s")
                        if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
                            tmdb_id = int(parts[0])
                            season_num = int(parts[1])
                            if (season_num, tmdb_id) not in current_seasons_tv:
                                orphan_cache.append(entry)
                    else:
                        if stem.isdigit():
                            tmdb_id = int(stem)
                            if tmdb_id not in current_series_tmdb_ids:
                                orphan_cache.append(entry)

        if not dry_run:
            for path in file_paths:
                try:
                    files_deleted += await asyncio.to_thread(_delete_path, path)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("poster maintenance could not delete %s: %s", path, exc)
            for path in orphan_backups:
                backups_pruned += await asyncio.to_thread(_delete_path, path)
            for path in orphan_cache:
                cache_pruned += await asyncio.to_thread(_delete_path, path)

            for movie in candidates_movies:
                await db.delete(movie)
            for series in candidates_series:
                # cascade deletes seasons and episodes
                await db.delete(series)
            await db.commit()

        return {
            "movies_deleted": 0 if dry_run else len(candidates_movies),
            "series_deleted": 0 if dry_run else len(candidates_series),
            "files_deleted": files_deleted,
            "backups_pruned": 0 if dry_run else backups_pruned,
            "cache_pruned": 0 if dry_run else cache_pruned,
            "dry_run": dry_run,
            "deleted": deleted_manifest,
            "orphan_backups": len(orphan_backups),
            "orphan_cache": len(orphan_cache),
            "errors": [],
        }


async def _gather_library_posters(ns: TasteNamespace | None = None) -> tuple[Path, Any, int]:
    """Copy every movie's (or show/season's) currently-deployed poster into a temp training dir.

    Prefers the local deployed-poster cache (``data/cache/posters``) and falls
    back to the on-disk poster in the media folder. Returns
    ``(dir, TemporaryDirectory, count)``; the caller cleans up the temp dir.
    """
    import tempfile  # noqa: PLC0415

    ns = ns or get_namespace("movies")
    factory = _get_session_factory()
    tmp = tempfile.TemporaryDirectory(prefix="marquee-libtrain-")
    dest = Path(tmp.name)
    count = 0

    async with factory() as db:
        if ns.library == "movies":
            rows = (
                (await db.execute(select(Movie).where(Movie.poster_path.is_not(None)))).scalars().all()
            )
            movies = [(m.title, m.year, m.tmdb_id, m.poster_path) for m in rows]
            count = await asyncio.to_thread(_copy_library_posters, movies, dest)
        else:
            # tv
            from marquee.models import Season, Series  # noqa: PLC0415
            shows_rows = (
                (await db.execute(select(Series).where(Series.poster_path.is_not(None)))).scalars().all()
            )
            shows = [(s.title, s.year, s.tmdb_id, s.poster_path) for s in shows_rows]

            seasons_rows = (
                (await db.execute(select(Season).where(Season.poster_path.is_not(None)))).scalars().all()
            )
            seasons = []
            for sn in seasons_rows:
                parent = await db.get(Series, sn.series_id)
                parent_title = parent.title if parent else "show"
                seasons.append((parent_title, sn.season_number, None, sn.poster_path))

            show_dest = dest / "show"
            season_dest = dest / "season"
            show_dest.mkdir(parents=True, exist_ok=True)
            season_dest.mkdir(parents=True, exist_ok=True)

            count_shows = await asyncio.to_thread(_copy_library_posters, shows, show_dest)
            count_seasons = await asyncio.to_thread(_copy_library_season_posters, seasons, season_dest)
            count = count_shows + count_seasons

    if count == 0:
        tmp.cleanup()
        raise RuntimeError("no deployed library posters found to train on")
    return dest, tmp, count


def _copy_library_posters(movies: list[tuple], dest: Path) -> int:
    import shutil  # noqa: PLC0415

    from marquee.core.poster_service import cache_paths  # noqa: PLC0415
    from marquee.ml.profile_updater import exemplar_filename  # noqa: PLC0415

    seen: set[str] = set()
    count = 0
    for title, year, tmdb_id, poster_path in movies:
        source: Path | None = None
        if tmdb_id is not None:
            cache_file, _ = cache_paths(tmdb_id)
            if cache_file.is_file():
                source = cache_file
        if source is None and poster_path and Path(poster_path).is_file():
            source = Path(poster_path)
        if source is None:
            continue
        name = exemplar_filename(title or "poster", year, ".jpg")
        if name in seen:  # two movies, same title+year — keep the first
            continue
        seen.add(name)
        try:
            shutil.copy2(source, dest / name)
            count += 1
        except OSError as exc:
            logger.warning("library-train: could not copy %s: %s", source, exc)
    return count


def _copy_library_season_posters(seasons: list[tuple], dest: Path) -> int:
    import shutil  # noqa: PLC0415

    from marquee.ml.profile_updater import exemplar_filename  # noqa: PLC0415

    seen: set[str] = set()
    count = 0
    for parent_title, season_number, _, poster_path in seasons:
        source: Path | None = None
        if poster_path and Path(poster_path).is_file():
            source = Path(poster_path)
        if source is None:
            continue
        title = f"{parent_title} Season {season_number}"
        name = exemplar_filename(title, None, ".jpg")
        if name in seen:
            continue
        seen.add(name)
        try:
            shutil.copy2(source, dest / name)
            count += 1
        except OSError as exc:
            logger.warning("library-train-season: could not copy %s: %s", source, exc)
    return count


@register("job_retention_purge")
async def job_retention_purge(_job: Job) -> dict[str, Any]:
    raise RuntimeError("job_retention_purge is not migrated to the canonical runtime")


@register("system_metrics_purge")
async def system_metrics_purge(_job: Job) -> dict[str, Any]:
    """Delete SystemMetricsSample rows older than METRICS_RETENTION_DAYS.

    Mirrors job_retention_purge's pattern. The samples this deletes are
    written by the lightweight asyncio sampler (system_metrics_sampler.py),
    not by a Job — but the daily cleanup itself is infrequent enough that
    running it as a real Job is fine.
    """
    from datetime import UTC, datetime, timedelta

    from marquee.models import SystemMetricsSample

    cutoff = datetime.now(UTC) - timedelta(days=settings.METRICS_RETENTION_DAYS)
    factory = _get_session_factory()
    async with factory() as db:
        result = await db.execute(
            SystemMetricsSample.__table__.delete().where(SystemMetricsSample.created_at < cutoff)
        )
        deleted = result.rowcount or 0
        await db.commit()

    logger.info(
        "system_metrics_purge: deleted %d sample(s) older than %d day(s)",
        deleted,
        settings.METRICS_RETENTION_DAYS,
    )
    return {"samples_deleted": deleted}
