"""Subtitle inspection routes (read-only) + the single-movie convenience entry.

These never modify a media file. ``POST /api/movies/{id}/subtitles/inspect`` is
the safe "run it against one movie and show me the output" endpoint: it resolves
the movie's physical file (creating the MediaFile row on demand), probes it, and
returns the full inventory + coverage + capabilities. Mutation lives behind the
plan/confirm/execute job endpoints (separate module).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from marquee.api.library_serializers import effective_movie_preferences
from marquee.core.jobs.manager import UnmigratedJobPlatformError
from marquee.core.media_files import (
    MediaFileNotFoundError,
    MediaFileUnavailableError,
    ensure_media_file_for_movie,
    resolve_media_file,
)
from marquee.core.subtitles import coverage, service
from marquee.core.subtitles.config import subtitle_settings
from marquee.database import get_db
from marquee.media import binaries
from marquee.models import Movie

logger = logging.getLogger(__name__)

router = APIRouter(tags=["subtitles"])


def _require_ffprobe() -> None:
    if binaries.resolve("ffprobe") is None:
        raise HTTPException(
            status_code=503,
            detail="ffprobe not found on PATH — install ffmpeg to inspect subtitles.",
        )


def _map_resolve_error(exc: Exception) -> HTTPException:
    if isinstance(exc, MediaFileNotFoundError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, MediaFileUnavailableError):
        return HTTPException(
            status_code=422, detail={"code": "file_unavailable", "message": str(exc)}
        )
    return HTTPException(status_code=500, detail=str(exc))


# ---------------------------------------------------------------------------
# Media-file-centric inventory
# ---------------------------------------------------------------------------


@router.get("/api/media-files/{media_file_id}/subtitles")
async def get_subtitles(
    media_file_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    force: bool = False,
):
    """Inventory, coverage, capabilities, and per-track actions for a file."""
    _require_ffprobe()
    try:
        return await service.get_inventory_dict(db, media_file_id, force=force)
    except (MediaFileNotFoundError, MediaFileUnavailableError) as exc:
        raise _map_resolve_error(exc) from exc


@router.post("/api/media-files/{media_file_id}/subtitles/scan")
async def scan_subtitles(media_file_id: int, db: Annotated[AsyncSession, Depends(get_db)]):
    """Force a fresh inventory scan (inline for a single file)."""
    _require_ffprobe()
    try:
        return await service.get_inventory_dict(db, media_file_id, force=True)
    except (MediaFileNotFoundError, MediaFileUnavailableError) as exc:
        raise _map_resolve_error(exc) from exc


@router.get("/api/media-files/{media_file_id}/subtitles/{track_id}/preview")
async def preview_track(
    media_file_id: int,
    track_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """First N text cues for a text track; a notice for bitmap subtitles."""
    track = await service.get_track(db, media_file_id, track_id)
    if track is None:
        raise HTTPException(status_code=404, detail="Track not found")

    if track.source == "external" and track.external_path:
        # Confine to the media file's own directory — mirrors download_track().
        try:
            resolved = await resolve_media_file(db, media_file_id)
        except (MediaFileNotFoundError, MediaFileUnavailableError) as exc:
            raise _map_resolve_error(exc) from exc
        path = Path(track.external_path).resolve()
        if path.parent != resolved.path.parent or not path.is_file():
            raise HTTPException(status_code=404, detail="Subtitle file not found on disk")
        return service.text_preview(path)

    # Embedded: extracting a cue preview requires ffmpeg extraction — deferred
    # to the extract job. Report previewability so the UI can offer "extract".
    if track.kind != "text":
        return {"previewable": False, "reason": "bitmap subtitle - text preview unavailable"}
    return {
        "previewable": False,
        "reason": "embedded text preview requires extraction (use the extract action)",
    }


@router.get("/api/media-files/{media_file_id}/subtitles/{track_id}/download")
async def download_track(
    media_file_id: int,
    track_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Safely serve an external (or extracted) subtitle file by track id."""
    track = await service.get_track(db, media_file_id, track_id)
    if track is None or track.source != "external" or not track.external_path:
        raise HTTPException(status_code=404, detail="No downloadable file for this track")

    # Confine to the media file's own directory (the path came from discovery
    # there, but re-verify before serving).
    try:
        resolved = await resolve_media_file(db, media_file_id)
    except (MediaFileNotFoundError, MediaFileUnavailableError) as exc:
        raise _map_resolve_error(exc) from exc
    path = Path(track.external_path).resolve()
    if path.parent != resolved.path.parent or not path.is_file():
        raise HTTPException(status_code=404, detail="Subtitle file not found on disk")
    return FileResponse(path, filename=path.name)


