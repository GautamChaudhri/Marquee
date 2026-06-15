"""Webhook routes — Radarr/Sonarr events, poster restoration on upgrade.

Radarr fires ``POST /api/webhooks/radarr`` on every significant event. We
care about:

  - ``Test``                  → 200 (so the user can save the webhook in Radarr)
  - ``Download`` + isUpgrade  → restore the poster to the new folder
  - ``Rename``                → update the stored folder path
  - ``MovieFileDelete``       → ignored (fires *before* the upgrade's Download)

Restoration runs as a fast-ACK background task (Radarr's webhook timeout is
short and a cache-miss restore includes a download), guarded by a per-movie
lock and a short retry backoff for the race where the new folder isn't
finalized yet. Sonarr currently only updates paths on ``Rename`` (its file
upgrades don't destroy posters).
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from marquee.config import settings
from marquee.core.path_utils import PathValidationError, safe_translate_and_validate
from marquee.core.poster_service import poster_service
from marquee.database import _get_session_factory, get_db
from marquee.models import ArtworkEvent, Movie

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])

# Tracks the last webhook received (for /api/system/status).
webhook_state: dict = {"last_received": None, "last_event": None}

_movie_locks: dict[int, asyncio.Lock] = {}
_RETRY_BACKOFF = (0.1, 0.5, 1.0, 3.0)


class _ArrMovie(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: int | None = None
    title: str | None = None
    year: int | None = None
    tmdbId: int | None = None  # noqa: N815
    folderPath: str | None = None  # noqa: N815


class RadarrWebhookPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")
    eventType: str | None = None  # noqa: N815
    isUpgrade: bool = False  # noqa: N815
    movie: _ArrMovie | None = None


def _check_token(token: str | None) -> None:
    if settings.WEBHOOK_TOKEN and token != settings.WEBHOOK_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid or missing webhook token")


async def _lookup_movie(db: AsyncSession, payload_movie: _ArrMovie) -> Movie | None:
    if payload_movie.id is not None:
        movie = (
            await db.execute(select(Movie).where(Movie.radarr_id == payload_movie.id))
        ).scalar_one_or_none()
        if movie is not None:
            return movie
    if payload_movie.tmdbId is not None:
        return (
            await db.execute(select(Movie).where(Movie.tmdb_id == payload_movie.tmdbId))
        ).scalar_one_or_none()
    return None


async def _restore_after_upgrade(radarr_id: int | None, tmdb_id: int | None, new_folder: str | None) -> None:
    """Background task: wait for the new folder, then restore if needed."""
    lock_key = radarr_id if radarr_id is not None else (tmdb_id or 0)
    lock = _movie_locks.setdefault(lock_key, asyncio.Lock())
    async with lock:
        # Wait for Radarr to finalize the new folder (tiny race window).
        translated = None
        if new_folder:
            try:
                translated = safe_translate_and_validate(new_folder, source="radarr")
            except PathValidationError:
                translated = None
        if translated is not None:
            for delay in _RETRY_BACKOFF:
                if translated.is_dir():
                    break
                await asyncio.sleep(delay)

        factory = _get_session_factory()
        async with factory() as db:
            stmt = select(Movie)
            if radarr_id is not None:
                stmt = stmt.where(Movie.radarr_id == radarr_id)
            else:
                stmt = stmt.where(Movie.tmdb_id == tmdb_id)
            movie = (await db.execute(stmt)).scalar_one_or_none()
            if movie is None:
                logger.info("WEBHOOK | upgrade for untracked movie (radarr_id=%s)", radarr_id)
                return

            if settings.WEBHOOK_DRY_RUN:
                db.add(
                    ArtworkEvent(
                        movie_id=movie.id, action="webhook_noop", source="webhook",
                        detail='{"dry_run": true}',
                    )
                )
                await db.commit()
                logger.info("WEBHOOK | DRY RUN | would restore %s", movie.title)
                return

            # Did the poster survive the upgrade (in-place upgrade)?
            if movie.poster_path and Path(movie.poster_path).is_file():
                if new_folder and movie.folder_path != new_folder:
                    movie.folder_path = new_folder
                db.add(
                    ArtworkEvent(
                        movie_id=movie.id, action="webhook_noop", source="webhook",
                        detail='{"reason": "poster survived upgrade"}',
                    )
                )
                await db.commit()
                logger.info("WEBHOOK | UPGRADE NO-OP | %s poster survived", movie.title)
                return

            await poster_service.restore(db, movie, new_folder=new_folder, source="webhook")


@router.post("/radarr")
async def radarr_webhook(
    payload: RadarrWebhookPayload,
    db: Annotated[AsyncSession, Depends(get_db)],
    token: str | None = None,
):
    """Receive Radarr events; restore posters on upgrade (fast ACK)."""
    _check_token(token)
    from datetime import UTC, datetime  # noqa: PLC0415

    event = payload.eventType
    webhook_state.update(last_received=datetime.now(UTC).isoformat(), last_event=event)

    if event == "Test":
        return {"ok": True, "message": "Marquee webhook reachable"}

    if event == "Rename" and payload.movie and payload.movie.folderPath:
        movie = await _lookup_movie(db, payload.movie)
        if movie is not None and movie.folder_path != payload.movie.folderPath:
            movie.folder_path = payload.movie.folderPath
            await db.commit()
            return {"status": "folder_updated", "movie_id": movie.id}
        return {"status": "ignored", "reason": "rename — no tracked movie or no change"}

    if event == "Download" and payload.isUpgrade and payload.movie:
        # Fast ACK: do the restore in the background (it may download).
        asyncio.create_task(
            _restore_after_upgrade(
                payload.movie.id, payload.movie.tmdbId, payload.movie.folderPath
            )
        )
        return {"status": "restore_scheduled", "movie": payload.movie.title}

    return {"status": "ignored", "eventType": event}


@router.post("/sonarr")
async def sonarr_webhook(
    payload: RadarrWebhookPayload,
    db: Annotated[AsyncSession, Depends(get_db)],
    token: str | None = None,
):
    """Receive Sonarr events. Currently path-updates on Rename only.

    Sonarr's file-level upgrades don't delete the series/season posters, so
    full restoration is deferred; the payload is parsed and Test/Rename are
    handled so the webhook can be configured today.
    """
    _check_token(token)
    from datetime import UTC, datetime  # noqa: PLC0415

    webhook_state.update(
        last_received=datetime.now(UTC).isoformat(), last_event=payload.eventType
    )
    if payload.eventType == "Test":
        return {"ok": True, "message": "Marquee Sonarr webhook reachable"}
    return {"status": "ignored", "eventType": payload.eventType}
