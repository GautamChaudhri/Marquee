"""Letterbox routes — detect, review, and apply MKV crop tags (design 04).

Three UI tabs (Candidates / Tagged / Skipped) are just ``status`` filters over
``GET /candidates``. Detection is read-only; tag mutation goes through
``LetterboxService``. Batch detect streams progress over SSE, mirroring the
pipeline run endpoints.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import BaseModel
from sqlalchemy import case, exists, func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from marquee.api.deps import enforce_rate_limit, get_rate_limiter
from marquee.api.routes.jobs import job_summary
from marquee.config import settings
from marquee.core import letterbox_reencode
from marquee.core.jobs import job_manager
from marquee.core.jobs.labels import humanize_job_type
from marquee.core.jobs.manager import ACTIVE, TERMINAL
from marquee.core.letterbox_prefilter import (
    prefilter_category,
    refresh_letterbox_prefilter_for_movie,
    state_has_detector_truth,
    tv_dimension_class,
)
from marquee.core.letterbox_rollups import (
    BAR_BEARING_BUCKETS,
    EpisodeLetterbox,
    season_rollup,
    show_rollup,
)
from marquee.core.letterbox_service import letterbox_service
from marquee.core.media_files import (
    MediaFileNotFoundError,
    MediaFileUnavailableError,
    ensure_media_file_for_movie,
    resolve_media_file,
)
from marquee.core.media_jobs import media_job_manager
from marquee.core.rate_limit import RateLimiter
from marquee.core.sort_title import title_sort_expr
from marquee.core.tv_queries import series_visible
from marquee.database import get_db
from marquee.media import binaries, letterbox_detect, letterbox_preview
from marquee.media.concurrency import gated
from marquee.models import (
    Episode,
    EpisodeMediaFile,
    Job,
    LetterboxEvent,
    LetterboxReencodeArtifact,
    LetterboxState,
    MediaFile,
    MediaJob,
    Movie,
    Series,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/letterbox", tags=["letterbox"])


async def _file_lock(db: AsyncSession, movie: Movie) -> dict[str, int]:
    """Per-physical-file reservation (capacity 1) for a movie's active file.

    Keying every operation that touches one file — letterbox detect/apply/remove
    *and* the subtitle/reencode media jobs — on the same ``media-file:{id}``
    namespace is what makes them mutually exclusive (e.g. a read-only detect can
    never overlap a crop-tag write or a re-encode of the same file).  Empty when
    the movie has no resolvable media file, so there is nothing to lock.
    """
    media_file = await ensure_media_file_for_movie(db, movie)
    return {f"media-file:{media_file.id}": 1} if media_file is not None else {}


# MKV pixel-crop tags are honored by these players only (design §7).
_HONORED_BY = ["plex-desktop", "vlc", "mpv"]
_NOT_HONORED_BY = ["plex-web", "plex-mobile"]
# Job statuses worth surfacing on the movie detail so the re-encode card stays
# visible. `interrupted` is included: a worker restart (deploy, crash, dev
# --reload) marks the running job interrupted, but the user still needs to see
# it — and recover it — rather than have it silently vanish into the candidate
# view. Only the *latest* job is surfaced, so discarding it never resurfaces an
# older interrupted run.
_SURFACED_REENCODE_STATUSES = ("planned", "queued", "running", "interrupted")


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
        "variable_ar": state.variable_ar,
        "variable_ar_note": state.variable_ar_note,
    }
    if movie is not None:
        data["title"] = movie.title
        data["year"] = movie.year
    return data


# `reviewed=True` normally means "decision finalized, previews purged" (confirm/
# ignore/mark-not-letterboxed all purge_previews() explicitly) — but these three
# statuses are auto-marked reviewed the moment detection runs, with the file
# untouched and nothing ever purged, so previews stay safe to render on demand.
_PREVIEWABLE_WHEN_REVIEWED = {"not_letterboxed", "variable_unsafe", "sampled_clear"}


def _preview_blocked(state: LetterboxState) -> bool:
    return bool(state.reviewed) and state.status not in _PREVIEWABLE_WHEN_REVIEWED


def _sample_preview_entries(samples: list[dict], *, url_for: Callable[[int], str]) -> list[dict]:
    entries: list[dict] = []
    for sample in samples:
        minute = sample.get("minute")
        if not isinstance(minute, int):
            continue
        ok = bool(sample.get("ok"))
        entries.append(
            {
                "minute": minute,
                "ok": ok,
                "url": url_for(minute) if ok else None,
            }
        )
    return entries


def _iso_or_none(value) -> str | None:
    return value.isoformat() if value else None


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=UTC)


async def _generic_media_job_bridge(db: AsyncSession, media_job: MediaJob) -> Job | None:
    rows = (await db.execute(select(Job).where(Job.type == media_job.operation))).scalars().all()
    return next((row for row in rows if row.payload.get("media_job_id") == media_job.job_id), None)


def _resolution_label(width: int | None, height: int | None) -> str | None:
    if not width or not height:
        return None
    return f"{width}x{height}"


def _prefilter_category(movie: Movie) -> tuple[str, dict]:
    return prefilter_category(movie)


def _state_has_detector_truth(state: LetterboxState | None) -> bool:
    return state_has_detector_truth(state)


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
        "variable_ar": state.variable_ar,
        "variable_ar_note": state.variable_ar_note,
    }


def _dolby_vision_summary(movie: Movie) -> dict:
    """Cheap Dolby Vision presence from the synced ``has_dv`` column — no probe.

    The movie-detail and detect responses must stay subprocess-free so they
    return instantly even while an encode saturates disk I/O. ``ffprobe`` on a
    4K file over a busy mergerfs mount can take tens of seconds, which used to
    time the detail fetch out entirely. Full DoVi detail (profile/level/RPU
    preservation) is probed only in ``build_plan``, where the user has
    explicitly chosen a permanent re-encode and a one-off probe is warranted.
    """
    if movie.has_dv is None:
        # Not yet checked by sync — report unknown rather than probing here.
        reason = "not_checked"
    elif movie.has_dv:
        reason = "probed_on_reencode_plan"
    else:
        return {
            "present": False,
            "profile": None,
            "level": None,
            "el_present": None,
            "bl_signal_compatibility_id": None,
            "preservation": {"status": "not_present", "supported": False, "reason": None},
        }
    return {
        "present": bool(movie.has_dv),
        "profile": None,
        "level": None,
        "el_present": None,
        "bl_signal_compatibility_id": None,
        "preservation": {"status": "unknown", "supported": False, "reason": reason},
    }


def _media_job_summary(job: MediaJob) -> dict:
    return {
        "job_id": job.job_id,
        "operation": job.operation,
        "label": humanize_job_type(job.operation),
        "status": job.status,
        "stage": job.stage,
        "trigger": job.trigger,
        "media_file_id": job.media_file_id,
        "batch_id": job.batch_id,
        "progress_done": job.progress_done,
        "progress_total": job.progress_total,
        "plan": json.loads(job.plan_json) if job.plan_json else None,
        "result": json.loads(job.result_json) if job.result_json else None,
        "error": json.loads(job.error_json) if job.error_json else None,
        "input_signature": job.input_signature,
        "plan_expires_at": _iso_or_none(job.plan_expires_at),
        "confirmed_at": _iso_or_none(job.confirmed_at),
        "created_at": _iso_or_none(job.created_at),
        "updated_at": _iso_or_none(job.updated_at),
    }


async def _latest_reencode_snapshot(db: AsyncSession, movie: Movie) -> dict | None:
    media_file = await ensure_media_file_for_movie(db, movie)
    if media_file is None:
        return None

    # Take the single most-recent job for this file, then surface it only if its
    # status is one we show. Filtering by status *before* ordering would let
    # discarding the latest interrupted run resurface an older interrupted one.
    job_result = await db.execute(
        select(MediaJob)
        .where(
            MediaJob.operation == "letterbox_reencode",
            MediaJob.media_file_id == media_file.id,
        )
        .order_by(MediaJob.created_at.desc(), MediaJob.job_id.desc())
        .limit(1)
    )
    latest = job_result.scalars().first()
    job = latest if latest is not None and latest.status in _SURFACED_REENCODE_STATUSES else None
    artifact_result = await db.execute(
        select(LetterboxReencodeArtifact)
        .where(LetterboxReencodeArtifact.movie_id == movie.id)
        .order_by(LetterboxReencodeArtifact.created_at.desc(), LetterboxReencodeArtifact.id.desc())
    )
    artifact = artifact_result.scalars().first()
    if job is None and artifact is None:
        return None
    return {
        "job": _media_job_summary(job) if job is not None else None,
        "artifact": letterbox_reencode.artifact_to_dict(artifact) if artifact is not None else None,
    }


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
    movie = (await db.execute(select(Movie).where(Movie.id == movie_id))).scalar_one_or_none()
    if movie is None:
        raise HTTPException(status_code=404, detail=f"Movie id={movie_id} not found")
    return movie


async def _load_state(db: AsyncSession, movie_id: int) -> LetterboxState:
    state = (
        await db.execute(
            select(LetterboxState).where(
                LetterboxState.media_type == "movie",
                LetterboxState.movie_id == movie_id,
            )
        )
    ).scalar_one_or_none()
    if state is None:
        raise HTTPException(
            status_code=404,
            detail=f"No letterbox detection for movie {movie_id} — run detect first.",
        )
    return state


def _workflow_funnel_from_status_rows(rows: list[tuple[str | None, bool]]) -> dict[str, int]:
    return {
        "candidates": sum(
            1
            for status, _reviewed in rows
            if status in {"prefilter_candidate", "prefilter_unknown"}
        ),
        "staging": sum(1 for status, _reviewed in rows if status == "candidate"),
        "preview": sum(
            1 for status, reviewed in rows if status == "tagged" and not reviewed
        ),
        "processed": sum(
            1 for status, reviewed in rows if status == "tagged" and reviewed
        ),
    }


def _workflow_funnel_from_states(states: list[LetterboxState | None]) -> dict[str, int]:
    return _workflow_funnel_from_status_rows(
        [
            (state.status if state is not None else None, bool(state.reviewed) if state is not None else False)
            for state in states
        ]
    )


def _verdict_breakdown_key(state: LetterboxState | None) -> str:
    if state is None or state.status in {"prefilter_candidate", "prefilter_unknown", "prefilter_skipped"}:
        return "unanalyzed"
    if state.status == "not_letterboxed":
        return "clear"
    if state.status == "sampled_clear":
        return "sampled_clear"
    if state.status in {"candidate", "skipped"}:
        return "letterboxed_untreated"
    if state.status == "tagged":
        return "tagged"
    if state.status == "reencoded":
        return "reencoded"
    if state.status == "variable_unsafe":
        return "variable"
    if state.status == "ineligible":
        return "ineligible"
    if state.status == "errored":
        return "error"
    return "unanalyzed"


def _aggregate_verdict_breakdown(states: list[LetterboxState | None]) -> dict[str, int]:
    counts = {
        "clear": 0,
        "sampled_clear": 0,
        "letterboxed_untreated": 0,
        "tagged": 0,
        "reencoded": 0,
        "variable": 0,
        "ineligible": 0,
        "error": 0,
        "unanalyzed": 0,
    }
    for state in states:
        counts[_verdict_breakdown_key(state)] += 1
    return counts


def _episode_display_aspect_label(state: LetterboxState | None) -> str | None:
    if state is None:
        return None
    if state.aspect_label:
        return state.aspect_label
    if (
        state.status == "not_letterboxed"
        and state.last_detected_at is not None
        and state.source_width
        and state.source_height
    ):
        return letterbox_detect.aspect_label(state.source_width, state.source_height)
    return None


def _episode_letterbox_row(
    episode: Episode,
    state: LetterboxState | None,
    media_file_id: int | None,
) -> tuple[EpisodeLetterbox, dict]:
    status = state.status if state is not None else None
    aspect_label = _episode_display_aspect_label(state)
    dimension_class = tv_dimension_class(episode.video_width, episode.video_height)
    bucket_override = dimension_class if dimension_class and not _state_has_detector_truth(state) else None
    if bucket_override:
        aspect_label = letterbox_detect.aspect_label(episode.video_width, episode.video_height)
    item = EpisodeLetterbox(
        episode_id=episode.id,
        season_number=episode.season_number,
        episode_number=episode.episode_number,
        title=episode.title,
        status=status,
        confidence=state.confidence if state is not None else None,
        aspect_label=aspect_label,
        recommended_crop_top=state.recommended_crop_top if state is not None else None,
        recommended_crop_bottom=state.recommended_crop_bottom if state is not None else None,
        applied_crop_top=state.applied_crop_top if state is not None else None,
        applied_crop_bottom=state.applied_crop_bottom if state is not None else None,
        eligible=state.eligible if state is not None else None,
        reviewed=bool(state.reviewed) if state is not None else False,
        media_file_id=media_file_id,
        bucket_override=bucket_override,
    )
    matrix_row = {
        "episode_id": episode.id,
        "season_number": episode.season_number,
        "episode_number": episode.episode_number,
        "code": f"S{episode.season_number:02d}E{episode.episode_number:02d}",
        "title": episode.title,
        "bucket": item.bucket,
        "status": item.status,
        "confidence": item.confidence,
        "aspect_label": aspect_label,
        "recommended_crop_top": item.recommended_crop_top,
        "recommended_crop_bottom": item.recommended_crop_bottom,
        "applied_crop_top": item.applied_crop_top,
        "applied_crop_bottom": item.applied_crop_bottom,
        "resolution": _resolution_label(episode.video_width, episode.video_height),
        "source_width": episode.video_width,
        "source_height": episode.video_height,
        "media_file_id": media_file_id,
        "resolved_by": state.resolved_by if state is not None else None,
        "eligible": item.eligible,
        "reviewed": item.reviewed,
    }
    return item, matrix_row


async def _load_tv_episode_rows(
    db: AsyncSession,
    *,
    series_id: int | None = None,
) -> list[tuple[Episode, Series, LetterboxState | None, int | None]]:
    media_subq = (
        select(
            EpisodeMediaFile.episode_id.label("episode_id"),
            func.min(MediaFile.id).label("media_file_id"),
        )
        .join(MediaFile, MediaFile.id == EpisodeMediaFile.media_file_id)
        .where(MediaFile.is_active.is_(True))
        .group_by(EpisodeMediaFile.episode_id)
        .subquery()
    )
    query = (
        select(Episode, Series, LetterboxState, media_subq.c.media_file_id)
        .join(Series, Series.id == Episode.series_id)
        .outerjoin(
            LetterboxState,
            (LetterboxState.media_type == "episode") & (LetterboxState.episode_id == Episode.id),
        )
        .outerjoin(media_subq, media_subq.c.episode_id == Episode.id)
        .where(
            series_visible(),
            Episode.episode_file_path.is_not(None),
            Episode.episode_file_path != "",
        )
        .order_by(Series.title, Episode.season_number, Episode.episode_number, Episode.id)
    )
    if series_id is not None:
        query = query.where(Series.id == series_id)
    return (await db.execute(query)).all()


async def _active_tv_job_ids(db: AsyncSession, series_id: int) -> list[str]:
    rows = (
        await db.execute(
            select(Job.id)
            .where(
                Job.subject_type == "series",
                Job.subject_id == str(series_id),
                Job.status.notin_(tuple(TERMINAL)),
            )
            .order_by(Job.created_at.desc(), Job.id.desc())
        )
    ).scalars().all()
    return rows


# ---------------------------------------------------------------------------
# Status + listing
# ---------------------------------------------------------------------------


@router.get("/status")
async def letterbox_status(db: Annotated[AsyncSession, Depends(get_db)]):
    child_job = aliased(Job)
    rows = (
        await db.execute(
            select(LetterboxState.status, func.count())
            .where(LetterboxState.media_type == "movie")
            .group_by(LetterboxState.status)
        )
    ).all()
    counts = dict(rows)
    last_scan = (
        await db.execute(
            select(func.max(LetterboxState.last_detected_at)).where(
                LetterboxState.media_type == "movie"
            )
        )
    ).scalar_one_or_none()
    full_frame = (
        await db.execute(
            select(func.count())
            .select_from(LetterboxState)
            .join(Movie, Movie.id == LetterboxState.movie_id)
            .where(
                LetterboxState.media_type == "movie",
                LetterboxState.status == "prefilter_skipped",
                Movie.movie_file_path.is_not(None),
                LetterboxState.prefilter_reason != "missing_movie_file_path",
            )
        )
    ).scalar_one()
    # Re-probe binaries so a tool installed after server start (the resolve()
    # cache is per-process) shows up on the next status poll without a restart.
    binaries.reset_cache()
    # The durable batch-detect job is the source of truth for "is a scan running?"
    # (only parents in an active lifecycle state count). The frontend uses this
    # id to re-attach its progress bar after a refresh.
    batch_active = (
        await db.execute(
            select(Job.id)
            .where(
                Job.type == "letterbox_detect_batch",
                Job.status.in_(tuple(ACTIVE)),
                exists(
                    select(1).where(
                        child_job.parent_id == Job.id,
                        child_job.status.notin_(tuple(TERMINAL)),
                    )
                ),
            )
            .order_by(Job.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    return {
        "enabled": settings.LETTERBOX_ENABLED,
        "method": settings.LETTERBOX_DETECT_METHOD,
        "counts": counts,
        "full_frame": full_frame,
        "binaries": binaries.availability(),
        "honored_by": _HONORED_BY,
        "not_honored_by": _NOT_HONORED_BY,
        "last_scan": last_scan.isoformat() if last_scan else None,
        "batch_active": batch_active,
    }


@router.get("/candidates")
async def list_candidates(
    db: Annotated[AsyncSession, Depends(get_db)],
    status: str | None = None,
    confidence: str | None = None,
    reviewed: bool | None = None,
    sort: str = "confidence",
    desc: bool = Query(True, description="confidence sort direction: True = high→low"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
):
    """Paginated letterbox state, joined to movie identity. Drives all 3 tabs."""
    query = select(LetterboxState, Movie).join(Movie, Movie.id == LetterboxState.movie_id)
    query = query.where(
        LetterboxState.media_type == "movie",
        Movie.movie_file_path.is_not(None),
    )
    if status:
        query = query.where(LetterboxState.status.in_(status.split(",")))
    if confidence:
        query = query.where(LetterboxState.confidence == confidence)
    if reviewed is not None:
        query = query.where(LetterboxState.reviewed.is_(reviewed))

    total = (await db.execute(select(func.count()).select_from(query.subquery()))).scalar_one()

    # Sort: confidence (rank-ordered high→low or low→high), title, or crop size.
    if sort == "title":
        query = query.order_by(title_sort_expr())
    elif sort == "crop":
        query = query.order_by(LetterboxState.recommended_crop_top.desc().nullslast())
    elif sort == "recent":
        query = query.order_by(LetterboxState.updated_at.desc().nullslast(), title_sort_expr())
    else:  # confidence
        rank = case(
            (LetterboxState.confidence == "high", 0),
            (LetterboxState.confidence == "medium", 1),
            (LetterboxState.confidence == "variable", 2),
            (LetterboxState.confidence == "low", 3),
            (LetterboxState.confidence == "none", 4),
            else_=5,
        )
        order = rank if desc else rank.desc()
        query = query.order_by(order, title_sort_expr())

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
                .outerjoin(
                    LetterboxState,
                    (LetterboxState.movie_id == Movie.id) & (LetterboxState.media_type == "movie"),
                )
                .order_by(title_sort_expr(), Movie.year)
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
            await refresh_letterbox_prefilter_for_movie(db, movie, now=now)
            continue

        already_analyzed = _state_has_detector_truth(state)
        state = await refresh_letterbox_prefilter_for_movie(db, movie, now=now)
        item = _prefilter_movie_to_dict(movie, state)
        category = item["prefilter"]["category"]

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


@router.get("/summary")
async def letterbox_summary(db: Annotated[AsyncSession, Depends(get_db)]):
    movie_rows = (
        await db.execute(
            select(Movie, LetterboxState)
            .outerjoin(
                LetterboxState,
                (LetterboxState.media_type == "movie") & (LetterboxState.movie_id == Movie.id),
            )
            .where(Movie.movie_file_path.is_not(None), Movie.movie_file_path != "")
            .order_by(title_sort_expr(), Movie.year)
        )
    ).all()
    movie_states = [state for _movie, state in movie_rows]
    movie_aspects: dict[str, int] = {}
    for state in movie_states:
        if state is not None and state.aspect_label:
            movie_aspects[state.aspect_label] = movie_aspects.get(state.aspect_label, 0) + 1

    movie_breakdown = _aggregate_verdict_breakdown(movie_states)
    movie_artifacts = (
        await db.execute(
            select(LetterboxReencodeArtifact).where(LetterboxReencodeArtifact.media_type == "movie")
        )
    ).scalars().all()
    movie_reencode = {
        "count": len(movie_artifacts),
        "space_reclaimed_bytes": sum(
            max(0, (artifact.original_size_bytes or 0) - (artifact.candidate_size_bytes or 0))
            for artifact in movie_artifacts
            if artifact.status == "replaced"
        ),
        "awaiting_decision": sum(
            1 for artifact in movie_artifacts if artifact.status in {"candidate_ready", "kept"}
        ),
        "saved_originals_on_disk": sum(
            1 for artifact in movie_artifacts if artifact.saved_original_path
        ),
    }

    tv_rows = await _load_tv_episode_rows(db)
    tv_items: list[EpisodeLetterbox] = []
    series_rollups: list[dict] = []
    grouped_by_series: dict[int, list[EpisodeLetterbox]] = {}
    for episode, _series, state, media_file_id in tv_rows:
        item, _matrix = _episode_letterbox_row(episode, state, media_file_id)
        grouped_by_series.setdefault(episode.series_id, []).append(item)
        if episode.season_number != 0:
            tv_items.append(item)
    for items in grouped_by_series.values():
        season_rollups = {
            number: season_rollup([item for item in items if item.season_number == number])
            for number in sorted({item.season_number for item in items})
        }
        series_rollups.append(show_rollup(season_rollups))

    tv_aspects: dict[str, int] = {}
    for item in tv_items:
        if item.bucket in BAR_BEARING_BUCKETS and item.aspect_label:
            tv_aspects[item.aspect_label] = tv_aspects.get(item.aspect_label, 0) + 1

    show_verdict_counts: dict[str, int] = {}
    uniformity_counts: dict[str, int] = {}
    for rollup in series_rollups:
        show_verdict_counts[rollup["verdict"]] = show_verdict_counts.get(rollup["verdict"], 0) + 1
        uniformity_counts[rollup["uniformity"]] = uniformity_counts.get(rollup["uniformity"], 0) + 1

    return {
        "movies": {
            "workflow_funnel": _workflow_funnel_from_states(movie_states),
            "verdict_breakdown": movie_breakdown,
            "aspect_distribution": movie_aspects,
            "coverage": {
                "analyzed": len(movie_states) - movie_breakdown["unanalyzed"],
                "total": len(movie_states),
                "percent": round(
                    (100.0 * (len(movie_states) - movie_breakdown["unanalyzed"]) / len(movie_states)),
                    2,
                )
                if movie_states
                else 0.0,
            },
            "reencode": movie_reencode,
        },
        "tv": {
            "workflow_funnel": _workflow_funnel_from_status_rows(
                [(item.status, item.reviewed) for item in tv_items]
            ),
            "verdict_breakdown": {
                "clear": sum(1 for item in tv_items if item.bucket == "clear"),
                "sampled_clear": sum(1 for item in tv_items if item.bucket == "sampled_clear"),
                "letterboxed_untreated": sum(1 for item in tv_items if item.bucket == "candidate"),
                "tagged": sum(1 for item in tv_items if item.bucket == "tagged"),
                "reencoded": sum(1 for item in tv_items if item.bucket == "reencoded"),
                "variable": sum(1 for item in tv_items if item.bucket == "variable"),
                "open_matte": sum(1 for item in tv_items if item.bucket == "open_matte"),
                "pillarbox": sum(1 for item in tv_items if item.bucket == "pillarbox"),
                "ineligible": sum(1 for item in tv_items if item.bucket == "ineligible"),
                "error": sum(1 for item in tv_items if item.bucket == "error"),
                "unanalyzed": sum(1 for item in tv_items if item.bucket == "unanalyzed"),
            },
            "aspect_distribution": tv_aspects,
            "coverage": {
                "analyzed": sum(1 for item in tv_items if item.bucket != "unanalyzed"),
                "total": len(tv_items),
                "percent": round(
                    100.0 * sum(1 for item in tv_items if item.bucket != "unanalyzed") / len(tv_items),
                    2,
                )
                if tv_items
                else 0.0,
            },
            "shows_total": len(grouped_by_series),
            "episodes_total": len(tv_items),
            "show_verdict_counts": show_verdict_counts,
            "uniformity_counts": uniformity_counts,
        },
    }


@router.get("/tv")
async def list_tv_letterbox(
    db: Annotated[AsyncSession, Depends(get_db)],
    verdict: str | None = None,
    uniformity: str | None = None,
    has_candidates: bool | None = None,
    q: str | None = None,
):
    rows = await _load_tv_episode_rows(db)
    series_map: dict[int, Series] = {}
    episodes_by_series: dict[int, list[EpisodeLetterbox]] = {}
    for episode, series, state, media_file_id in rows:
        series_map[series.id] = series
        item, _matrix = _episode_letterbox_row(episode, state, media_file_id)
        episodes_by_series.setdefault(series.id, []).append(item)

    items: list[dict] = []
    for series_id, episode_items in episodes_by_series.items():
        series = series_map[series_id]
        season_rollups = {
            number: season_rollup([item for item in episode_items if item.season_number == number])
            for number in sorted({item.season_number for item in episode_items})
        }
        rollup = show_rollup(season_rollups)
        item = {
            "series_id": series.id,
            "title": series.title,
            "year": series.year,
            "episodes_total": rollup["episodes_total"],
            "dominant_aspect_label": rollup["dominant_aspect_label"],
            "rollup": rollup,
            "active_job_ids": await _active_tv_job_ids(db, series.id),
        }
        if verdict and rollup["verdict"] != verdict:
            continue
        if uniformity and rollup["uniformity"] != uniformity:
            continue
        if has_candidates is not None and rollup["has_candidates"] is not has_candidates:
            continue
        if q and q.lower() not in (series.title or "").lower():
            continue
        items.append(item)

    items.sort(key=lambda item: ((item["title"] or "").lower(), item["series_id"]))
    return {"total": len(items), "items": items}


@router.get("/tv/{series_id}")
async def tv_letterbox_detail(series_id: int, db: Annotated[AsyncSession, Depends(get_db)]):
    rows = await _load_tv_episode_rows(db, series_id=series_id)
    if not rows:
        raise HTTPException(status_code=404, detail=f"Series id={series_id} not found")

    series = rows[0][1]
    season_payloads: list[dict] = []
    episode_items: list[EpisodeLetterbox] = []
    matrix_by_season: dict[int, list[dict]] = {}
    items_by_season: dict[int, list[EpisodeLetterbox]] = {}
    for episode, _series, state, media_file_id in rows:
        item, matrix = _episode_letterbox_row(episode, state, media_file_id)
        episode_items.append(item)
        items_by_season.setdefault(episode.season_number, []).append(item)
        matrix_by_season.setdefault(episode.season_number, []).append(matrix)

    season_rollups = {
        number: season_rollup(items_by_season[number]) for number in sorted(items_by_season)
    }
    for season_number in sorted(items_by_season):
        season_payloads.append(
            {
                "season_number": season_number,
                "is_specials": season_number == 0,
                "rollup": season_rollups[season_number],
                "episodes": sorted(
                    matrix_by_season[season_number],
                    key=lambda row: (row["episode_number"], row["episode_id"]),
                ),
            }
        )

    return {
        "series": {
            "id": series.id,
            "title": series.title,
            "year": series.year,
        },
        "rollup": show_rollup(season_rollups),
        "seasons": season_payloads,
        "active_job_ids": await _active_tv_job_ids(db, series.id),
    }


@router.get("/tv/{series_id}/episodes/{episode_id}")
async def tv_episode_detail(
    series_id: int, episode_id: int, db: Annotated[AsyncSession, Depends(get_db)]
):
    rows = await _tv_scope_rows(db, series_id, episode_id=episode_id)
    if not rows:
        raise HTTPException(status_code=404, detail=f"Episode id={episode_id} not found")
    episode, series, state, _media_file_id = rows[0]
    if state is None:
        raise HTTPException(
            status_code=404,
            detail=f"No letterbox detection for episode {episode_id} — run detect first.",
        )
    samples = json.loads(state.samples_json) if state.samples_json else []
    # Preview at the first successfully-measured sample (or minute 5) — a
    # season-sample-cleared episode never ran its own detection, so it has no
    # samples at all and always falls back to minute 5.
    preview_minute = next((s["minute"] for s in samples if s.get("ok")), 5)
    detail = _state_to_dict(state)
    detail.pop("movie_id", None)
    detail["aspect_label"] = _episode_display_aspect_label(state)
    detail["episode_id"] = episode.id
    detail["series_id"] = series_id
    detail["season_number"] = episode.season_number
    detail["episode_number"] = episode.episode_number
    detail["title"] = episode.title
    detail["series_title"] = series.title
    detail["samples"] = samples
    detail["preview_minute"] = preview_minute
    detail["sample_previews"] = _sample_preview_entries(
        samples,
        url_for=lambda minute: (
            f"/api/letterbox/tv/{series_id}/episodes/{episode_id}/preview"
            f"?mode=before&minute={minute}"
        ),
    )
    if not _preview_blocked(state):
        detail["preview_urls"] = {
            "before": (
                f"/api/letterbox/tv/{series_id}/episodes/{episode_id}/preview"
                f"?mode=before&minute={preview_minute}"
            ),
            "after": (
                f"/api/letterbox/tv/{series_id}/episodes/{episode_id}/preview"
                f"?mode=after&minute={preview_minute}"
            ),
        }
    return detail


@router.get("/movies/{movie_id}")
async def get_movie_detail(movie_id: int, db: Annotated[AsyncSession, Depends(get_db)]):
    movie = await _load_movie(db, movie_id)
    state = await _load_state(db, movie_id)
    samples = json.loads(state.samples_json) if state.samples_json else []
    # Preview at the first successfully-measured sample (or minute 5).
    preview_minute = next((s["minute"] for s in samples if s.get("ok")), 5)
    detail = _state_to_dict(state, movie)
    detail["samples"] = samples
    detail["honored_by"] = _HONORED_BY
    detail["not_honored_by"] = _NOT_HONORED_BY
    detail["dolby_vision"] = _dolby_vision_summary(movie)
    reencode_snapshot = await _latest_reencode_snapshot(db, movie)
    if reencode_snapshot is not None:
        detail["reencode"] = reencode_snapshot
    detect_job = await _active_detect_job(db, movie.id)
    if detect_job is not None:
        detail["detection_job"] = job_summary(detect_job)
    detail["preview_minute"] = preview_minute
    detail["sample_previews"] = _sample_preview_entries(
        samples,
        url_for=lambda minute: f"/api/letterbox/movies/{movie_id}/preview?mode=before&minute={minute}",
    )
    if not _preview_blocked(state):
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


class TvDetectRequest(BaseModel):
    season_number: int | None = None
    episode_id: int | None = None
    exhaustive: bool = False
    force: bool = False
    include_open_matte: bool = False


class TvLibraryDetectRequest(BaseModel):
    exhaustive: bool = False
    force: bool = False


async def _resolve_batch_movie_ids(body: BatchDetectRequest, db: AsyncSession) -> list[int]:
    if body.movie_ids:
        return body.movie_ids
    if body.all_candidates:
        rows = (
            await db.execute(
                select(Movie, LetterboxState)
                .outerjoin(
                    LetterboxState,
                    (LetterboxState.movie_id == Movie.id) & (LetterboxState.media_type == "movie"),
                )
                .where(Movie.movie_file_path.is_not(None))
            )
        ).all()
        movie_ids = [
            movie.id for movie, state in rows if _should_enqueue_for_detection(movie, state)
        ]
        return movie_ids
    raise HTTPException(status_code=400, detail="Provide movie_ids or set all_candidates=true")


async def _start_detect_job(
    body: BatchDetectRequest,
    db: AsyncSession,
    *,
    detector: str,
) -> dict:
    movie_ids = await _resolve_batch_movie_ids(body, db)
    if not movie_ids:
        raise HTTPException(status_code=400, detail="No matching candidate movies")

    children: list[dict] = []
    for movie_id in movie_ids:
        movie = await db.get(Movie, movie_id)
        file_lock = await _file_lock(db, movie) if movie is not None else {}
        children.append(
            {
                "job_type": "letterbox_detect",
                "payload": {"movie_id": movie_id, "detector": detector},
                "priority": 60,
                "resources": {"media_read": 1, **file_lock},
                "subject_type": "movie",
                "subject_id": movie_id,
            }
        )
    batch, _children = await job_manager.create_batch(
        db,
        parent_type="letterbox_detect_batch",
        parent_payload={"detector": detector, "movie_ids": movie_ids},
        parent_priority=60,
        parent_subject_type="letterbox_batch",
        parent_subject_id=uuid4().hex,
        children=children,
    )
    return {
        "job_id": batch.id,
        "detector": detector,
        "total": len(movie_ids),
        "events_url": f"/api/jobs/{batch.id}/events",
    }


async def _active_detect_job(db: AsyncSession, movie_id: int) -> Job | None:
    return (
        await db.execute(
            select(Job)
            .where(
                Job.type == "letterbox_detect",
                Job.subject_type == "movie",
                Job.subject_id == str(movie_id),
                Job.status.notin_(tuple(TERMINAL)),
            )
            .order_by(Job.created_at.desc(), Job.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def _tv_scope_rows(
    db: AsyncSession,
    series_id: int,
    *,
    season_number: int | None = None,
    episode_id: int | None = None,
) -> list[tuple[Episode, Series, LetterboxState | None, int | None]]:
    rows = await _load_tv_episode_rows(db, series_id=series_id)
    if episode_id is not None:
        rows = [row for row in rows if row[0].id == episode_id]
    elif season_number is not None:
        rows = [row for row in rows if row[0].season_number == season_number]
    return rows


def _tv_scope_child(
    *,
    series_id: int,
    exhaustive: bool,
    force: bool,
    include_open_matte: bool = False,
    season_number: int | None = None,
    episode_id: int | None = None,
) -> dict:
    return {
        "job_type": "letterbox_detect_tv_scope",
        "payload": {
            "series_id": series_id,
            "season_number": season_number,
            "episode_id": episode_id,
            "exhaustive": exhaustive,
            "force": force,
            "include_open_matte": include_open_matte,
        },
        "priority": 60,
        "resources": {"media_read": 1},
        "subject_type": "series",
        "subject_id": series_id,
    }


@router.post("/movies/{movie_id}/detect")
async def detect_one(
    movie_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    limiter: Annotated[RateLimiter, Depends(get_rate_limiter)],
    thorough: bool = Query(False),
):
    """Enqueue a durable single-movie detect job; returns its job summary."""
    _require_ffmpeg()
    movie = await _load_movie(db, movie_id)
    active = await _active_detect_job(db, movie.id)
    if active is not None:
        return job_summary(active)
    enforce_rate_limit(limiter, f"lb_detect:{movie_id}", settings.RATE_LETTERBOX_DETECT_SECONDS)
    limiter.record(f"lb_detect:{movie_id}")
    job = await job_manager.create(
        db,
        job_type="letterbox_detect",
        payload={"movie_id": movie.id, "detector": "v2", "thorough": thorough},
        priority=80,
        resources={"media_read": 1, **(await _file_lock(db, movie))},
        subject_type="movie",
        subject_id=movie.id,
    )
    return job_summary(job)


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


@router.post("/tv/detect", status_code=202)
async def detect_tv_batch(
    body: TvLibraryDetectRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    limiter: Annotated[RateLimiter, Depends(get_rate_limiter)],
):
    _require_ffmpeg()
    enforce_rate_limit(limiter, "lb_detect_batch_tv", settings.RATE_LETTERBOX_BATCH_SECONDS)
    series_rows = sorted(
        {series.id: series for _episode, series, _state, _media_file_id in await _load_tv_episode_rows(db)}.values(),
        key=lambda series: ((series.title or "").lower(), series.id),
    )
    if not series_rows:
        raise HTTPException(status_code=400, detail="No downloaded TV series to analyze")

    children = [
        _tv_scope_child(
            series_id=series.id,
            exhaustive=body.exhaustive,
            force=body.force,
            include_open_matte=False,
        )
        for series in series_rows
    ]
    batch, _children = await job_manager.create_batch(
        db,
        parent_type="letterbox_detect_tv_batch",
        parent_payload={
            "series_ids": [series.id for series in series_rows],
            "exhaustive": body.exhaustive,
            "force": body.force,
        },
        parent_priority=60,
        parent_subject_type="letterbox_tv_batch",
        parent_subject_id=uuid4().hex,
        children=children,
    )
    limiter.record("lb_detect_batch_tv")
    return {
        "job_id": batch.id,
        "total": len(children),
        "events_url": f"/api/jobs/{batch.id}/events",
    }


@router.post("/tv/{series_id}/detect", status_code=202)
async def detect_tv_series(
    series_id: int,
    body: TvDetectRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    _require_ffmpeg()
    rows = await _tv_scope_rows(
        db,
        series_id,
        season_number=body.season_number,
        episode_id=body.episode_id,
    )
    if not rows:
        raise HTTPException(status_code=404, detail=f"Series id={series_id} not found")

    if body.episode_id is not None:
        children = [
            _tv_scope_child(
                series_id=series_id,
                exhaustive=True,
                force=body.force,
                include_open_matte=body.include_open_matte,
                episode_id=body.episode_id,
            )
        ]
    elif body.season_number is not None:
        children = [
            _tv_scope_child(
                series_id=series_id,
                exhaustive=body.exhaustive,
                force=body.force,
                include_open_matte=body.include_open_matte,
                season_number=body.season_number,
            )
        ]
    else:
        season_numbers = sorted({episode.season_number for episode, *_rest in rows})
        children = [
            _tv_scope_child(
                series_id=series_id,
                exhaustive=body.exhaustive,
                force=body.force,
                include_open_matte=False,
                season_number=season_number,
            )
            for season_number in season_numbers
        ]

    batch, _children = await job_manager.create_batch(
        db,
        parent_type="letterbox_detect_tv_batch",
        parent_payload={
            "series_id": series_id,
            "season_number": body.season_number,
            "episode_id": body.episode_id,
            "exhaustive": body.exhaustive,
            "force": body.force,
        },
        parent_priority=60,
        parent_subject_type="letterbox_tv_batch",
        parent_subject_id=str(series_id),
        children=children,
    )
    return {
        "job_id": batch.id,
        "total": len(children),
        "events_url": f"/api/jobs/{batch.id}/events",
    }


@router.get("/jobs/{job_id}/events")
async def job_events(job_id: str):
    """Compatibility redirect to the durable generic job event stream."""
    return RedirectResponse(url=f"/api/jobs/{job_id}/events", status_code=307)


# ---------------------------------------------------------------------------
# Preview frames
# ---------------------------------------------------------------------------


@router.get("/movies/{movie_id}/preview")
async def movie_preview(
    movie_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    mode: str = "before",
    minute: int = 5,
    exact: bool = False,
):
    movie = await _load_movie(db, movie_id)
    state = await _load_state(db, movie_id)
    if _preview_blocked(state):
        raise HTTPException(status_code=404, detail="Preview unavailable after confirmation")
    _require_ffmpeg()
    # check_eligibility shells out to mkvmerge — offload off the event loop.
    eligibility = await asyncio.to_thread(letterbox_service.check_eligibility, movie)
    if eligibility.path is None:
        raise HTTPException(status_code=404, detail="Media file unavailable for preview")

    samples = json.loads(state.samples_json) if state.samples_json else []
    candidate_minutes = [s["minute"] for s in samples if s.get("ok")]

    # Bound concurrent ffmpeg work so the before/after pair (and any other
    # in-flight previews/detects) don't dogpile the disk; the gate offloads
    # off the event loop too.
    out = await gated(
        letterbox_preview.generate_preview,
        eligibility.path,
        subject_key=letterbox_preview.movie_subject_key(movie_id),
        minute=minute,
        mode=mode,
        crop_top=state.recommended_crop_top or 0,
        crop_bottom=state.recommended_crop_bottom or 0,
        height=state.source_height,
        candidate_minutes=candidate_minutes,
        exact=exact,
    )
    if out is None:
        raise HTTPException(status_code=404, detail="Could not generate preview")
    # Confine served files to the preview cache tree.
    resolved = Path(out).resolve()
    if not str(resolved).startswith(str(settings.letterbox_preview_path.resolve())):
        raise HTTPException(status_code=403, detail="Preview path outside cache tree")
    return FileResponse(resolved, media_type="image/webp")


@router.get("/tv/{series_id}/episodes/{episode_id}/preview")
async def tv_episode_preview(
    series_id: int,
    episode_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    mode: str = "before",
    minute: int = 5,
    exact: bool = False,
):
    rows = await _tv_scope_rows(db, series_id, episode_id=episode_id)
    if not rows:
        raise HTTPException(status_code=404, detail=f"Episode id={episode_id} not found")
    _episode, _series, state, media_file_id = rows[0]
    if state is None:
        raise HTTPException(
            status_code=404,
            detail=f"No letterbox detection for episode {episode_id} — run detect first.",
        )
    if _preview_blocked(state):
        raise HTTPException(status_code=404, detail="Preview unavailable after confirmation")
    if media_file_id is None:
        raise HTTPException(status_code=404, detail="Media file unavailable for preview")
    _require_ffmpeg()
    try:
        resolved_media = await resolve_media_file(db, media_file_id)
    except (MediaFileNotFoundError, MediaFileUnavailableError) as exc:
        raise HTTPException(
            status_code=404, detail="Media file unavailable for preview"
        ) from exc

    samples = json.loads(state.samples_json) if state.samples_json else []
    candidate_minutes = [s["minute"] for s in samples if s.get("ok")]

    out = await gated(
        letterbox_preview.generate_preview,
        resolved_media.path,
        subject_key=letterbox_preview.episode_subject_key(episode_id),
        minute=minute,
        mode=mode,
        crop_top=state.recommended_crop_top or 0,
        crop_bottom=state.recommended_crop_bottom or 0,
        height=state.source_height,
        candidate_minutes=candidate_minutes,
        exact=exact,
    )
    if out is None:
        raise HTTPException(status_code=404, detail="Could not generate preview")
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


class TvApplyRequest(BaseModel):
    season_number: int | None = None
    episode_id: int | None = None


class BatchApplyRequest(BaseModel):
    movie_ids: list[int]
    only_high: bool = False


class ReencodePlanRequest(BaseModel):
    top: int | None = None
    bottom: int | None = None
    allow_cpu_fallback: bool | None = None
    encoder: str | None = None
    quality: int | None = None
    preset: str | None = None
    codec: str | None = None


class BatchReencodeSettings(BaseModel):
    quality_profile: str | None = None
    encoder: str | None = None
    quality: int | None = None
    preset: str | None = None
    codec: str | None = None
    allow_cpu: bool | None = None
    crop_top_override: int | None = None
    crop_bottom_override: int | None = None


class BatchReencodeRequest(BaseModel):
    movie_ids: list[int]
    settings: BatchReencodeSettings


class RestoreReencodeRequest(BaseModel):
    keep_candidate: bool = False


def _scope_episode_group(
    rows: list[tuple[Episode, Series, LetterboxState | None, int | None]],
    episode_id: int,
) -> list[tuple[Episode, Series, LetterboxState | None, int | None]]:
    target_row = next((row for row in rows if row[0].id == episode_id), None)
    if target_row is None:
        raise HTTPException(status_code=404, detail=f"Episode id={episode_id} not found")
    media_file_id = target_row[3]
    if media_file_id is None:
        return [target_row]
    return [row for row in rows if row[3] == media_file_id]


@router.post("/tv/{series_id}/apply")
async def apply_tv_scope(
    series_id: int,
    body: TvApplyRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    rows = await _tv_scope_rows(
        db,
        series_id,
        season_number=body.season_number if body.episode_id is None else None,
    )
    if not rows:
        raise HTTPException(status_code=404, detail=f"Series id={series_id} not found")
    if body.episode_id is not None:
        rows = _scope_episode_group(rows, body.episode_id)

    groups: dict[int, tuple[list[tuple[Episode, Series, LetterboxState | None, int | None]], LetterboxState]] = {}
    for row in rows:
        episode, _series, state, media_file_id = row
        if state is None or state.status != "candidate":
            continue
        if not (state.recommended_crop_top or state.recommended_crop_bottom):
            continue
        group_key = media_file_id if media_file_id is not None else -(episode.id or 0)
        groups.setdefault(group_key, (_scope_episode_group(rows, episode.id), state))
    if not groups:
        raise HTTPException(status_code=400, detail="No eligible TV episodes with a crop to apply")

    results: list[dict] = []
    for group_rows, state in groups.values():
        result = await letterbox_service.apply_episode_group(
            db,
            [episode for episode, *_rest in group_rows],
            top=state.recommended_crop_top or 0,
            bottom=state.recommended_crop_bottom or 0,
        )
        results.append(
            {
                "episode_ids": [episode.id for episode, *_rest in group_rows],
                "top": result.top,
                "bottom": result.bottom,
                "path": result.path,
                "verified": result.verified,
            }
        )
    return {
        "applied_groups": len(results),
        "applied_episodes": sum(len(item["episode_ids"]) for item in results),
        "items": results,
    }


@router.post("/movies/{movie_id}/apply")
async def apply_one(
    movie_id: int,
    body: ApplyRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    movie = await _load_movie(db, movie_id)
    state = await _load_state(db, movie_id)
    eligibility = letterbox_service.check_eligibility(movie)
    if not eligibility.eligible:
        raise HTTPException(status_code=422, detail=eligibility.reason or "ineligible")
    if state.status == "variable_unsafe":
        raise HTTPException(
            status_code=422,
            detail="Variable aspect ratio (contains 16:9 scenes) — unsafe to crop.",
        )
    top = body.top if body.top is not None else state.recommended_crop_top
    bottom = body.bottom if body.bottom is not None else state.recommended_crop_bottom
    if not top and not bottom:
        raise HTTPException(status_code=422, detail="No crop to apply (recommendation is 0).")

    job = await job_manager.create_and_run(
        db,
        job_type="letterbox_apply",
        payload={"movie_id": movie.id, "top": top or 0, "bottom": bottom or 0},
        priority=80,
        subject_type="movie",
        subject_id=movie.id,
        max_attempts=1,
        worker_id="inline-api",
    )
    return {**job_summary(job), **(job.result or {})}


@router.post("/apply")
async def apply_batch(body: BatchApplyRequest, db: Annotated[AsyncSession, Depends(get_db)]):
    """Queue one durable apply child per eligible movie."""
    batch = await job_manager.create(
        db,
        job_type="letterbox_apply_batch",
        payload={"movie_ids": body.movie_ids, "only_high": body.only_high},
        priority=70,
        subject_type="letterbox_batch",
        subject_id=uuid4().hex,
        status="waiting_external",
    )
    skipped = []
    queued = 0
    for movie_id in body.movie_ids:
        movie = (await db.execute(select(Movie).where(Movie.id == movie_id))).scalar_one_or_none()
        state = (
            await db.execute(
                select(LetterboxState).where(
                    LetterboxState.media_type == "movie",
                    LetterboxState.movie_id == movie_id,
                )
            )
        ).scalar_one_or_none()
        if movie is None or state is None:
            skipped.append({"movie_id": movie_id, "reason": "not_found"})
            continue
        if state.status == "variable_unsafe" or not state.recommended_crop_top:
            skipped.append({"movie_id": movie_id, "reason": "not_applicable"})
            continue
        if body.only_high and state.confidence != "high":
            skipped.append({"movie_id": movie_id, "reason": "not_high_confidence"})
            continue
        await job_manager.create(
            db,
            job_type="letterbox_apply",
            payload={
                "movie_id": movie.id,
                "top": state.recommended_crop_top or 0,
                "bottom": state.recommended_crop_bottom or 0,
            },
            priority=70,
            resources={"media_write": 1, **(await _file_lock(db, movie))},
            parent_id=batch.id,
            correlation_id=batch.correlation_id,
            subject_type="movie",
            subject_id=movie.id,
            max_attempts=1,
        )
        queued += 1
    if not queued:
        batch.status = "succeeded"
        batch.finished_at = datetime.now(UTC)
        batch.progress = {"children_total": 0, "children_completed": 0, "children_failed": 0}
        await db.commit()
    result = job_summary(batch)
    result.update({"queued": queued, "skipped": skipped})
    return result


@router.post("/movies/{movie_id}/confirm")
async def confirm_one(movie_id: int, db: Annotated[AsyncSession, Depends(get_db)]):
    """Mark a tagged movie reviewed so it moves to Processed."""
    await _load_movie(db, movie_id)
    state = await _load_state(db, movie_id)
    if state.status != "tagged" and not state.reviewed:
        raise HTTPException(status_code=422, detail="Can only confirm a tagged movie.")
    state.reviewed = True
    response = _state_to_dict(state)
    db.add(
        LetterboxEvent(
            media_type="movie",
            movie_id=movie_id,
            action="confirm",
            source="api",
            detail="{}",
        )
    )
    await db.commit()
    letterbox_preview.purge_previews(letterbox_preview.movie_subject_key(movie_id))
    return response


def _map_reencode_error(exc: letterbox_reencode.ReencodePlanError) -> HTTPException:
    return HTTPException(
        status_code=422,
        detail={"code": exc.code, "message": str(exc), "warnings": exc.warnings},
    )


async def _load_reencode_artifact(db: AsyncSession, artifact_id: int) -> LetterboxReencodeArtifact:
    artifact = await db.get(LetterboxReencodeArtifact, artifact_id)
    if artifact is None:
        raise HTTPException(status_code=404, detail=f"Re-encode artifact {artifact_id} not found")
    return artifact


async def _create_reencode_plan_job(
    db: AsyncSession,
    movie_id: int,
    body: ReencodePlanRequest,
) -> tuple[MediaJob, dict, datetime]:
    movie = await _load_movie(db, movie_id)
    state = await _load_state(db, movie_id)
    if state.status == "variable_unsafe":
        raise HTTPException(
            status_code=422,
            detail={
                "code": "variable_unsafe",
                "message": "Variable aspect ratio is unsafe to crop permanently.",
            },
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
            encoder=body.encoder,
            quality=body.quality,
            preset=body.preset,
            codec=body.codec,
        )
    except (MediaFileNotFoundError, MediaFileUnavailableError) as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "file_unavailable", "message": str(exc)},
        ) from exc
    except letterbox_reencode.ReencodePlanError as exc:
        raise _map_reencode_error(exc) from exc

    await media_job_manager.supersede_planned_media_jobs(
        db,
        media_file_id=media_file.id,
        operation="letterbox_reencode",
    )

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
    return job, plan, expires_at


async def _confirm_media_job_plan(db: AsyncSession, job: MediaJob) -> None:
    if job.status != "planned":
        raise HTTPException(status_code=409, detail={"code": "not_planned", "status": job.status})
    plan_expires_at = _as_utc(job.plan_expires_at)
    if plan_expires_at and plan_expires_at < datetime.now(UTC):
        job.status = "failed"
        job.error_json = json.dumps({"code": "plan_stale", "error": "plan expired"})
        await db.commit()
        raise HTTPException(
            status_code=409, detail={"code": "plan_stale", "message": "plan expired"}
        )

    if job.plan_json:
        plan = json.loads(job.plan_json)
        if not plan.get("capabilities", {}).get("can_execute", True):
            blocking = [
                w["code"]
                for w in plan.get("warnings", [])
                if w.get("code") in ("container_not_writable", "mkv_track_ids_unavailable")
            ]
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "plan_not_executable",
                    "message": "plan cannot be executed",
                    "blocking_warnings": blocking,
                },
            )

    try:
        resolved = await resolve_media_file(db, job.media_file_id)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=422, detail={"code": "file_unavailable", "message": str(exc)}
        ) from exc
    if job.input_signature and resolved.signature != job.input_signature:
        raise HTTPException(
            status_code=409,
            detail={"code": "plan_stale", "message": "file changed since plan was created"},
        )

    job.status = "queued"
    job.confirmed_at = datetime.now(UTC)
    generic = await _generic_media_job_bridge(db, job)
    if generic is not None:
        generic.status = "queued"
        generic.scheduled_at = datetime.now(UTC)

    if job.operation == "letterbox_reencode" and job.media_file_id is not None:
        movie_file = await db.get(MediaFile, job.media_file_id)
        if movie_file and movie_file.movie_id is not None:
            stmt = select(LetterboxState).where(
                LetterboxState.media_type == "movie",
                LetterboxState.movie_id == movie_file.movie_id,
            )
            state = (await db.execute(stmt)).scalar_one_or_none()
            if state and state.status == "candidate":
                state.status = "tagged"
                state.reviewed = False

    await db.commit()


@router.post("/movies/{movie_id}/reencode-plan", status_code=201)
async def create_reencode_plan(
    movie_id: int,
    body: ReencodePlanRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Plan a permanent cropped re-encode. No media file is written here."""
    _require_ffmpeg()
    job, plan, expires_at = await _create_reencode_plan_job(db, movie_id, body)
    return {
        **plan,
        "job_id": job.job_id,
        "status": "planned",
        "expires_at": expires_at.isoformat(),
    }


