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

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from marquee.api.job_submission import submission_response
from marquee.api.library_serializers import effective_movie_preferences
from marquee.core.jobs.audio_subtitle_planning import (
    AudioSubtitlePlanError,
    load_before_inventory,
    plan_audio_subtitle_mutation,
    selector_by_stream_index,
)
from marquee.core.jobs.mutation_planning import (
    PlanConflictError,
    PlanValidationError,
    confirm_mutation,
    plan_version,
)
from marquee.core.jobs.submission import Initiator, SubmissionError
from marquee.core.jobs.track_selectors import TrackSelectorError
from marquee.core.media_files import (
    MediaFileNotFoundError,
    MediaFileUnavailableError,
    ensure_media_file_for_movie,
    resolve_media_file,
)
from marquee.core.subtitles import coverage, service
from marquee.core.subtitles.config import subtitle_settings
from marquee.core.subtitles.scan_batch import create_subtitle_scan_batch
from marquee.database import get_db
from marquee.media import binaries
from marquee.models import Job, MediaOperationDetail, Movie

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
        payload = await service.get_inventory_dict(db, media_file_id, force=force)
        _resolved, inventory = await load_before_inventory(db, media_file_id)
        payload["mutation_inventory"] = inventory.model_dump(mode="json")
        return payload
    except (MediaFileNotFoundError, MediaFileUnavailableError) as exc:
        raise _map_resolve_error(exc) from exc


@router.post("/api/media-files/{media_file_id}/subtitles/scan")
async def scan_subtitles(media_file_id: int, db: Annotated[AsyncSession, Depends(get_db)]):
    """Force a fresh inventory scan (inline for a single file)."""
    _require_ffprobe()
    try:
        payload = await service.get_inventory_dict(db, media_file_id, force=True)
        _resolved, inventory = await load_before_inventory(db, media_file_id)
        payload["mutation_inventory"] = inventory.model_dump(mode="json")
        return payload
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
    from marquee.core.filesystem import (  # noqa: PLC0415
        FilesystemBoundaryError,
        boundary_for_roots,
    )

    path = Path(track.external_path)
    try:
        boundary = boundary_for_roots(
            {"media_directory": resolved.path.parent}, purpose="subtitle-download"
        )
        classified = boundary.classify(path, require_file=True)
    except FilesystemBoundaryError as exc:
        raise HTTPException(status_code=404, detail="Subtitle file not found on disk") from exc
    return boundary.response(classified, filename=path.name)


# ---------------------------------------------------------------------------
# Mutation plans (plan → confirm → execute; design §16.3, §23.1)
# ---------------------------------------------------------------------------


class PlanRequest(BaseModel):
    operation: Literal[
        "audio_remove",
        "track_remove",
        "subtitle_remove",
        "audio_reorder",
        "subtitle_metadata",
        "subtitle_extract",
        "subtitle_embed",
        "subtitle_generate",
        "subtitle_policy",
        "subtitle_restore",
    ]
    request: dict[str, object]


class ConfirmMutationRequest(BaseModel):
    expected_plan_version: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    expected_configuration_version: int = Field(ge=0)


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
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=240)],
):
    """Create a transport-free canonical mutation plan with immutable evidence."""
    try:
        async with db.begin():
            result, resolved, _inventory = await plan_audio_subtitle_mutation(
                db,
                job_type=body.operation,
                request=body.request,
                media_file_id=media_file_id,
                idempotency_key=idempotency_key,
                initiator=Initiator(kind="user", identifier="audio-subtitle-api"),
            )
            detail = await db.get(MediaOperationDetail, result.job_id)
            job = await db.get(Job, result.job_id)
            if detail is None or job is None:
                raise PlanValidationError("planned mutation evidence was not persisted")
            version = plan_version(detail)
            expires_at = detail.plan_expires_at
        return {
            "job_id": result.job_id,
            "disposition": result.disposition,
            "phase": result.phase,
            "operation": body.operation,
            "plan_version": version,
            "input_signature": resolved.signature,
            "configuration_version": job.configuration_version,
            "plan_expires_at": expires_at,
            "snapshot_url": result.snapshot_link,
            "detail_url": result.detail_link,
            "confirmation_url": f"/api/jobs/{result.job_id}/mutation-confirmation",
        }
    except PlanConflictError as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": exc.reason, "message": str(exc)},
        ) from exc
    except (AudioSubtitlePlanError, PlanValidationError, SubmissionError, TrackSelectorError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except (MediaFileNotFoundError, MediaFileUnavailableError) as exc:
        raise _map_resolve_error(exc) from exc


@router.post("/api/jobs/{job_id}/mutation-confirmation", status_code=202)
async def confirm_subtitle_plan(
    job_id: str,
    body: ConfirmMutationRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Re-resolve the source and dispatch the same planned job exactly once."""
    try:
        async with db.begin():
            detail = await db.get(MediaOperationDetail, job_id)
            if detail is None or detail.media_file_id is None:
                raise PlanConflictError("missing", "the planned media mutation no longer exists")
            resolved = await resolve_media_file(db, detail.media_file_id)
            result = await confirm_mutation(
                db,
                job_id=job_id,
                expected_plan_version=body.expected_plan_version,
                current_input_signature=resolved.signature,
                confirmed_by=Initiator(kind="user", identifier="audio-subtitle-api"),
                expected_configuration_version=body.expected_configuration_version,
            )
        return submission_response(result)
    except PlanConflictError as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": exc.reason, "message": str(exc)},
        ) from exc
    except (PlanValidationError, SubmissionError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except (MediaFileNotFoundError, MediaFileUnavailableError) as exc:
        raise _map_resolve_error(exc) from exc


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
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=240)],
):
    """Plan extraction using a durable selector resolved from the current inventory."""
    track = await service.get_track(db, media_file_id, track_id)
    if track is None or track.source != "embedded" or track.stream_index is None:
        raise HTTPException(status_code=404, detail="Embedded subtitle track not found")
    try:
        _resolved, inventory = await load_before_inventory(db, media_file_id)
        selector = selector_by_stream_index(
            inventory, kind="subtitle", stream_index=track.stream_index
        )
    except (AudioSubtitlePlanError, MediaFileNotFoundError, MediaFileUnavailableError) as exc:
        if isinstance(exc, (MediaFileNotFoundError, MediaFileUnavailableError)):
            raise _map_resolve_error(exc) from exc
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await db.rollback()
    return await create_subtitle_plan(
        media_file_id,
        PlanRequest(
            operation="subtitle_extract",
            request={
                "media_file_id": media_file_id,
                "selector": selector.model_dump(mode="json"),
            },
        ),
        db,
        idempotency_key,
    )


@router.post("/api/subtitles/scan-library", status_code=202)
async def scan_library_subtitles(
    db: Annotated[AsyncSession, Depends(get_db)],
    body: LibraryScanRequest,
):
    """Submit a fixed batch of read-only subtitle scans for the requested scope (202)."""
    try:
        async with db.begin():
            result = await create_subtitle_scan_batch(
                db,
                parent_job_type="subtitle_scan_all",
                scope=body.scope,
                force=body.force,
                series_id=body.series_id,
                season_number=body.season_number,
                initiator=Initiator(kind="system", identifier="subtitle-scan-api"),
            )
    except SubmissionError as exc:
        raise HTTPException(status_code=422, detail=exc.code) from exc
    return submission_response(result.parent)
