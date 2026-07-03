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
from marquee.database import _get_session_factory
from marquee.models import ArtworkEvent, Job, MediaFile, Movie, PipelineRun

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


@register("poster_heal", instant=True)
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
    movie_id = int(job.payload["movie_id"])

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
                detector=job.payload.get("detector", "v2"),
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


@register("letterbox_apply", instant=True)
async def letterbox_apply(job: Job) -> dict[str, Any]:
    from marquee.core.letterbox_service import letterbox_service  # noqa: PLC0415

    factory = _get_session_factory()
    async with factory() as db:
        movie = await db.get(Movie, int(job.payload["movie_id"]))
        if movie is None:
            raise RuntimeError("movie not found")
        result = await letterbox_service.apply(
            db, movie, top=int(job.payload["top"]), bottom=int(job.payload["bottom"]), source="job"
        )
        return {
            "applied": result.applied,
            "top": result.top,
            "bottom": result.bottom,
            "verified": result.verified,
        }


@register("letterbox_remove", instant=True)
async def letterbox_remove(job: Job) -> dict[str, Any]:
    from marquee.core.letterbox_service import letterbox_service  # noqa: PLC0415

    factory = _get_session_factory()
    async with factory() as db:
        movie = await db.get(Movie, int(job.payload["movie_id"]))
        if movie is None:
            raise RuntimeError("movie not found")
        result = await letterbox_service.remove(db, movie, source="job")
        return {"removed": result.removed, "path": result.path}


@register("backup_create", instant=True)
async def backup_create(_job: Job) -> dict[str, Any]:
    from marquee.core.backup import backup_service  # noqa: PLC0415

    return (await backup_service.create_backup()).to_dict()


@register("taste_rebuild")
async def taste_rebuild(job: Job) -> dict[str, Any]:
    """Rebuild the taste profile outside FastAPI; the worker owns the GPU lease.

    ``payload.source`` selects the exemplar set:
      * ``"training_dir"`` (default) — the curated ``data/training/positive`` folder.
      * ``"library"`` — every movie's currently-deployed poster.
    """
    from marquee.core.pipeline_config import pipeline_settings  # noqa: PLC0415
    from marquee.ml.head_trainer import train_from_labels  # noqa: PLC0415
    from marquee.ml.taste_trainer import rebuild_profile  # noqa: PLC0415
    from marquee.pipeline.progress_bridge import JobProgressBridge  # noqa: PLC0415
    from marquee.pipeline.run_manager import run_manager  # noqa: PLC0415

    cancel_event = cancel_registry.get(job.id)
    source = job.payload.get("source", "training_dir")
    training_dir: Path | None = None
    tmp = None
    gathered: int | None = None
    if source == "library":
        cancel_registry.raise_if_cancelled(cancel_event, "taste rebuild cancelled")
        await _emit_stage(job.id, "gather", "Gathering library posters as exemplars…")
        training_dir, tmp, gathered = await _gather_library_posters()
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
            )
        cancel_registry.raise_if_cancelled(cancel_event, "taste rebuild cancelled")
        if pipeline_settings.HEAD_AUTO_RETRAIN:
            await _emit_stage(job.id, "train_head", "Training learned ranking head…")
            head = await asyncio.to_thread(train_from_labels, cancel_event=cancel_event)
        else:
            head = None
    finally:
        if tmp is not None:
            tmp.cleanup()
    # The next pipeline run in this worker must reload the rebuilt profile.
    run_manager.reset_extractor()
    return {
        "rebuild": "completed",
        "source": source,
        "exemplars_gathered": gathered,
        "head": head[1] if head else None,
    }


