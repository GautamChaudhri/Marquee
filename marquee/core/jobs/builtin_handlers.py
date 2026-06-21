"""Central handlers for non-media workflows."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import threading
from pathlib import Path
from typing import Any

from sqlalchemy import select

from marquee.config import settings
from marquee.core.jobs import job_manager
from marquee.core.jobs.handlers import register
from marquee.database import _get_session_factory
from marquee.models import Job, Movie, PipelineRun

logger = logging.getLogger(__name__)


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


@register("letterbox_apply")
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


@register("letterbox_remove")
async def letterbox_remove(job: Job) -> dict[str, Any]:
    from marquee.core.letterbox_service import letterbox_service  # noqa: PLC0415

    factory = _get_session_factory()
    async with factory() as db:
        movie = await db.get(Movie, int(job.payload["movie_id"]))
        if movie is None:
            raise RuntimeError("movie not found")
        result = await letterbox_service.remove(db, movie, source="job")
        return {"removed": result.removed, "path": result.path}


@register("backup_create")
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
    from marquee.pipeline.run_manager import run_manager  # noqa: PLC0415

    source = job.payload.get("source", "training_dir")
    training_dir: Path | None = None
    tmp = None
    gathered: int | None = None
    if source == "library":
        training_dir, tmp, gathered = await _gather_library_posters()
    try:
        await asyncio.to_thread(rebuild_profile, training_dir=training_dir)
        head = (
            await asyncio.to_thread(train_from_labels)
            if pipeline_settings.HEAD_AUTO_RETRAIN
            else None
        )
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
async def taste_map(_job: Job) -> dict[str, Any]:
    from marquee.ml.taste_map import build_map  # noqa: PLC0415

    return await asyncio.to_thread(build_map)


@register("library_sync")
async def library_sync(_job: Job) -> dict[str, Any]:
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
    try:
        factory = _get_session_factory()
        async with factory() as db:
            report = await SyncService(db, radarr=radarr, sonarr=sonarr, tmdb=tmdb).sync_all()
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

    cancel_event = threading.Event()

    async def _watch_cancel() -> None:
        while not cancel_event.is_set():
            await asyncio.sleep(2.0)
            async with factory() as watch_db:
                current = await watch_db.get(Job, job.id)
            if current is None or current.cancel_requested:
                cancel_event.set()
                return

    tmdb = TMDBClient(read_access_token=settings.TMDB_READ_ACCESS_TOKEN)
    await tmdb.connect()
    # Load the model stack once, off the event loop, for the whole batch.
    extractor = await asyncio.to_thread(run_manager._ensure_extractor)
    watcher = asyncio.create_task(_watch_cancel())
    try:
        async with JobProgressBridge(job.id) as bridge:
            summary = await run_batch(
                job_id=job.id,
                movies=movies,
                tmdb=tmdb,
                extractor=extractor,
                progress=bridge.callback,
                should_cancel=cancel_event.is_set,
            )
    finally:
        cancel_event.set()
        watcher.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await watcher
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

    head, info = await asyncio.to_thread(train_from_labels)
    return {"trained": head is not None, **info}


@register("pipeline_cache_clear")
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
            (await db.execute(select(Movie).where(Movie.poster_path.is_not(None))))
            .scalars()
            .all()
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
