"""Central handlers for non-media workflows."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from sqlalchemy import exists, or_, select

from marquee.config import settings
from marquee.core.jobs import cancel_registry, job_manager
from marquee.core.jobs.handlers import register
from marquee.core.media_files import MediaFileUnavailableError, resolve_media_file
from marquee.core.tv_queries import season_downloaded, series_visible
from marquee.database import _get_session_factory
from marquee.ml.namespaces import TasteNamespace, get_namespace
from marquee.models import (
    Episode,
    EpisodeMediaFile,
    Job,
    MediaFile,
    Movie,
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





@register("letterbox_heal")
async def letterbox_heal(_job: Job) -> dict[str, Any]:
    from marquee.core.letterbox_heal import letterbox_heal_scan  # noqa: PLC0415

    return await letterbox_heal_scan()








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