@register("taste_map")
async def taste_map(job: Job) -> dict[str, Any]:
    from marquee.ml.taste_map import build_map  # noqa: PLC0415
    from marquee.pipeline.progress_bridge import JobProgressBridge  # noqa: PLC0415

    cancel_event = cancel_registry.get(job.id)
    async with JobProgressBridge(job.id) as bridge:
        return await asyncio.to_thread(
            build_map, progress_callback=bridge.callback, cancel_event=cancel_event
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
    from sqlalchemy import select  # noqa: PLC0415

    from marquee.core.media_jobs import media_job_manager  # noqa: PLC0415
    from marquee.models import MediaFile, SubtitleInventory  # noqa: PLC0415

    cancel_event = cancel_registry.get(job.id)
    force = job.payload.get("force", False)
    factory = _get_session_factory()
    async with factory() as db:
        if force:
            stmt = select(MediaFile).where(MediaFile.is_active.is_(True))
        else:
            subquery = select(SubtitleInventory.media_file_id)
            stmt = select(MediaFile).where(
                MediaFile.is_active.is_(True), MediaFile.id.not_in(subquery)
            )
        media_files = (await db.execute(stmt)).scalars().all()

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
        return {"queued_scans": count}


@register("radarr_upgrade")
async def radarr_upgrade(job: Job) -> dict[str, Any]:
    """Durably perform all upgrade follow-up after the webhook has ACKed."""
    from marquee.api.routes.webhooks import (  # noqa: PLC0415
        _letterbox_stale_after_upgrade,
        _restore_after_upgrade,
        _schedule_subtitle_scan,
    )

    radarr_id = job.payload.get("radarr_id")
    tmdb_id = job.payload.get("tmdb_id")
    folder = job.payload.get("folder")
    await _restore_after_upgrade(radarr_id, tmdb_id, folder)
    await _letterbox_stale_after_upgrade(radarr_id, tmdb_id)
    await _schedule_subtitle_scan(radarr_id, tmdb_id)
    return {"radarr_id": radarr_id, "tmdb_id": tmdb_id, "reconciled": True}


@register("poster_pipeline")
async def poster_pipeline(job: Job) -> dict[str, Any]:
    """Run one pipeline inside the worker instead of a detached API task."""
    from marquee.core.poster_sources.tmdb import TMDBClient  # noqa: PLC0415
    from marquee.pipeline.run_manager import RunState, run_manager  # noqa: PLC0415

    movie_id = int(job.payload["movie_id"])
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
    from marquee.pipeline.batch_runner import run_batch  # noqa: PLC0415
    from marquee.pipeline.progress_bridge import JobProgressBridge  # noqa: PLC0415
    from marquee.pipeline.run_manager import run_manager  # noqa: PLC0415

    movie_ids = [int(m) for m in job.payload.get("movie_ids", [])]
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
            )
    finally:
        await tmdb.disconnect()
        if not settings.PIPELINE_CACHE_EXTRACTOR:
            with contextlib.suppress(Exception):
                run_manager.release_gpu_resources()
    return summary


@register("learned_head_train")
async def learned_head_train(_job: Job) -> dict[str, Any]:
    """Train the learned head (the UI's 'Key Art Engine') from accumulated labels.

    Pure-numpy logistic head — cheap, no GPU. Picks accumulate labels +
    exemplars into storage; this is the manual trigger that consumes them.
    """
    from marquee.ml.head_trainer import train_from_labels  # noqa: PLC0415

    cancel_event = cancel_registry.get(_job.id)
    head, info = await asyncio.to_thread(train_from_labels, cancel_event=cancel_event)
    return {"trained": head is not None, **info}


@register("pipeline_cache_clear", instant=True)
async def pipeline_cache_clear(job: Job) -> dict[str, Any]:
    """Clear the downloaded-poster pipeline cache (never learned-head/taste data)."""
    from marquee.core.pipeline_cache import clear_pipeline_cache  # noqa: PLC0415

    include_embeddings = bool(job.payload.get("include_embeddings", True))
    include_archives = bool(job.payload.get("include_archives", False))
    return await asyncio.to_thread(
        clear_pipeline_cache,
        include_embeddings=include_embeddings,
        include_archives=include_archives,
    )


