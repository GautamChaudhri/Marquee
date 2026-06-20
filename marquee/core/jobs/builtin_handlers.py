"""Central handlers for non-media workflows."""

from __future__ import annotations

import asyncio
from typing import Any

from sqlalchemy import select

from marquee.config import settings
from marquee.core.jobs.handlers import register
from marquee.database import _get_session_factory
from marquee.models import Job, Movie, PipelineRun


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
    from marquee.media.letterbox_manager import letterbox_manager  # noqa: PLC0415

    factory = _get_session_factory()
    async with factory() as db:
        movie = await db.get(Movie, int(job.payload["movie_id"]))
        if movie is None:
            raise RuntimeError("movie not found")
        state = await letterbox_manager.detect_and_store(db, movie, detector=job.payload.get("detector", "v2"))
        return {"movie_id": movie.id, "status": state.status, "confidence": state.confidence}


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
        return {"applied": result.applied, "top": result.top, "bottom": result.bottom, "verified": result.verified}


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
async def taste_rebuild(_job: Job) -> dict[str, Any]:
    """Run training outside FastAPI; the worker owns the exclusive GPU lease."""
    from marquee.core.pipeline_config import pipeline_settings  # noqa: PLC0415
    from marquee.ml.head_trainer import train_from_labels  # noqa: PLC0415
    from marquee.ml.taste_trainer import rebuild_profile  # noqa: PLC0415

    await asyncio.to_thread(rebuild_profile)
    head = await asyncio.to_thread(train_from_labels) if pipeline_settings.HEAD_AUTO_RETRAIN else None
    return {"rebuild": "completed", "head": head[1] if head else None}


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

    radarr = RadarrClient(settings.RADARR_URL, settings.RADARR_API_KEY) if settings.radarr_configured else None
    sonarr = SonarrClient(settings.SONARR_URL, settings.SONARR_API_KEY) if settings.sonarr_configured else None
    tmdb = TMDBClient(read_access_token=settings.TMDB_READ_ACCESS_TOKEN) if settings.tmdb_configured else None
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
            db.add(PipelineRun(run_id=run_id, movie_id=movie.id, status="running", output_dir=str(settings.runs_work_path / movie.title)))
            await db.commit()
        movie_id, title, tmdb_id = movie.id, movie.title, movie.tmdb_id
    if tmdb_id is None:
        raise RuntimeError("movie has no TMDB ID")
    tmdb = TMDBClient(read_access_token=settings.TMDB_READ_ACCESS_TOKEN)
    await tmdb.connect()
    run_manager._active_run_id = run_id
    run_manager._runs[run_id] = RunState(run_id=run_id)
    try:
        await run_manager._execute(run_id=run_id, tmdb=tmdb, movie_id=movie_id, movie_title=title, movie_tmdb_id=tmdb_id)
    finally:
        await tmdb.disconnect()
    return {"run_id": run_id}
