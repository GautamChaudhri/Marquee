"""Letterbox routes — detect, review, and apply MKV crop tags (design 04).

Three UI tabs (Candidates / Tagged / Skipped) are just ``status`` filters over
``GET /candidates``. Detection is read-only; tag mutation goes through
``LetterboxService``. Batch detect streams progress over SSE, mirroring the
pipeline run endpoints.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from marquee.api.deps import enforce_rate_limit, get_rate_limiter
from marquee.config import settings
from marquee.core import letterbox_reencode
from marquee.core.letterbox_service import IneligibleError, letterbox_service
from marquee.core.media_files import (
    MediaFileNotFoundError,
    MediaFileUnavailableError,
    ensure_media_file_for_movie,
    resolve_media_file,
)
from marquee.core.media_jobs import media_job_manager
from marquee.core.rate_limit import RateLimiter
from marquee.database import get_db
from marquee.media import binaries, letterbox_preview
from marquee.media.letterbox_manager import BatchInProgressError, letterbox_manager
from marquee.media.probe import prefilter_bucket
from marquee.models import LetterboxEvent, LetterboxReencodeArtifact, LetterboxState, Movie

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/letterbox", tags=["letterbox"])

# MKV pixel-crop tags are honored by these players only (design §7).
_HONORED_BY = ["plex-desktop", "vlc", "mpv"]
_NOT_HONORED_BY = ["plex-web", "plex-mobile"]
_PREFILTER_STATUSES = {
    "prefilter_candidate",
    "prefilter_unknown",
    "prefilter_skipped",
}
_DETECTOR_TRUTH_STATUSES = {
    "candidate",
    "not_letterboxed",
    "variable_unsafe",
    "tagged",
    "reencoded",
    "skipped",
    "ineligible",
}


def _is_prefilter_schema_error(exc: OperationalError) -> bool:
    message = str(exc.orig if getattr(exc, "orig", None) else exc)
    return "letterbox_state" in message and "prefilter_" in message


def _raise_prefilter_schema_error() -> None:
    raise HTTPException(
        status_code=503,
        detail=(
            "Letterbox prefilter schema is not installed yet. Run "
            "`alembic upgrade head` and restart Marquee."
        ),
    )


def _require_ffmpeg() -> None:
    if binaries.resolve("ffmpeg") is None:
        raise HTTPException(
            status_code=503,
            detail="ffmpeg not found on PATH — install it to run letterbox detection.",
        )


def _state_to_dict(state: LetterboxState, movie: Movie | None = None) -> dict:
    data = {
        "movie_id": state.movie_id,
        "status": state.status,
        "confidence": state.confidence,
        "eligible": state.eligible,
        "ineligible_reason": state.ineligible_reason,
        "source_width": state.source_width,
        "source_height": state.source_height,
        "recommended_crop_top": state.recommended_crop_top,
        "recommended_crop_bottom": state.recommended_crop_bottom,
        "aspect_label": state.aspect_label,
        "applied_crop_top": state.applied_crop_top,
        "applied_crop_bottom": state.applied_crop_bottom,
        "detect_method": state.detect_method,
        "reviewed": state.reviewed,
        "last_detected_at": state.last_detected_at.isoformat() if state.last_detected_at else None,
        "last_applied_at": state.last_applied_at.isoformat() if state.last_applied_at else None,
        "error": state.error,
        "prefilter_bucket": state.prefilter_bucket,
        "prefilter_reason": state.prefilter_reason,
        "prefilter_aspect_ratio": state.prefilter_aspect_ratio,
        "last_prefiltered_at": _iso_or_none(state.last_prefiltered_at),
    }
    if movie is not None:
        data["title"] = movie.title
        data["year"] = movie.year
    return data


def _iso_or_none(value) -> str | None:
    return value.isoformat() if value else None


def _resolution_label(width: int | None, height: int | None) -> str | None:
    if not width or not height:
        return None
    return f"{width}x{height}"


def _prefilter_category(movie: Movie) -> tuple[str, dict]:
    result = prefilter_bucket(movie.video_width, movie.video_height)
    category = "unknown_resolution" if result.reason == "unknown_resolution" else result.bucket
    aspect_ratio = round(result.aspect_ratio, 4) if result.aspect_ratio is not None else None
    return category, {
        "bucket": result.bucket,
        "category": category,
        "reason": result.reason,
        "aspect_ratio": aspect_ratio,
    }


def _prefilter_status_for_category(category: str) -> str:
    if category == "candidate":
        return "prefilter_candidate"
    if category == "unknown_resolution":
        return "prefilter_unknown"
    return "prefilter_skipped"


def _state_has_detector_truth(state: LetterboxState | None) -> bool:
    if state is None:
        return False
    if state.status in _PREFILTER_STATUSES or state.status == "errored":
        return False
    if state.last_detected_at is not None:
        return True
    if state.reviewed or state.applied_crop_top is not None or state.applied_crop_bottom is not None:
        return True
    if state.status in _DETECTOR_TRUTH_STATUSES:
        return state.recommended_crop_top is not None or state.status != "candidate"
    return False


def _apply_prefilter_state(
    state: LetterboxState,
    movie: Movie,
    *,
    category: str,
    prefilter: dict,
    now: datetime,
) -> None:
    state.prefilter_bucket = prefilter["bucket"]
    state.prefilter_reason = prefilter["reason"]
    state.prefilter_aspect_ratio = prefilter["aspect_ratio"]
    state.last_prefiltered_at = now

    # Keep the latest sync dimensions visible before expensive detection runs.
    if state.last_detected_at is None:
        state.source_width = movie.video_width
        state.source_height = movie.video_height

    if not _state_has_detector_truth(state):
        state.status = _prefilter_status_for_category(category)
        state.confidence = None
        state.recommended_crop_top = None
        state.recommended_crop_bottom = None
        state.aspect_label = None
        state.detect_method = None
        state.samples_json = None
        state.reviewed = False
        state.error = None


def _should_enqueue_for_detection(movie: Movie, state: LetterboxState | None = None) -> bool:
    if not movie.movie_file_path:
        return False
    if _state_has_detector_truth(state):
        return False
    category, _ = _prefilter_category(movie)
    return category in {"candidate", "unknown_resolution"}


def _letterbox_state_summary(state: LetterboxState | None) -> dict | None:
    if state is None:
        return None
    return {
        "status": state.status,
        "confidence": state.confidence,
        "reviewed": state.reviewed,
        "eligible": state.eligible,
        "ineligible_reason": state.ineligible_reason,
        "source_width": state.source_width,
        "source_height": state.source_height,
        "recommended_crop_top": state.recommended_crop_top,
        "recommended_crop_bottom": state.recommended_crop_bottom,
        "aspect_label": state.aspect_label,
        "applied_crop_top": state.applied_crop_top,
        "applied_crop_bottom": state.applied_crop_bottom,
        "last_detected_at": _iso_or_none(state.last_detected_at),
        "last_applied_at": _iso_or_none(state.last_applied_at),
        "error": state.error,
        "prefilter_bucket": state.prefilter_bucket,
        "prefilter_reason": state.prefilter_reason,
        "prefilter_aspect_ratio": state.prefilter_aspect_ratio,
        "last_prefiltered_at": _iso_or_none(state.last_prefiltered_at),
    }


async def _dolby_vision_for_movie(db: AsyncSession, movie: Movie) -> dict:
    try:
        media_file = await ensure_media_file_for_movie(db, movie)
        if media_file is None:
            return letterbox_reencode.dovi_info(None)
        resolved = await resolve_media_file(db, media_file.id)
    except (MediaFileNotFoundError, MediaFileUnavailableError):
        return letterbox_reencode.dovi_info(None)
    source = letterbox_reencode.inspect_source(resolved.path)
    return letterbox_reencode.dovi_info(source)


def _prefilter_movie_to_dict(movie: Movie, state: LetterboxState | None) -> dict:
    category, prefilter = _prefilter_category(movie)
    has_file = bool(movie.movie_file_path)
    return {
        "movie_id": movie.id,
        "title": movie.title,
        "year": movie.year,
        "tmdb_id": movie.tmdb_id,
        "imdb_id": movie.imdb_id,
        "radarr_id": movie.radarr_id,
        "folder_path": movie.folder_path,
        "movie_file_path": movie.movie_file_path,
        "has_file": has_file,
        "container": movie.container,
        "has_dv": movie.has_dv,
        "source_width": movie.video_width,
        "source_height": movie.video_height,
        "resolution": _resolution_label(movie.video_width, movie.video_height),
        "prefilter": prefilter,
        "is_resolution_candidate": category == "candidate",
        "needs_probe": category == "unknown_resolution",
        "already_analyzed": _state_has_detector_truth(state),
        "will_detect_in_all_candidates_batch": _should_enqueue_for_detection(movie, state),
        "letterbox_state": _letterbox_state_summary(state),
    }


async def _load_movie(db: AsyncSession, movie_id: int) -> Movie:
    movie = (
        await db.execute(select(Movie).where(Movie.id == movie_id))
    ).scalar_one_or_none()
    if movie is None:
        raise HTTPException(status_code=404, detail=f"Movie id={movie_id} not found")
    return movie


async def _load_state(db: AsyncSession, movie_id: int) -> LetterboxState:
    state = (
        await db.execute(
            select(LetterboxState).where(LetterboxState.movie_id == movie_id)
        )
    ).scalar_one_or_none()
    if state is None:
        raise HTTPException(
            status_code=404,
            detail=f"No letterbox detection for movie {movie_id} — run detect first.",
        )
    return state


# ---------------------------------------------------------------------------
# Status + listing
# ---------------------------------------------------------------------------


@router.get("/status")
async def letterbox_status(db: Annotated[AsyncSession, Depends(get_db)]):
    rows = (
        await db.execute(
            select(LetterboxState.status, func.count())
            .group_by(LetterboxState.status)
        )
    ).all()
    counts = dict(rows)
    last_scan = (
        await db.execute(select(func.max(LetterboxState.last_detected_at)))
    ).scalar_one_or_none()
    return {
        "enabled": settings.LETTERBOX_ENABLED,
        "method": settings.LETTERBOX_DETECT_METHOD,
        "counts": counts,
        "binaries": binaries.availability(),
        "honored_by": _HONORED_BY,
        "not_honored_by": _NOT_HONORED_BY,
        "last_scan": last_scan.isoformat() if last_scan else None,
        "batch_active": letterbox_manager.active_job_id,
    }


@router.get("/candidates")
async def list_candidates(
    db: Annotated[AsyncSession, Depends(get_db)],
    status: str | None = None,
    confidence: str | None = None,
    sort: str = "confidence",
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
):
    """Paginated letterbox state, joined to movie identity. Drives all 3 tabs."""
    query = select(LetterboxState, Movie).join(Movie, Movie.id == LetterboxState.movie_id)
    if status:
        query = query.where(LetterboxState.status.in_(status.split(",")))
    if confidence:
        query = query.where(LetterboxState.confidence == confidence)

    total = (
        await db.execute(select(func.count()).select_from(query.subquery()))
    ).scalar_one()

    # Sort: confidence (high→low), title, or crop size.
    if sort == "title":
        query = query.order_by(Movie.title)
    elif sort == "crop":
        query = query.order_by(LetterboxState.recommended_crop_top.desc().nullslast())
    else:  # confidence
        order = func.coalesce(
            func.nullif(LetterboxState.confidence, ""), "zzz"
        )
        query = query.order_by(order, Movie.title)

    query = query.limit(page_size).offset((page - 1) * page_size)
    rows = (await db.execute(query)).all()
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": [_state_to_dict(state, movie) for state, movie in rows],
    }


@router.get("/movies/find-candidates")
async def find_candidate_movies(
    db: Annotated[AsyncSession, Depends(get_db)],
    include_skipped: bool = Query(
        False,
        description="Include movies rejected by the resolution pre-filter.",
    ),
    include_analyzed: bool = Query(
        False,
        description="Include movies that already have detector truth in letterbox_state.",
    ),
):
    """Classify all movies by stored Radarr resolution; no ffmpeg, no media writes."""
    try:
        rows = (
            await db.execute(
                select(Movie, LetterboxState)
                .outerjoin(LetterboxState, LetterboxState.movie_id == Movie.id)
                .order_by(Movie.title, Movie.year)
            )
        ).all()
    except OperationalError as exc:
        if _is_prefilter_schema_error(exc):
            _raise_prefilter_schema_error()
        raise

    items = []
    returned_movie_ids = []
    candidate_movie_ids = []
    unknown_resolution_movie_ids = []
    detectable_movie_ids = []
    counts = {
        "candidate": 0,
        "unknown_resolution": 0,
        "skipped_by_resolution": 0,
        "missing_file_path": 0,
        "already_analyzed": 0,
        "will_detect_in_all_candidates_batch": 0,
    }
    now = datetime.now(UTC)

    for movie, state in rows:
        if not movie.movie_file_path:
            counts["missing_file_path"] += 1
            if state is None:
                state = LetterboxState(movie_id=movie.id)
                db.add(state)
            if not _state_has_detector_truth(state):
                state.status = "prefilter_skipped"
                state.prefilter_bucket = "no_file"
                state.prefilter_reason = "missing_movie_file_path"
                state.last_prefiltered_at = now
            continue

        if state is None:
            state = LetterboxState(movie_id=movie.id)
            db.add(state)

        already_analyzed = _state_has_detector_truth(state)
        item = _prefilter_movie_to_dict(movie, state)
        category = item["prefilter"]["category"]
        _apply_prefilter_state(
            state,
            movie,
            category=category,
            prefilter=item["prefilter"],
            now=now,
        )
        item = _prefilter_movie_to_dict(movie, state)

        if already_analyzed:
            counts["already_analyzed"] += 1
        elif category == "candidate":
            counts["candidate"] += 1
            candidate_movie_ids.append(movie.id)
        elif category == "unknown_resolution":
            counts["unknown_resolution"] += 1
            unknown_resolution_movie_ids.append(movie.id)
        else:
            counts["skipped_by_resolution"] += 1

        if item["will_detect_in_all_candidates_batch"]:
            counts["will_detect_in_all_candidates_batch"] += 1
            detectable_movie_ids.append(movie.id)

        if already_analyzed and not include_analyzed:
            continue
        if category in {"candidate", "unknown_resolution"} or include_skipped:
            items.append(item)
            returned_movie_ids.append(movie.id)

    await db.commit()
    return {
        "total_movies": len(rows),
        "returned": len(items),
        "include_skipped": include_skipped,
        "include_analyzed": include_analyzed,
        "counts": counts,
        "movie_ids": returned_movie_ids,
        "candidate_movie_ids": candidate_movie_ids,
        "unknown_resolution_movie_ids": unknown_resolution_movie_ids,
        "detectable_movie_ids": detectable_movie_ids,
        "items": items,
    }


@router.get("/movies/{movie_id}")
async def get_movie_detail(
    movie_id: int, db: Annotated[AsyncSession, Depends(get_db)]
):
    movie = await _load_movie(db, movie_id)
    state = await _load_state(db, movie_id)
    samples = json.loads(state.samples_json) if state.samples_json else []
    # Preview at the first successfully-measured sample (or minute 5).
    preview_minute = next(
        (s["minute"] for s in samples if s.get("ok")), 5
    )
    detail = _state_to_dict(state, movie)
    detail["samples"] = samples
    detail["honored_by"] = _HONORED_BY
    detail["not_honored_by"] = _NOT_HONORED_BY
    detail["dolby_vision"] = await _dolby_vision_for_movie(db, movie)
    detail["preview_minute"] = preview_minute
    detail["preview_urls"] = {
        "before": f"/api/letterbox/movies/{movie_id}/preview?mode=before&minute={preview_minute}",
        "after": f"/api/letterbox/movies/{movie_id}/preview?mode=after&minute={preview_minute}",
    }
    return detail


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------


class BatchDetectRequest(BaseModel):
    movie_ids: list[int] | None = None
    all_candidates: bool = False


async def _resolve_batch_movie_ids(
    body: BatchDetectRequest, db: AsyncSession
) -> list[int]:
    if body.movie_ids:
        return body.movie_ids
    if body.all_candidates:
        rows = (
            await db.execute(
                select(Movie, LetterboxState)
                .outerjoin(LetterboxState, LetterboxState.movie_id == Movie.id)
                .where(Movie.movie_file_path.is_not(None))
            )
        ).all()
        movie_ids = [
            movie.id
            for movie, state in rows
            if _should_enqueue_for_detection(movie, state)
        ]
        return movie_ids
    raise HTTPException(
        status_code=400, detail="Provide movie_ids or set all_candidates=true"
    )


async def _start_detect_job(
    body: BatchDetectRequest,
    db: AsyncSession,
    *,
    detector: str,
) -> dict:
    movie_ids = await _resolve_batch_movie_ids(body, db)
    if not movie_ids:
        raise HTTPException(status_code=400, detail="No matching candidate movies")

    try:
        job_id = await letterbox_manager.start_batch(movie_ids, detector=detector)
    except BatchInProgressError as exc:
        raise HTTPException(
            status_code=409,
            detail={"message": "A letterbox batch is already running", "job_id": exc.active_job_id},
        ) from exc

    return {
        "job_id": job_id,
        "detector": detector,
        "total": len(movie_ids),
        "events_url": f"/api/letterbox/jobs/{job_id}/events",
    }


@router.post("/movies/{movie_id}/detect")
async def detect_one(
    movie_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    limiter: Annotated[RateLimiter, Depends(get_rate_limiter)],
):
    """Detect a single movie synchronously and return its updated state."""
    _require_ffmpeg()
    enforce_rate_limit(limiter, f"lb_detect:{movie_id}", settings.RATE_LETTERBOX_DETECT_SECONDS)
    movie = await _load_movie(db, movie_id)
    state = await letterbox_manager.detect_and_store(db, movie)
    limiter.record(f"lb_detect:{movie_id}")
    detail = _state_to_dict(state, movie)
    detail["dolby_vision"] = await _dolby_vision_for_movie(db, movie)
    return detail


@router.post("/detect", status_code=202)
async def detect_batch(
    body: BatchDetectRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    limiter: Annotated[RateLimiter, Depends(get_rate_limiter)],
):
    """Start a background batch detect (SSE progress). 202 + job_id, or 409."""
    _require_ffmpeg()
    enforce_rate_limit(limiter, "lb_detect_batch", settings.RATE_LETTERBOX_BATCH_SECONDS)
    result = await _start_detect_job(body, db, detector="v2")
    limiter.record("lb_detect_batch")
    return result


@router.get("/jobs/{job_id}/events")
async def job_events(job_id: str):
    """SSE stream of batch-detect progress (history replay → live → done)."""
    state = letterbox_manager.get_state(job_id)
    if state is None:
        raise HTTPException(status_code=404, detail=f"No batch job {job_id}")

    async def event_generator():
        from marquee.media.letterbox_manager import _SENTINEL  # noqa: PLC0415

        queue = state.subscribe()
        try:
            while True:
                event = await queue.get()
                if event is _SENTINEL:
                    yield "event: done\ndata: {}\n\n"
                    return
                yield f"data: {json.dumps(event)}\n\n"
        finally:
            state.unsubscribe(queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ---------------------------------------------------------------------------
# Preview frames
# ---------------------------------------------------------------------------


@router.get("/movies/{movie_id}/preview")
async def movie_preview(
    movie_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    mode: str = "before",
    minute: int = 5,
):
    _require_ffmpeg()
    movie = await _load_movie(db, movie_id)
    state = await _load_state(db, movie_id)
    eligibility = letterbox_service.check_eligibility(movie)
    if eligibility.path is None:
        raise HTTPException(status_code=404, detail="Media file unavailable for preview")

    import asyncio  # noqa: PLC0415

    samples = json.loads(state.samples_json) if state.samples_json else []
    candidate_minutes = [s["minute"] for s in samples if s.get("ok")]

    out = await asyncio.to_thread(
        letterbox_preview.generate_preview,
        eligibility.path,
        movie_id=movie_id,
        minute=minute,
        mode=mode,
        crop_top=state.recommended_crop_top or 0,
        crop_bottom=state.recommended_crop_bottom or 0,
        height=state.source_height,
        candidate_minutes=candidate_minutes,
    )
    if out is None:
        raise HTTPException(status_code=404, detail="Could not generate preview")
    # Confine served files to the preview cache tree.
    resolved = Path(out).resolve()
    if not str(resolved).startswith(str(settings.letterbox_preview_path.resolve())):
        raise HTTPException(status_code=403, detail="Preview path outside cache tree")
    return FileResponse(resolved, media_type="image/webp")


# ---------------------------------------------------------------------------
# Apply / remove / ignore
# ---------------------------------------------------------------------------


class ApplyRequest(BaseModel):
    top: int | None = None
    bottom: int | None = None


class BatchApplyRequest(BaseModel):
    movie_ids: list[int]
    only_high: bool = False


class ReencodePlanRequest(BaseModel):
    top: int | None = None
    bottom: int | None = None
    allow_cpu_fallback: bool | None = None


class RestoreReencodeRequest(BaseModel):
    keep_candidate: bool = False


@router.post("/movies/{movie_id}/apply")
async def apply_one(
    movie_id: int,
    body: ApplyRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    movie = await _load_movie(db, movie_id)
    state = await _load_state(db, movie_id)
    if state.status == "variable_unsafe":
        raise HTTPException(
            status_code=422,
            detail="Variable aspect ratio (contains 16:9 scenes) — unsafe to crop.",
        )
    top = body.top if body.top is not None else state.recommended_crop_top
    bottom = body.bottom if body.bottom is not None else state.recommended_crop_bottom
    if not top and not bottom:
        raise HTTPException(status_code=422, detail="No crop to apply (recommendation is 0).")

    try:
        result = await letterbox_service.apply(
            db, movie, top=top or 0, bottom=bottom or 0, source="api"
        )
    except IneligibleError as exc:
        raise HTTPException(status_code=422, detail=f"Cannot apply: {exc.reason}") from exc
    return {"applied": result.applied, "top": result.top, "bottom": result.bottom, "verified": result.verified}


@router.post("/apply")
async def apply_batch(
    body: BatchApplyRequest, db: Annotated[AsyncSession, Depends(get_db)]
):
    """Apply recommended crops to several movies (e.g. all High-confidence)."""
    results = []
    for movie_id in body.movie_ids:
        movie = (
            await db.execute(select(Movie).where(Movie.id == movie_id))
        ).scalar_one_or_none()
        state = (
            await db.execute(
                select(LetterboxState).where(LetterboxState.movie_id == movie_id)
            )
        ).scalar_one_or_none()
        if movie is None or state is None:
            results.append({"movie_id": movie_id, "applied": False, "reason": "not_found"})
            continue
        if state.status == "variable_unsafe" or not state.recommended_crop_top:
            results.append({"movie_id": movie_id, "applied": False, "reason": "not_applicable"})
            continue
        if body.only_high and state.confidence != "high":
            results.append({"movie_id": movie_id, "applied": False, "reason": "not_high_confidence"})
            continue
        try:
            await letterbox_service.apply(
                db, movie,
                top=state.recommended_crop_top or 0,
                bottom=state.recommended_crop_bottom or 0,
                source="api",
            )
            results.append({"movie_id": movie_id, "applied": True})
        except IneligibleError as exc:
            results.append({"movie_id": movie_id, "applied": False, "reason": exc.reason})
    applied = sum(1 for r in results if r["applied"])
    return {"applied": applied, "total": len(body.movie_ids), "results": results}


def _map_reencode_error(exc: letterbox_reencode.ReencodePlanError) -> HTTPException:
    return HTTPException(
        status_code=422,
        detail={"code": exc.code, "message": str(exc), "warnings": exc.warnings},
    )


async def _load_reencode_artifact(
    db: AsyncSession, artifact_id: int
) -> LetterboxReencodeArtifact:
    artifact = await db.get(LetterboxReencodeArtifact, artifact_id)
    if artifact is None:
        raise HTTPException(status_code=404, detail=f"Re-encode artifact {artifact_id} not found")
    return artifact


@router.post("/movies/{movie_id}/reencode-plan", status_code=201)
async def create_reencode_plan(
    movie_id: int,
    body: ReencodePlanRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Plan a permanent cropped re-encode. No media file is written here."""
    _require_ffmpeg()
    movie = await _load_movie(db, movie_id)
    state = await _load_state(db, movie_id)
    if state.status == "variable_unsafe":
        raise HTTPException(
            status_code=422,
            detail={"code": "variable_unsafe", "message": "Variable aspect ratio is unsafe to crop permanently."},
        )
    top = body.top if body.top is not None else state.recommended_crop_top
    bottom = body.bottom if body.bottom is not None else state.recommended_crop_bottom
    if not top and not bottom:
        raise HTTPException(
            status_code=422,
            detail={"code": "missing_crop", "message": "No crop to apply (recommendation is 0)."},
        )
    media_file = await ensure_media_file_for_movie(db, movie)
    if media_file is None:
        raise HTTPException(
            status_code=422,
            detail={"code": "missing_media_file", "message": "Movie has no media file path."},
        )
    try:
        resolved = await resolve_media_file(db, media_file.id)
        plan = await letterbox_reencode.build_plan(
            db,
            resolved,
            top=top or 0,
            bottom=bottom or 0,
            allow_cpu_fallback=body.allow_cpu_fallback,
        )
    except (MediaFileNotFoundError, MediaFileUnavailableError) as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "file_unavailable", "message": str(exc)},
        ) from exc
    except letterbox_reencode.ReencodePlanError as exc:
        raise _map_reencode_error(exc) from exc

    expires_at = datetime.now(UTC) + timedelta(hours=2)
    job = await media_job_manager.create_job(
        db,
        operation="letterbox_reencode",
        media_file_id=media_file.id,
        trigger="manual",
        request={
            "movie_id": movie_id,
            "top": top or 0,
            "bottom": bottom or 0,
            "allow_cpu_fallback": body.allow_cpu_fallback,
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


@router.get("/reencode-artifacts")
async def list_reencode_artifacts(
    db: Annotated[AsyncSession, Depends(get_db)],
    status: str | None = None,
    movie_id: int | None = None,
    limit: int = Query(100, ge=1, le=500),
):
    query = select(LetterboxReencodeArtifact).order_by(
        LetterboxReencodeArtifact.created_at.desc()
    ).limit(limit)
    if status:
        query = query.where(LetterboxReencodeArtifact.status == status)
    if movie_id:
        query = query.where(LetterboxReencodeArtifact.movie_id == movie_id)
    rows = (await db.execute(query)).scalars().all()
    return {
        "summary": await letterbox_reencode.artifact_summary(db),
        "items": [letterbox_reencode.artifact_to_dict(row) for row in rows],
    }


@router.post("/reencode-artifacts/{artifact_id}/replace-original")
async def replace_reencode_original(
    artifact_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    artifact = await _load_reencode_artifact(db, artifact_id)
    try:
        return await letterbox_reencode.replace_original(db, artifact)
    except letterbox_reencode.ReencodePlanError as exc:
        raise _map_reencode_error(exc) from exc


@router.post("/reencode-artifacts/{artifact_id}/restore-original")
async def restore_reencode_original(
    artifact_id: int,
    body: RestoreReencodeRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    artifact = await _load_reencode_artifact(db, artifact_id)
    try:
        return await letterbox_reencode.restore_original(
            db, artifact, keep_candidate=body.keep_candidate
        )
    except letterbox_reencode.ReencodePlanError as exc:
        raise _map_reencode_error(exc) from exc


@router.delete("/reencode-artifacts/{artifact_id}")
async def delete_reencode_artifact(
    artifact_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    artifact = await _load_reencode_artifact(db, artifact_id)
    return await letterbox_reencode.delete_artifact_files(db, artifact)


@router.post("/movies/{movie_id}/remove")
async def remove_one(movie_id: int, db: Annotated[AsyncSession, Depends(get_db)]):
    movie = await _load_movie(db, movie_id)
    await _load_state(db, movie_id)
    try:
        result = await letterbox_service.remove(db, movie, source="api")
    except IneligibleError as exc:
        raise HTTPException(status_code=422, detail=f"Cannot remove: {exc.reason}") from exc
    return {"removed": result.removed, "path": result.path}


@router.post("/movies/{movie_id}/ignore")
async def ignore_one(movie_id: int, db: Annotated[AsyncSession, Depends(get_db)]):
    """Mark a movie reviewed → Skipped tab; won't be re-flagged on re-scan."""
    await _load_movie(db, movie_id)
    state = await _load_state(db, movie_id)
    state.status = "skipped"
    state.reviewed = True
    db.add(LetterboxEvent(movie_id=movie_id, action="ignore", source="api", detail="{}"))
    await db.commit()
    return _state_to_dict(state)


@router.post("/heal")
async def letterbox_heal():
    """Re-apply crop tags that drifted off tagged files (tag-drift scan)."""
    from marquee.core.letterbox_heal import letterbox_heal_scan  # noqa: PLC0415

    return await letterbox_heal_scan()


# ---------------------------------------------------------------------------
# Dev / debug helpers — bulk and per-movie state resets
# ---------------------------------------------------------------------------

_NOT_LB_STATUSES = {"not_letterboxed", "variable_unsafe", "skipped"}


def _wipe_detection(state: LetterboxState, now: datetime) -> None:
    """Clear all detection and application fields; set status to prefilter_candidate."""
    state.status = "prefilter_candidate"
    state.confidence = None
    state.recommended_crop_top = None
    state.recommended_crop_bottom = None
    state.applied_crop_top = None
    state.applied_crop_bottom = None
    state.aspect_label = None
    state.detect_method = None
    state.samples_json = None
    state.error = None
    state.reviewed = False
    state.last_detected_at = None
    state.last_applied_at = None
    state.last_prefiltered_at = now


@router.post("/dev/reset-not-letterboxed")
async def dev_reset_not_letterboxed(db: Annotated[AsyncSession, Depends(get_db)]):
    """Dev: move all not_letterboxed / variable_unsafe / skipped back to Candidates."""
    result = await db.execute(
        select(LetterboxState).where(LetterboxState.status.in_(list(_NOT_LB_STATUSES)))
    )
    states = result.scalars().all()
    now = datetime.now(UTC)
    for state in states:
        _wipe_detection(state, now)
    await db.commit()
    return {"reset": len(states)}


@router.post("/movies/{movie_id}/reset")
async def dev_reset_movie(movie_id: int, db: Annotated[AsyncSession, Depends(get_db)]):
    """Dev: fully clear all letterbox data for one movie → back to Candidates."""
    await _load_movie(db, movie_id)
    result = await db.execute(
        select(LetterboxState).where(LetterboxState.movie_id == movie_id)
    )
    state = result.scalar_one_or_none()
    if state is None:
        raise HTTPException(status_code=404, detail="No letterbox state found for this movie")
    _wipe_detection(state, datetime.now(UTC))
    await db.commit()
    return {"reset": True, "movie_id": movie_id}