@register("poster_deploy_reset", instant=True)
async def poster_deploy_reset(job: Job) -> dict[str, Any]:
    """Delete every deployed poster and reset movies to missing.

    Walks every ``Movie`` with a non-NULL ``poster_path``, validates the
    folder via ``safe_translate_and_validate``, deletes the poster file
    (confinement check: the file must reside in the validated folder), and
    resets deploy-selection ``poster_*`` columns to the missing state. Cache
    copies under ``data/cache/posters/`` and local backup paths/files are KEPT
    as fallback restore sources; poster maintenance owns backup garbage
    collection.

    Idempotent — a second run finds zero deploy rows and returns reset=0.
    """
    import json as _json

    from marquee.core.path_utils import safe_translate_and_validate
    from marquee.core.poster_service import cache_paths

    factory = _get_session_factory()
    async with factory() as db:
        rows = (
            (await db.execute(select(Movie).where(Movie.poster_path.is_not(None)))).scalars().all()
        )
        if not rows:
            return {"reset": 0, "failed": 0, "errors": []}

        reset = 0
        failed = 0
        errors: list[dict] = []
        for movie in rows:
            deleted = False
            stored_path = str(movie.poster_path)  # capture before clearing
            try:
                poster_file = Path(movie.poster_path)
                folder = safe_translate_and_validate(movie.folder_path, source="radarr")
                if poster_file.parent.resolve() != folder.resolve():
                    raise RuntimeError(f"Poster parent {poster_file.parent} != folder {folder}")
                poster_file.unlink(missing_ok=True)
                deleted = True
            except Exception as exc:
                logger.warning(
                    "DEPLOY RESET | file error for movie %d (%s): %s",
                    movie.id,
                    movie.title,
                    exc,
                )
                with contextlib.suppress(Exception):
                    Path(movie.poster_path).unlink(missing_ok=True)

            # Always reset DB columns — the file is gone or unreachable.
            movie.poster_path = None
            movie.poster_source = None
            movie.poster_source_url = None
            movie.poster_ai_selected = False
            movie.poster_embedding = None
            movie.poster_sha256 = None
            movie.poster_phash = None
            movie.poster_user_approved = False
            movie.poster_deployed_filename = None
            movie.poster_deployed_at = None

            detail = _json.dumps(
                {
                    "deleted_path": stored_path,
                    "cache_kept": str(cache_paths(movie.tmdb_id)[0]) if movie.tmdb_id else None,
                }
            )
            db.add(
                ArtworkEvent(
                    movie_id=movie.id,
                    action="deploy_reset",
                    source="maintenance",
                    detail=detail,
                )
            )
            if deleted:
                reset += 1
            else:
                failed += 1
                errors.append(
                    {
                        "movie_id": movie.id,
                        "title": movie.title,
                        "error": "file unavailable — DB state cleared",
                    }
                )

        await db.commit()

    logger.info("DEPLOY RESET | reset=%d | failed=%d", reset, failed)
    return {"reset": reset, "failed": failed, "errors": errors}


@register("poster_rescan")
async def poster_rescan(job: Job) -> dict[str, Any]:
    """Re-stat expected poster files after filename/path settings change."""
    from marquee.core.path_utils import safe_translate_and_validate
    from marquee.core.poster_service import render_filename

    factory = _get_session_factory()
    async with factory() as db:
        rows = (
            (await db.execute(select(Movie).where(_downloaded_condition()).order_by(Movie.id)))
            .scalars()
            .all()
        )
        total = len(rows)
        updated = missing = unchanged = failed = 0
        errors: list[dict] = []

        for index, movie in enumerate(rows, 1):
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
                    }
            try:
                folder = safe_translate_and_validate(movie.folder_path, source="radarr")
                expected = folder / render_filename(movie)
                exists_on_disk = await asyncio.to_thread(expected.is_file)
                expected_str = str(expected)
                if exists_on_disk:
                    if movie.poster_path != expected_str:
                        movie.poster_path = expected_str
                        updated += 1
                    else:
                        unchanged += 1
                elif movie.poster_path is not None:
                    movie.poster_path = None
                    missing += 1
                else:
                    unchanged += 1
            except Exception as exc:  # noqa: BLE001
                failed += 1
                errors.append({"movie_id": movie.id, "title": movie.title, "error": str(exc)})

        await db.commit()
        return {
            "updated": updated,
            "missing": missing,
            "unchanged": unchanged,
            "failed": failed,
            "errors": errors,
        }


