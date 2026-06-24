"""Sync routes — trigger *arr → database sync."""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from marquee.api.deps import get_radarr, get_sonarr, get_tmdb
from marquee.config import settings
from marquee.core.arr_clients.radarr_client import RadarrClient
from marquee.core.arr_clients.sonarr_client import SonarrClient
from marquee.core.poster_sources.tmdb import TMDBClient
from marquee.core.sync_service import SyncService
from marquee.database import get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/sync", tags=["sync"])


@router.post("/all")
async def sync_all(
    db: Annotated[AsyncSession, Depends(get_db)],
    radarr: Annotated[RadarrClient, Depends(get_radarr)],
    sonarr: Annotated[SonarrClient, Depends(get_sonarr)],
    tmdb: Annotated[TMDBClient, Depends(get_tmdb)],
):
    """Sync all movies and TV shows from Radarr/Sonarr into the database.

    Runs inline: it is network + DB only (no GPU, no real CPU load), so it does
    not go through the job manager — the caller gets the full sync report back.
    """
    logger.info(
        "Sync started — source=radarr=%s sonarr=%s tmdb=%s",
        settings.RADARR_URL or "unconfigured",
        settings.SONARR_URL or "unconfigured",
        "configured" if settings.tmdb_configured else "unconfigured",
    )

    svc = SyncService(db, radarr=radarr, sonarr=sonarr, tmdb=tmdb)
    report = await svc.sync_all()

    logger.info(
        "Sync complete — %.1fs | movies +%d/~%d | series +%d/~%d | "
        "seasons +%d/~%d | episodes +%d/~%d | errors %d",
        report.duration_seconds,
        report.movies.created,
        report.movies.updated,
        report.series.created,
        report.series.updated,
        report.seasons.created,
        report.seasons.updated,
        report.episodes.created,
        report.episodes.updated,
        report.movies.errors
        + report.series.errors
        + report.seasons.errors
        + report.episodes.errors,
    )

    return {
        "status": "ok",
        "duration_seconds": report.duration_seconds,
        "movies": {
            "created": report.movies.created,
            "updated": report.movies.updated,
            "errors": report.movies.errors,
        },
        "series": {
            "created": report.series.created,
            "updated": report.series.updated,
            "errors": report.series.errors,
        },
        "seasons": {
            "created": report.seasons.created,
            "updated": report.seasons.updated,
        },
        "episodes": {
            "created": report.episodes.created,
            "updated": report.episodes.updated,
        },
    }
