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
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from marquee.core.media_files import (
    MediaFileNotFoundError,
    MediaFileUnavailableError,
    ensure_media_file_for_movie,
    resolve_media_file,
)
from marquee.core.media_jobs import media_job_manager
from marquee.core.media_jobs.serialize import job_dict as _media_job_dict
from marquee.core.subtitles import mutation, service
from marquee.core.subtitles.config import subtitle_settings
from marquee.database import get_db
from marquee.media import binaries
from marquee.models import MediaJob, Movie

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
        return HTTPException(status_code=422, detail={"code": "file_unavailable", "message": str(exc)})
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
async def scan_subtitles(
    media_file_id: int, db: Annotated[AsyncSession, Depends(get_db)]
):
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
    operation: str  # subtitle_remove | subtitle_embed | subtitle_metadata
    track_ids: list[str] = []
    edits: list[dict] = []
    backup: bool = False
    allow_break: bool = False


@router.post("/api/media-files/{media_file_id}/subtitle-plans", status_code=201)
async def create_subtitle_plan(
    media_file_id: int,
    body: PlanRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Persist an expiring before/after plan as a ``planned`` job (no writes)."""
    _require_ffprobe()
    try:
        resolved = await resolve_media_file(db, media_file_id)
        inventory = await service.get_inventory_dict(db, media_file_id)
    except (MediaFileNotFoundError, MediaFileUnavailableError) as exc:
        raise _map_resolve_error(exc) from exc

    try:
        plan = await mutation.build_plan(
            db, resolved, inventory,
            operation=body.operation,
            params={"track_ids": body.track_ids, "edits": body.edits},
            backup_requested=body.backup,
        )
    except mutation.PlanError as exc:
        raise HTTPException(status_code=422, detail={"code": "plan_error", "message": str(exc)}) from exc

    expires_at = mutation.now_plus_ttl()
    job = await media_job_manager.create_job(
        db,
        operation=body.operation,
        media_file_id=media_file_id,
        trigger="manual",
        request={
            "inventory_id": inventory["inventory_id"],
            "track_ids": body.track_ids,
            "edits": body.edits,
            "backup": body.backup,
            "allow_break": body.allow_break,
        },
        plan=plan,
        status="planned",
        input_signature=resolved.signature,
        plan_expires_at=expires_at,
    )
    return {
        "job_id": job.job_id,
        "status": "planned",
        "expires_at": expires_at.isoformat(),
        **plan,
    }


# ---------------------------------------------------------------------------
# Single-movie convenience entry (the "run it on one movie" endpoint)
# ---------------------------------------------------------------------------

movies_router = APIRouter(prefix="/api/movies", tags=["subtitles"])


@movies_router.post("/{movie_id}/subtitles/inspect")
async def inspect_movie_subtitles(
    movie_id: int, db: Annotated[AsyncSession, Depends(get_db)]
):
    """Resolve a movie's file, probe it, and return its full subtitle inventory.

    Read-only. Creates the MediaFile row on demand so it works without a fresh
    sync. This is the safe way to exercise the feature on one title.
    """
    if not subtitle_settings.SUBTITLE_ENABLED:
        raise HTTPException(status_code=503, detail="Subtitle management is disabled")
    _require_ffprobe()

    movie = (
        await db.execute(select(Movie).where(Movie.id == movie_id))
    ).scalar_one_or_none()
    if movie is None:
        raise HTTPException(status_code=404, detail=f"Movie id={movie_id} not found")

    media_file = await ensure_media_file_for_movie(db, movie)
    if media_file is None:
        raise HTTPException(
            status_code=422,
            detail={"code": "no_media_file", "message": f"{movie.title!r} has no media file (run sync / download it)"},
        )

    try:
        inventory = await service.get_inventory_dict(db, media_file.id, force=True)
    except MediaFileUnavailableError as exc:
        raise _map_resolve_error(exc) from exc

    # Re-attach to a running job so the progress bar survives a refresh —
    # the page's loader calls this endpoint on every load, already scoped
    # to this exact media file.
    active_job = (
        await db.execute(
            select(MediaJob)
            .where(
                MediaJob.media_file_id == media_file.id,
                MediaJob.status.in_(["queued", "running"]),
            )
            .order_by(MediaJob.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()

    return {
        "movie_id": movie.id,
        "title": movie.title,
        "media_file_id": media_file.id,
        "path_present": True,
        "inventory": inventory,
        "active_job": _media_job_dict(active_job) if active_job else None,
    }


@router.post("/api/media-files/{media_file_id}/subtitles/{track_id}/extract", status_code=202)
async def extract_subtitle_track(
    media_file_id: int,
    track_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Queue a job to extract an embedded subtitle track to an external sidecar."""
    if not subtitle_settings.SUBTITLE_ENABLED:
        raise HTTPException(status_code=503, detail="Subtitle management is disabled")
    try:
        resolved = await resolve_media_file(db, media_file_id)
    except (MediaFileNotFoundError, MediaFileUnavailableError) as exc:
        raise _map_resolve_error(exc) from exc

    track = await service.get_track(db, media_file_id, track_id)
    if track is None or track.source != "embedded":
        raise HTTPException(status_code=404, detail="Embedded track not found")

    job = await media_job_manager.create_job(
        db,
        operation="subtitle_extract",
        media_file_id=media_file_id,
        trigger="manual",
        request={"track_id": track_id},
        status="confirmed",
    )
    return {"job_id": job.job_id, "status": "queued"}


@router.post("/api/subtitles/scan-library", status_code=202)
async def scan_library_subtitles(
    db: Annotated[AsyncSession, Depends(get_db)],
    force: bool = False,
):
    """Enqueue a job to scan subtitle coverage for all active media files in the library."""
    from marquee.core.jobs.manager import job_manager  # noqa: PLC0415
    job = await job_manager.create(
        db,
        job_type="subtitle_scan_all",
        payload={"force": force},
    )
    return {"job_id": job.id, "status": "queued"}