@router.post("/batch/reencode", status_code=202)
async def batch_reencode(
    body: BatchReencodeRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    _require_ffmpeg()
    if not body.movie_ids:
        raise HTTPException(status_code=400, detail="Provide at least one movie_id")

    plan_body = ReencodePlanRequest(
        top=body.settings.crop_top_override,
        bottom=body.settings.crop_bottom_override,
        allow_cpu_fallback=body.settings.allow_cpu,
        encoder=body.settings.encoder,
        quality=body.settings.quality,
        preset=body.settings.preset,
        codec=body.settings.codec,
    )
    job_ids: list[str] = []
    skipped: list[dict] = []
    for movie_id in body.movie_ids:
        try:
            job, _plan, _expires = await _create_reencode_plan_job(db, movie_id, plan_body)
            await _confirm_media_job_plan(db, job)
            job_ids.append(job.job_id)
        except HTTPException as exc:
            detail = exc.detail if isinstance(exc.detail, dict) else {"message": str(exc.detail)}
            skipped.append(
                {
                    "movie_id": movie_id,
                    "code": detail.get("code"),
                    "reason": detail.get("message") or str(exc.detail),
                }
            )
    return {"job_ids": job_ids, "count": len(job_ids), "skipped": skipped}


@router.get("/reencode-artifacts")
async def list_reencode_artifacts(
    db: Annotated[AsyncSession, Depends(get_db)],
    status: str | None = None,
    movie_id: int | None = None,
    limit: int = Query(100, ge=1, le=500),
):
    query = (
        select(LetterboxReencodeArtifact)
        .order_by(LetterboxReencodeArtifact.created_at.desc())
        .limit(limit)
    )
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


@router.post("/tv/{series_id}/episodes/{episode_id}/remove")
async def remove_tv_episode(
    series_id: int,
    episode_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    rows = await _tv_scope_rows(db, series_id)
    group_rows = _scope_episode_group(rows, episode_id)
    result = await letterbox_service.remove_episode_group(
        db,
        [episode for episode, *_rest in group_rows],
        source="api",
    )
    return {
        "removed": result.removed,
        "path": result.path,
        "episode_ids": [episode.id for episode, *_rest in group_rows],
    }


@router.post("/movies/{movie_id}/remove")
async def remove_one(movie_id: int, db: Annotated[AsyncSession, Depends(get_db)]):
    movie = await _load_movie(db, movie_id)
    await _load_state(db, movie_id)
    job = await job_manager.create_and_run(
        db,
        job_type="letterbox_remove",
        payload={"movie_id": movie.id},
        priority=80,
        subject_type="movie",
        subject_id=movie.id,
        max_attempts=1,
        worker_id="inline-api",
    )
    return {**job_summary(job), **(job.result or {})}


@router.post("/tv/{series_id}/episodes/{episode_id}/ignore")
async def ignore_tv_episode(
    series_id: int,
    episode_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    rows = await _tv_scope_rows(db, series_id)
    target_row = next((row for row in rows if row[0].id == episode_id), None)
    if target_row is None:
        raise HTTPException(status_code=404, detail=f"Episode id={episode_id} not found")
    episode, _series, state, media_file_id = target_row
    if state is None:
        raise HTTPException(status_code=404, detail="Run detect first for this episode.")
    state.status = "skipped"
    state.reviewed = True
    db.add(
        LetterboxEvent(
            media_type="episode",
            episode_id=episode_id,
            action="ignore",
            source="api",
            detail="{}",
        )
    )
    await db.commit()
    return _episode_letterbox_row(episode, state, media_file_id)[1]


@router.post("/movies/{movie_id}/ignore")
async def ignore_one(movie_id: int, db: Annotated[AsyncSession, Depends(get_db)]):
    """Mark a movie reviewed → Skipped tab; won't be re-flagged on re-scan."""
    await _load_movie(db, movie_id)
    state = await _load_state(db, movie_id)
    state.status = "skipped"
    state.reviewed = True
    response = _state_to_dict(state)
    db.add(
        LetterboxEvent(
            media_type="movie",
            movie_id=movie_id,
            action="ignore",
            source="api",
            detail="{}",
        )
    )
    await db.commit()
    letterbox_preview.purge_previews(letterbox_preview.movie_subject_key(movie_id))
    return response


@router.post("/tv/{series_id}/episodes/{episode_id}/mark-not-letterboxed")
async def mark_tv_episode_not_letterboxed(
    series_id: int,
    episode_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    rows = await _tv_scope_rows(db, series_id)
    target_row = next((row for row in rows if row[0].id == episode_id), None)
    if target_row is None:
        raise HTTPException(status_code=404, detail=f"Episode id={episode_id} not found")
    episode, _series, state, media_file_id = target_row
    if state is None or state.status != "candidate":
        raise HTTPException(
            status_code=422,
            detail="Can only mark detected candidate episodes as not letterboxed.",
        )
    state.status = "not_letterboxed"
    state.confidence = "none"
    state.reviewed = True
    state.recommended_crop_top = 0
    state.recommended_crop_bottom = 0
    state.applied_crop_top = 0
    state.applied_crop_bottom = 0
    state.aspect_label = letterbox_detect.aspect_label(
        state.source_width or episode.video_width,
        state.source_height or episode.video_height,
    )
    state.variable_ar = False
    state.variable_ar_note = None
    state.error = None
    db.add(
        LetterboxEvent(
            media_type="episode",
            episode_id=episode.id,
            action="mark_not_letterboxed",
            source="api",
            detail="{}",
        )
    )
    await db.commit()
    return _episode_letterbox_row(episode, state, media_file_id)[1]


@router.post("/movies/{movie_id}/mark-not-letterboxed")
async def mark_not_letterboxed(movie_id: int, db: Annotated[AsyncSession, Depends(get_db)]):
    """Manual review override: move a bad detected crop to Not Letterboxed."""
    await _load_movie(db, movie_id)
    state = await _load_state(db, movie_id)
    if state.status != "candidate":
        raise HTTPException(
            status_code=422,
            detail="Can only mark detected candidate movies as not letterboxed.",
        )
    state.status = "not_letterboxed"
    state.confidence = "none"
    state.reviewed = True
    state.recommended_crop_top = 0
    state.recommended_crop_bottom = 0
    state.aspect_label = None
    state.variable_ar = False
    state.variable_ar_note = None
    state.error = None
    response = _state_to_dict(state)
    db.add(
        LetterboxEvent(
            media_type="movie",
            movie_id=movie_id,
            action="mark_not_letterboxed",
            source="api",
            detail="{}",
        )
    )
    await db.commit()
    letterbox_preview.purge_previews(letterbox_preview.movie_subject_key(movie_id))
    return response


@router.post("/heal")
async def letterbox_heal(db: Annotated[AsyncSession, Depends(get_db)]):
    """Re-apply crop tags that drifted off tagged files (tag-drift scan)."""
    job = await job_manager.create(
        db, job_type="letterbox_heal", priority=20, resources={"media_write": 1}
    )
    return job_summary(job)


# ---------------------------------------------------------------------------
# Dev / debug helpers — bulk and per-movie state resets
# ---------------------------------------------------------------------------

_NOT_LB_STATUSES = {"not_letterboxed", "variable_unsafe", "skipped"}


def _wipe_detection(state: LetterboxState, _now: datetime) -> None:
    """Clear all detection and application fields; set status to prefilter_candidate."""
    state.status = "prefilter_candidate"
    state.confidence = None
    state.prefilter_bucket = None
    state.prefilter_reason = None
    state.prefilter_aspect_ratio = None
    state.recommended_crop_top = None
    state.recommended_crop_bottom = None
    state.applied_crop_top = None
    state.applied_crop_bottom = None
    state.aspect_label = None
    state.detect_method = None
    state.samples_json = None
    state.error = None
    state.reviewed = False
    state.variable_ar = False
    state.variable_ar_note = None
    state.last_detected_at = None
    state.last_applied_at = None
    state.last_prefiltered_at = None


@router.post("/dev/reset-not-letterboxed")
async def dev_reset_not_letterboxed(db: Annotated[AsyncSession, Depends(get_db)]):
    """Dev: move all not_letterboxed / variable_unsafe / skipped back to Candidates."""
    result = await db.execute(
        select(LetterboxState).where(
            LetterboxState.media_type == "movie",
            LetterboxState.status.in_(list(_NOT_LB_STATUSES)),
        )
    )
    states = result.scalars().all()
    now = datetime.now(UTC)
    for state in states:
        _wipe_detection(state, now)
    await db.commit()
    return {"reset": len(states)}


@router.post("/dev/reset-detected")
async def dev_reset_detected(db: Annotated[AsyncSession, Depends(get_db)]):
    """Dev: move all detected (candidate) movies back to Candidates."""
    result = await db.execute(
        select(LetterboxState).where(
            LetterboxState.media_type == "movie",
            LetterboxState.status == "candidate",
        )
    )
    states = result.scalars().all()
    now = datetime.now(UTC)
    for state in states:
        _wipe_detection(state, now)
    await db.commit()
    return {"reset": len(states)}


@router.post("/dev/reset-all")
async def dev_reset_all(db: Annotated[AsyncSession, Depends(get_db)]):
    """Dev: fully clear all letterbox workflow rows back to Candidates."""
    result = await db.execute(select(LetterboxState).where(LetterboxState.media_type == "movie"))
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
        select(LetterboxState).where(
            LetterboxState.media_type == "movie",
            LetterboxState.movie_id == movie_id,
        )
    )
    state = result.scalar_one_or_none()
    if state is None:
        raise HTTPException(status_code=404, detail="No letterbox state found for this movie")
    _wipe_detection(state, datetime.now(UTC))
    await db.commit()
    return {"reset": True, "movie_id": movie_id}