# ---------------------------------------------------------------------------
# Mutation plans (plan → confirm → execute; design §16.3, §23.1)
# ---------------------------------------------------------------------------


class PlanRequest(BaseModel):
    operation: str  # audio_remove | subtitle_remove | subtitle_embed | subtitle_metadata | track_remove | audio_reorder
    track_ids: list[str] = []
    audio_stream_indices: list[int] = []
    audio_stream_order: list[int] = []
    edits: list[dict] = []
    backup: bool = False
    allow_break: bool = False


class MovieSubtitlePreferencesUpdate(BaseModel):
    preferred_audio_languages: list[str] | None = None
    preferred_subtitle_languages: list[str] | None = None
    use_global: bool = False


class LibraryScanRequest(BaseModel):
    force: bool = False
    scope: Literal["movies", "tv", "series"] = "movies"
    series_id: int | None = None
    season_number: int | None = None


@router.post("/api/media-files/{media_file_id}/subtitle-plans", status_code=201)
async def create_subtitle_plan(
    media_file_id: int,
    body: PlanRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Fail closed until subtitle mutation plans use canonical media details."""
    raise UnmigratedJobPlatformError(f"subtitle_plan.create:{media_file_id}")


# ---------------------------------------------------------------------------
# Single-movie convenience entry (the "run it on one movie" endpoint)
# ---------------------------------------------------------------------------

movies_router = APIRouter(prefix="/api/movies", tags=["subtitles"])


@movies_router.post("/{movie_id}/subtitles/inspect")
async def inspect_movie_subtitles(movie_id: int, db: Annotated[AsyncSession, Depends(get_db)]):
    """Resolve a movie's file, probe it, and return its full subtitle inventory."""
    if not subtitle_settings.SUBTITLE_ENABLED:
        raise HTTPException(status_code=503, detail="Subtitle management is disabled")
    _require_ffprobe()

    movie = (await db.execute(select(Movie).where(Movie.id == movie_id))).scalar_one_or_none()
    if movie is None:
        raise HTTPException(status_code=404, detail=f"Movie id={movie_id} not found")

    media_file = await ensure_media_file_for_movie(db, movie)
    if media_file is None:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "no_media_file",
                "message": f"{movie.title!r} has no media file (run sync / download it)",
            },
        )

    try:
        preferences = effective_movie_preferences(movie)
        inventory = await service.get_inventory_dict(
            db,
            media_file.id,
            force=True,
            preferred_audio_languages=preferences["audio"],
            preferred_subtitle_languages=preferences["subtitles"],
        )
    except MediaFileUnavailableError as exc:
        raise _map_resolve_error(exc) from exc

    return {
        "movie_id": movie.id,
        "title": movie.title,
        "media_file_id": media_file.id,
        "path_present": True,
        "inventory": inventory,
        "preferred_languages": preferences,
        "active_job": None,
    }


@movies_router.put("/{movie_id}/subtitles/preferences")
async def update_movie_subtitle_preferences(
    movie_id: int,
    body: MovieSubtitlePreferencesUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Set or reset per-movie preferred audio/subtitle language overrides."""
    movie = (await db.execute(select(Movie).where(Movie.id == movie_id))).scalar_one_or_none()
    if movie is None:
        raise HTTPException(status_code=404, detail=f"Movie id={movie_id} not found")

    if body.use_global:
        movie.preferred_audio_languages_json = None
        movie.preferred_subtitle_languages_json = None
    else:
        movie.preferred_audio_languages_json = coverage.normalize_language_list(
            body.preferred_audio_languages
        )
        movie.preferred_subtitle_languages_json = coverage.normalize_language_list(
            body.preferred_subtitle_languages
        )
    await db.commit()
    await db.refresh(movie)
    return {"movie_id": movie.id, "preferred_languages": effective_movie_preferences(movie)}


@router.post("/api/media-files/{media_file_id}/subtitles/{track_id}/extract", status_code=202)
async def extract_subtitle_track(
    media_file_id: int,
    track_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Fail closed until subtitle extraction has a canonical definition."""
    raise UnmigratedJobPlatformError(f"subtitle_extract:{media_file_id}:{track_id}")


@router.post("/api/subtitles/scan-library", status_code=202)
async def scan_library_subtitles(
    db: Annotated[AsyncSession, Depends(get_db)],
    body: LibraryScanRequest,
):
    """Fail closed until library subtitle scans have a canonical definition."""
    raise UnmigratedJobPlatformError(f"subtitle_scan_all:{body.scope}")
