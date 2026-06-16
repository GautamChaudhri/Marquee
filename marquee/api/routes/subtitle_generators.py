"""Subtitle generation provider routes (design §24.2, §25.4).

Reports the *real* capabilities of the configured external generator (Subgen)
and submits generation jobs. Per-request model/percentage controls are NOT
exposed because Subgen does not support them.
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from marquee.core.media_files import ensure_media_file_for_movie
from marquee.core.media_jobs import media_job_manager
from marquee.core.subtitles import generation
from marquee.core.subtitles.config import subtitle_settings
from marquee.database import get_db
from marquee.models import Movie

logger = logging.getLogger(__name__)

router = APIRouter(tags=["subtitle-generators"])


@router.get("/api/subtitle-generators")
async def list_generators():
    """Provider health + actual capabilities for the frontend to choose from."""
    return {"generators": await generation.list_generators()}


class GenerateRequest(BaseModel):
    generator_id: str | None = None
    language_hint: str | None = None  # None = auto-detect
    output: str = "external"  # external | embedded


@router.post("/api/media-files/{media_file_id}/subtitle-generations", status_code=202)
async def generate_for_media_file(
    media_file_id: int,
    body: GenerateRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Queue a generation job for a media file (runs on the durable worker)."""
    if not subtitle_settings.generation_enabled:
        raise HTTPException(status_code=503, detail="Generation disabled — set SUBGEN_URL")
    job = await media_job_manager.create_job(
        db,
        operation="subtitle_generate",
        media_file_id=media_file_id,
        trigger="manual",
        request=body.model_dump(),
        status="queued",
    )
    return {"job_id": job.job_id, "events_url": f"/api/media-jobs/{job.job_id}/events"}


@router.post("/api/movies/{movie_id}/subtitle-generations", status_code=202)
async def generate_for_movie(
    movie_id: int,
    body: GenerateRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Convenience: queue generation for a movie (resolves its media file)."""
    if not subtitle_settings.generation_enabled:
        raise HTTPException(status_code=503, detail="Generation disabled — set SUBGEN_URL")
    movie = (await db.execute(select(Movie).where(Movie.id == movie_id))).scalar_one_or_none()
    if movie is None:
        raise HTTPException(status_code=404, detail=f"Movie id={movie_id} not found")
    media_file = await ensure_media_file_for_movie(db, movie)
    if media_file is None:
        raise HTTPException(status_code=422, detail="Movie has no media file")
    job = await media_job_manager.create_job(
        db,
        operation="subtitle_generate",
        media_file_id=media_file.id,
        trigger="manual",
        request=body.model_dump(),
        status="queued",
    )
    return {"job_id": job.job_id, "events_url": f"/api/media-jobs/{job.job_id}/events"}