@register("poster_backup_all")
async def poster_backup_all(job: Job) -> dict[str, Any]:
    """Copy all deployed poster bytes into the local backup directory."""
    from marquee.core.poster_service import _atomic_copy, _sha256, backup_path, cache_paths

    factory = _get_session_factory()
    async with factory() as db:
        rows = (
            (
                await db.execute(
                    select(Movie).where(Movie.poster_path.is_not(None)).order_by(Movie.id)
                )
            )
            .scalars()
            .all()
        )
        total = len(rows)
        copied = skipped = failed = 0
        errors: list[dict] = []

        for index, movie in enumerate(rows, 1):
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
                    }

            dest = backup_path(movie)
            try:
                if await asyncio.to_thread(dest.is_file):
                    if movie.poster_sha256:
                        if await asyncio.to_thread(_sha256, dest) == movie.poster_sha256:
                            skipped += 1
                            movie.poster_local_backup_path = str(dest)
                            continue
                    elif movie.poster_deployed_at:
                        mtime = datetime.fromtimestamp(dest.stat().st_mtime, UTC)
                        if mtime >= movie.poster_deployed_at:
                            skipped += 1
                            movie.poster_local_backup_path = str(dest)
                            continue
                    else:
                        skipped += 1
                        movie.poster_local_backup_path = str(dest)
                        continue

                source: Path | None = None
                if movie.tmdb_id is not None:
                    cache_file, _ = cache_paths(movie.tmdb_id)
                    if await asyncio.to_thread(cache_file.is_file):
                        source = cache_file
                if source is None and movie.poster_path:
                    poster_file = Path(movie.poster_path)
                    if await asyncio.to_thread(poster_file.is_file):
                        source = poster_file
                if source is None:
                    raise FileNotFoundError("no deployed poster file or cache copy found")

                await asyncio.to_thread(_atomic_copy, source, dest)
                movie.poster_local_backup_path = str(dest)
                copied += 1
            except Exception as exc:  # noqa: BLE001
                failed += 1
                errors.append({"movie_id": movie.id, "title": movie.title, "error": str(exc)})

        await db.commit()
        return {
            "checked": total,
            "copied": copied,
            "skipped": skipped,
            "failed": failed,
            "errors": errors,
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
    """Reconcile Radarr-deleted movies and prune orphaned poster caches/backups.

    Job result/events are the audit trail. ArtworkEvents cascade when Movie rows
    are deleted; taste/feedback training data is intentionally left alone.
    """
    from marquee.core.arr_clients.radarr_client import RadarrClient
    from marquee.core.poster_service import backup_path, cache_paths

    dry_run = bool(job.payload.get("dry_run", False))
    force = bool(job.payload.get("force", False))
    if not settings.radarr_configured:
        return {"skipped": "radarr not configured", "dry_run": dry_run}

    radarr = RadarrClient(settings.RADARR_URL, settings.RADARR_API_KEY)
    try:
        await radarr.connect()
        try:
            radarr_movies = await radarr.get_movies()
        except Exception as exc:  # noqa: BLE001
            return {"skipped": f"radarr fetch failed: {exc}", "dry_run": dry_run}
    finally:
        await radarr.disconnect()

    if not radarr_movies:
        return {"skipped": "radarr returned no movies", "dry_run": dry_run}

    radarr_ids = {int(row["id"]) for row in radarr_movies if row.get("id") is not None}
    factory = _get_session_factory()
    async with factory() as db:
        current_movie_ids = set((await db.execute(select(Movie.id))).scalars().all())
        current_tmdb_ids = {
            tmdb_id
            for tmdb_id in (await db.execute(select(Movie.tmdb_id))).scalars().all()
            if tmdb_id is not None
        }
        candidates = (
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
        threshold = max(5, int(0.1 * max(len(radarr_ids), 1)))
        if len(candidates) > threshold and not force:
            raise RuntimeError(
                f"refusing to delete {len(candidates)} movies; exceeds threshold {threshold}"
            )

        deleted_manifest: list[dict] = []
        file_paths: list[Path] = []
        for movie in candidates:
            deleted_manifest.append(
                {
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
            if movie.tmdb_id is not None:
                cache_file, cache_meta = cache_paths(movie.tmdb_id)
                file_paths.extend([cache_file, cache_meta])
            file_paths.append(backup_path(movie))

        backups_pruned = cache_pruned = files_deleted = 0
        backup_dir = settings.poster_backup_path
        cache_dir = settings.poster_cache_path / "movies"
        orphan_backups = [
            entry
            for entry in backup_dir.glob("*.jpg")
            if entry.stem.isdigit() and int(entry.stem) not in current_movie_ids
        ]
        orphan_cache = [
            entry
            for entry in cache_dir.glob("*")
            if entry.suffix in {".jpg", ".json"}
            and entry.stem.removesuffix(".meta").isdigit()
            and int(entry.stem.removesuffix(".meta")) not in current_tmdb_ids
        ]

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
            for movie in candidates:
                await db.delete(movie)
            await db.commit()

        return {
            "movies_deleted": 0 if dry_run else len(candidates),
            "files_deleted": files_deleted,
            "backups_pruned": 0 if dry_run else backups_pruned,
            "cache_pruned": 0 if dry_run else cache_pruned,
            "dry_run": dry_run,
            "deleted": deleted_manifest,
            "orphan_backups": len(orphan_backups),
            "orphan_cache": len(orphan_cache),
            "errors": [],
        }


async def _gather_library_posters() -> tuple[Path, Any, int]:
    """Copy every movie's currently-deployed poster into a temp training dir.

    Prefers the local deployed-poster cache (``data/cache/posters``) and falls
    back to the on-disk poster in the media folder. Returns
    ``(dir, TemporaryDirectory, count)``; the caller cleans up the temp dir.
    """
    import tempfile  # noqa: PLC0415

    factory = _get_session_factory()
    async with factory() as db:
        rows = (
            (await db.execute(select(Movie).where(Movie.poster_path.is_not(None)))).scalars().all()
        )
        movies = [(m.title, m.year, m.tmdb_id, m.poster_path) for m in rows]
    tmp = tempfile.TemporaryDirectory(prefix="marquee-libtrain-")
    dest = Path(tmp.name)
    count = await asyncio.to_thread(_copy_library_posters, movies, dest)
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


@register("job_retention_purge", instant=True)
async def job_retention_purge(_job: Job) -> dict[str, Any]:
    """Delete expired terminal jobs and stale resource / worker rows."""
    from datetime import UTC, datetime, timedelta

    from marquee.models import JobResource, JobResourceReservation, JobWorker, MediaJob

    cutoff = datetime.now(UTC) - timedelta(days=settings.JOB_RETENTION_DAYS)
    stopped_dead_cutoff = datetime.now(UTC) - timedelta(days=7)
    stale_live_cutoff = datetime.now(UTC) - timedelta(hours=24)
    factory = _get_session_factory()
    async with factory() as db:
        rows = (
            await db.execute(
                select(Job.id, Job.payload).where(
                    Job.status.in_(
                        ("succeeded", "failed", "cancelled", "interrupted", "dead_letter")
                    ),
                    Job.finished_at.is_not(None),
                    Job.finished_at < cutoff,
                )
            )
        ).all()

        job_ids = [row.id for row in rows]
        media_job_ids = [
            row.payload.get("media_job_id")
            for row in rows
            if isinstance(row.payload, dict) and row.payload.get("media_job_id")
        ]

        media_jobs_deleted = 0
        if media_job_ids:
            result = await db.execute(
                MediaJob.__table__.delete().where(MediaJob.job_id.in_(media_job_ids))
            )
            media_jobs_deleted = result.rowcount or 0

        jobs_deleted = 0
        if job_ids:
            result = await db.execute(Job.__table__.delete().where(Job.id.in_(job_ids)))
            jobs_deleted = result.rowcount or 0

        active_resource_keys = select(JobResourceReservation.resource_key).where(
            JobResourceReservation.released_at.is_(None)
        )
        resource_result = await db.execute(
            JobResource.__table__.delete().where(
                JobResource.key.like("media-file:%"),
                JobResource.key.not_in(active_resource_keys),
            )
        )
        resources_deleted = resource_result.rowcount or 0

        stopped_dead_result = await db.execute(
            JobWorker.__table__.delete().where(
                JobWorker.status.in_(("stopped", "dead")),
                JobWorker.heartbeat_at < stopped_dead_cutoff,
            )
        )
        stale_live_result = await db.execute(
            JobWorker.__table__.delete().where(
                JobWorker.status.in_(("starting", "running", "draining")),
                JobWorker.heartbeat_at < stale_live_cutoff,
            )
        )
        workers_deleted = (stopped_dead_result.rowcount or 0) + (stale_live_result.rowcount or 0)
        await db.commit()

    logger.info(
        "job_retention_purge: deleted %d job(s), %d bridged media job(s), %d media-file resource row(s), and %d worker row(s)",
        jobs_deleted,
        media_jobs_deleted,
        resources_deleted,
        workers_deleted,
    )
    return {
        "jobs_deleted": jobs_deleted,
        "media_jobs_deleted": media_jobs_deleted,
        "resources_deleted": resources_deleted,
        "workers_deleted": workers_deleted,
    }


@register("system_metrics_purge", instant=True)
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
