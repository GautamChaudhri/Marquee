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
import json
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


async def _letterbox_stale_after_upgrade(radarr_id: int | None, tmdb_id: int | None) -> None:
    """An upgrade replaces the file → its crop tags are gone. Re-queue detection.

    Cheap and write-free on the media file: clears the recorded applied crop and
    flips the row back to a fresh ``candidate`` so it reappears in the queue. The
    actual re-detect is left to the user / batch scan (it spends ffmpeg time).
    """
    if not settings.LETTERBOX_ENABLED or settings.WEBHOOK_DRY_RUN:
        return
    from marquee.models import LetterboxEvent, LetterboxState  # noqa: PLC0415

    factory = _get_session_factory()
    async with factory() as db:
        stmt = select(Movie)
        stmt = stmt.where(
            Movie.radarr_id == radarr_id if radarr_id is not None else Movie.tmdb_id == tmdb_id
        )
        movie = (await db.execute(stmt)).scalar_one_or_none()
        if movie is None:
            return
        state = (
            await db.execute(
                select(LetterboxState).where(LetterboxState.movie_id == movie.id)
            )
        ).scalar_one_or_none()
        if state is None:
            return
        state.applied_crop_top = None
        state.applied_crop_bottom = None
        state.status = "candidate"
        state.reviewed = False
        db.add(
            LetterboxEvent(
                movie_id=movie.id, action="detect", source="webhook",
                detail='{"reason": "stale after upgrade — re-detect queued"}',
            )
        )
        await db.commit()
        logger.info("WEBHOOK | letterbox state staled for %s (upgrade)", movie.title)


@router.post("/radarr")
async def radarr_webhook(
    payload: RadarrWebhookPayload,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Receive Radarr events; restore posters on upgrade (fast ACK)."""
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
        # The new file has no crop tags — re-queue letterbox detection.
        asyncio.create_task(
            _letterbox_stale_after_upgrade(payload.movie.id, payload.movie.tmdbId)
        )
        # Queue a durable subtitle inventory scan for the (new) file.
        asyncio.create_task(
            _schedule_subtitle_scan(payload.movie.id, payload.movie.tmdbId)
        )
        return {"status": "restore_scheduled", "movie": payload.movie.title}

    return {"status": "ignored", "eventType": event}


@router.post("/subgen")
async def subgen_callback(payload: dict, token: str | None = None):
    """Subgen completion callback (design §24.4) — an optimization, not the SoT.

    The generation worker reconciles via the filesystem regardless; this just
    records the callback so we have an audit trail and can surface it.
    """
    from datetime import UTC, datetime  # noqa: PLC0415

    from marquee.core.subtitles.config import subtitle_settings  # noqa: PLC0415

    if subtitle_settings.SUBGEN_CALLBACK_TOKEN and token != subtitle_settings.SUBGEN_CALLBACK_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid Subgen callback token")
    webhook_state.update(
        last_received=datetime.now(UTC).isoformat(), last_event="subgen_callback"
    )
    logger.info("WEBHOOK | subgen callback: %s", json.dumps(payload, default=str)[:300])
    return {"ok": True}


async def _schedule_subtitle_scan(radarr_id: int | None, tmdb_id: int | None) -> None:
    """Durable subtitle scan job for an imported/upgraded movie (§26.1)."""
    from marquee.core.media_files import ensure_media_file_for_movie  # noqa: PLC0415
    from marquee.core.media_jobs import media_job_manager  # noqa: PLC0415
    from marquee.core.subtitles.config import subtitle_settings  # noqa: PLC0415
    from marquee.models import MediaJob  # noqa: PLC0415

    if not subtitle_settings.SUBTITLE_ENABLED:
        return
    factory = _get_session_factory()
    async with factory() as db:
        stmt = select(Movie).where(
            Movie.radarr_id == radarr_id if radarr_id is not None else Movie.tmdb_id == tmdb_id
        )
        movie = (await db.execute(stmt)).scalar_one_or_none()
        if movie is None:
            return
        media_file = await ensure_media_file_for_movie(db, movie)
        if media_file is None:
            return
        key = f"radarr:{movie.id}:subtitle-scan:{media_file.path}"
        existing = (
            await db.execute(select(MediaJob).where(MediaJob.idempotency_key == key))
        ).scalar_one_or_none()
        if existing is not None:
            return  # collapse duplicate import webhooks
        await media_job_manager.create_job(
            db, operation="subtitle_scan", media_file_id=media_file.id,
            trigger="webhook", status="queued", idempotency_key=key,
        )


@router.post("/sonarr")
async def sonarr_webhook(
    payload: RadarrWebhookPayload,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Receive Sonarr events. Currently path-updates on Rename only.

    Sonarr's file-level upgrades don't delete the series/season posters, so
    full restoration is deferred; the payload is parsed and Test/Rename are
    handled so the webhook can be configured today.
    """
    from datetime import UTC, datetime  # noqa: PLC0415

    webhook_state.update(
        last_received=datetime.now(UTC).isoformat(), last_event=payload.eventType
    )
    if payload.eventType == "Test":
        return {"ok": True, "message": "Marquee Sonarr webhook reachable"}
    return {"status": "ignored", "eventType": payload.eventType}
