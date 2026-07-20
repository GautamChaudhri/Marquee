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
from datetime import UTC, datetime
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import String, case, cast, delete, exists, func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from marquee.api.deps import enforce_rate_limit, get_rate_limiter
from marquee.api.job_submission import JobSubmissionResponse, submission_response
from marquee.config import settings
from marquee.core import letterbox_transcode
from marquee.core.jobs.batches import BatchScope, create_fixed_batch
from marquee.core.jobs.contracts import TriggerKind
from marquee.core.jobs.letterbox_reencode_documents import (
    LetterboxReencodeDiscardRequestV1,
    LetterboxReencodePublishRequestV1,
    LetterboxReencodeRequestV1,
    LetterboxReencodeRestoreRequestV1,
    ReencodeProbeV1,
)
from marquee.core.jobs.mutation_documents import MutationTargetV1
from marquee.core.jobs.mutation_planning import (
    MutationPlan,
    confirm_mutation,
    plan_mutation,
    plan_version,
)
from marquee.core.jobs.submission import (
    Initiator,
    SubjectLocator,
    SubmissionError,
    SubmissionIntent,
    submit_job,
)
from marquee.core.letterbox_eligibility import check_movie_eligibility
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
from marquee.core.letterbox_scope import normalize_confidence_levels
from marquee.core.media_files import (
    MediaFileNotFoundError,
    MediaFileUnavailableError,
    ensure_media_file_for_movie,
    resolve_media_file,
)
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
    JobArtifact,
    LetterboxEvent,
    LetterboxState,
    MediaFile,
    MediaOperationDetail,
    Movie,
    Series,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/letterbox", tags=["letterbox"])


def _letterbox_detection_config_snapshot() -> dict[str, object]:
    """Freeze all detector controls that affect a queued observation result."""
    return {
        "method": settings.LETTERBOX_DETECT_METHOD,
        "trim_fuzz": list(settings.LETTERBOX_TRIM_FUZZ),
        "movie_samples_min": settings.LETTERBOX_MOVIE_SAMPLES_MIN,
        "movie_samples_max": settings.LETTERBOX_MOVIE_SAMPLES_MAX,
        "movie_sample_step": settings.LETTERBOX_MOVIE_SAMPLE_STEP,
        "tv_quick_windows": settings.LETTERBOX_TV_QUICK_WINDOWS,
        "tv_thorough_windows": settings.LETTERBOX_TV_THOROUGH_WINDOWS,
        "tv_head_skip_pct": settings.LETTERBOX_TV_HEAD_SKIP_PCT,
        "tv_tail_skip_pct": settings.LETTERBOX_TV_TAIL_SKIP_PCT,
        "window_seconds": settings.LETTERBOX_WINDOW_SECONDS,
        "cropdetect_limit": settings.LETTERBOX_CROPDETECT_LIMIT,
        "cropdetect_hdr_limit": settings.LETTERBOX_CROPDETECT_HDR_LIMIT,
        "cropdetect_round": settings.LETTERBOX_CROPDETECT_ROUND,
        "noise_px": settings.LETTERBOX_NOISE_PX,
        "min_bar_px": settings.LETTERBOX_MIN_BAR_PX,
        "agree_px": settings.LETTERBOX_AGREE_PX,
        "medium_spread_px": settings.LETTERBOX_MEDIUM_SPREAD_PX,
        "variable_gap_px": settings.LETTERBOX_VARIABLE_GAP_PX,
        "variable_min_fraction": settings.LETTERBOX_VARIABLE_MIN_FRACTION,
        "asym_px": settings.LETTERBOX_ASYM_PX,
        "asymmetric": settings.LETTERBOX_ASYMMETRIC,
        "early_stop_windows": settings.LETTERBOX_EARLY_STOP_WINDOWS,
    }


async def _letterbox_mutation_snapshot(
    db: AsyncSession, movie: Movie, state: LetterboxState
) -> tuple[MediaFile, dict[str, object]]:
    """Seal the source and detector facts consumed by a canonical tag mutation."""
    media_file = await ensure_media_file_for_movie(db, movie)
    if media_file is None:
        raise HTTPException(status_code=409, detail="movie has no active media file")
    try:
        resolved = await resolve_media_file(db, media_file.id)
    except (MediaFileNotFoundError, MediaFileUnavailableError) as exc:
        raise HTTPException(status_code=409, detail="movie media file is unavailable") from exc
    return media_file, _letterbox_state_snapshot(state, resolved.signature)


def _letterbox_state_snapshot(state: LetterboxState, source_signature: str) -> dict[str, object]:
    if state.source_width is None or state.source_height is None:
        raise HTTPException(status_code=422, detail="letterbox source dimensions are unknown")
    return {
        "source_signature": source_signature,
        "status": state.status,
        "confidence": state.confidence,
        "variable_ar": state.variable_ar,
        "source_width": state.source_width,
        "source_height": state.source_height,
        "current_crop_top": state.applied_crop_top,
        "current_crop_bottom": state.applied_crop_bottom,
        "recommended_crop_top": state.recommended_crop_top,
        "recommended_crop_bottom": state.recommended_crop_bottom,
    }


async def _tv_mutation_intents(
    db: AsyncSession,
    rows: list[tuple[Episode, Series, LetterboxState | None, int | None]],
    *,
    operation: str,
    confidence_levels: tuple[str, ...] = (),
    initiator: Initiator,
) -> tuple[SubmissionIntent, ...]:
    """Resolve one immutable child per physical TV file."""
    grouped: dict[int, list[tuple[Episode, LetterboxState]]] = {}
    for episode, _series, state, media_file_id in rows:
        if state is None or media_file_id is None or state.variable_ar:
            continue
        if operation == "apply":
            if state.status != "candidate" or state.confidence not in confidence_levels:
                continue
            if not (state.recommended_crop_top or state.recommended_crop_bottom):
                continue
        elif state.applied_crop_top is None and state.applied_crop_bottom is None:
            continue
        grouped.setdefault(media_file_id, []).append((episode, state))

    intents: list[SubmissionIntent] = []
    for media_file_id, group in sorted(grouped.items()):
        resolved = await resolve_media_file(db, media_file_id)
        snapshots = [
            _letterbox_state_snapshot(state, resolved.signature) for _episode, state in group
        ]
        if any(snapshot != snapshots[0] for snapshot in snapshots[1:]):
            raise HTTPException(
                status_code=409,
                detail=f"episodes sharing media file {media_file_id} have inconsistent letterbox state",
            )
        request: dict[str, object] = {
            "media_file_id": media_file_id,
            "subject_kind": "episode",
            "subject_ids": [episode.id for episode, _state in group],
            "before": snapshots[0],
            "source": "api",
        }
        if operation == "apply":
            request["crop_top"] = group[0][1].recommended_crop_top or 0
            request["crop_bottom"] = group[0][1].recommended_crop_bottom or 0
        intents.append(
            SubmissionIntent(
                job_type=f"letterbox_{operation}",
                request=request,
                subject=SubjectLocator(kind="media_file", reference=str(media_file_id)),
                trigger=TriggerKind.BATCH,
                initiator=initiator,
                idempotency_key=f"letterbox_{operation}:batch-{uuid4().hex}",
                priority=70,
            )
        )
    return tuple(intents)


async def _submit_tv_mutation_parent(
    db: AsyncSession,
    *,
    series_id: int,
    season_number: int | None,
    rows: list[tuple[Episode, Series, LetterboxState | None, int | None]],
    operation: str,
    confidence_levels: tuple[str, ...] = (),
) -> JobSubmissionResponse:
    initiator = Initiator(kind="system", identifier="letterbox-api")
    children = await _tv_mutation_intents(
        db,
        rows,
        operation=operation,
        confidence_levels=confidence_levels,
        initiator=initiator,
    )
    if not children:
        raise HTTPException(
            status_code=400, detail="No eligible TV media files in the sealed scope"
        )
    if db.in_transaction():
        await db.commit()
    parent_type = (
        "letterbox_apply_tv_scope" if operation == "apply" else "letterbox_revert_tv_scope"
    )
    scope_key = uuid4().hex[:12]
    try:
        async with db.begin():
            result = await create_fixed_batch(
                db,
                parent_job_type=(
                    "letterbox_apply_tv_scope"
                    if operation == "apply"
                    else "letterbox_revert_tv_scope"
                ),
                parent_request={
                    "operation": operation,
                    "series_id": series_id,
                    "season_number": season_number,
                    "confidence_levels": list(confidence_levels),
                    "sealed_file_count": len(children),
                },
                scope=BatchScope(
                    reference=f"series-{series_id}-{operation}-{scope_key}",
                    display_name=f"Letterbox {operation}: series {series_id}",
                    summary=f"{len(children)} sealed physical media files",
                ),
                trigger=TriggerKind.MANUAL,
                initiator=initiator,
                idempotency_key=f"{parent_type}:manual-{uuid4().hex}",
                children=children,
                priority=70,
            )
    except SubmissionError as exc:
        raise HTTPException(status_code=422, detail=exc.code) from exc
    return submission_response(result.parent)


async def _letterbox_heal_intents(
    db: AsyncSession, *, initiator: Initiator
) -> tuple[SubmissionIntent, ...]:
    """Seal tagged movie/episode files; leaf probes decide intact versus drifted."""
    intents: list[SubmissionIntent] = []
    movie_rows = (
        await db.execute(
            select(Movie, LetterboxState)
            .join(LetterboxState, LetterboxState.movie_id == Movie.id)
            .where(
                LetterboxState.media_type == "movie",
                LetterboxState.status == "tagged",
                LetterboxState.variable_ar.is_(False),
            )
        )
    ).all()
    for movie, state in movie_rows:
        if state.applied_crop_top is None or state.applied_crop_bottom is None:
            continue
        media_file, before = await _letterbox_mutation_snapshot(db, movie, state)
        intents.append(
            SubmissionIntent(
                job_type="letterbox_apply",
                request={
                    "media_file_id": media_file.id,
                    "subject_kind": "movie",
                    "subject_ids": [movie.id],
                    "before": before,
                    "crop_top": state.applied_crop_top,
                    "crop_bottom": state.applied_crop_bottom,
                    "source": "heal",
                },
                subject=SubjectLocator(kind="media_file", reference=str(media_file.id)),
                trigger=TriggerKind.BATCH,
                initiator=initiator,
                idempotency_key=f"letterbox_apply:heal-{uuid4().hex}",
                priority=20,
            )
        )

    episode_rows = (
        await db.execute(
            select(Episode, LetterboxState, EpisodeMediaFile.media_file_id)
            .join(LetterboxState, LetterboxState.episode_id == Episode.id)
            .join(EpisodeMediaFile, EpisodeMediaFile.episode_id == Episode.id)
            .where(
                LetterboxState.media_type == "episode",
                LetterboxState.status == "tagged",
                LetterboxState.variable_ar.is_(False),
            )
        )
    ).all()
    grouped: dict[int, list[tuple[Episode, LetterboxState]]] = {}
    for episode, state, media_file_id in episode_rows:
        if state.applied_crop_top is None or state.applied_crop_bottom is None:
            continue
        grouped.setdefault(media_file_id, []).append((episode, state))
    for media_file_id, group in sorted(grouped.items()):
        resolved = await resolve_media_file(db, media_file_id)
        snapshots = [
            _letterbox_state_snapshot(state, resolved.signature) for _episode, state in group
        ]
        if any(snapshot != snapshots[0] for snapshot in snapshots[1:]):
            continue
        intents.append(
            SubmissionIntent(
                job_type="letterbox_apply",
                request={
                    "media_file_id": media_file_id,
                    "subject_kind": "episode",
                    "subject_ids": [episode.id for episode, _state in group],
                    "before": snapshots[0],
                    "crop_top": group[0][1].applied_crop_top,
                    "crop_bottom": group[0][1].applied_crop_bottom,
                    "source": "heal",
                },
                subject=SubjectLocator(kind="media_file", reference=str(media_file_id)),
                trigger=TriggerKind.BATCH,
                initiator=initiator,
                idempotency_key=f"letterbox_apply:heal-{uuid4().hex}",
                priority=20,
            )
        )
    return tuple(intents)


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


async def _latest_reencode_snapshot(db: AsyncSession, movie: Movie) -> dict | None:
    artifact = await db.scalar(
        select(JobArtifact)
        .join(Job, Job.id == JobArtifact.job_id)
        .where(
            JobArtifact.kind == "media_candidate",
            Job.type == "letterbox_reencode",
            Job.subject_kind == "movie",
            Job.subject_reference == str(movie.id),
        )
        .order_by(JobArtifact.created_at.desc(), JobArtifact.id.desc())
        .limit(1)
    )
    if artifact is None:
        return None
    return {"job": None, "artifact": _canonical_reencode_artifact(artifact)}


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
        "preview": sum(1 for status, reviewed in rows if status == "tagged" and not reviewed),
        "processed": sum(1 for status, reviewed in rows if status == "tagged" and reviewed),
    }


def _workflow_funnel_from_states(states: list[LetterboxState | None]) -> dict[str, int]:
    return _workflow_funnel_from_status_rows(
        [
            (
                state.status if state is not None else None,
                bool(state.reviewed) if state is not None else False,
            )
            for state in states
        ]
    )


def _verdict_breakdown_key(state: LetterboxState | None) -> str:
    if state is None or state.status in {
        "prefilter_candidate",
        "prefilter_unknown",
        "prefilter_skipped",
    }:
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
    bucket_override = (
        dimension_class if dimension_class and not _state_has_detector_truth(state) else None
    )
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
                Job.phase.in_(("queued", "running", "stopping")),
                exists(
                    select(1).where(
                        child_job.parent_id == Job.id,
                        child_job.phase != "terminal",
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
        (
            await db.execute(
                select(JobArtifact)
                .join(Job, Job.id == JobArtifact.job_id)
                .where(
                    JobArtifact.kind == "media_candidate",
                    Job.type == "letterbox_reencode",
                    Job.subject_kind == "movie",
                )
            )
        )
        .scalars()
        .all()
    )
    movie_reencode = {
        "count": len(movie_artifacts),
        "space_reclaimed_bytes": 0,
        "awaiting_decision": sum(artifact.status == "available" for artifact in movie_artifacts),
        "saved_originals_on_disk": 0,
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
                    (
                        100.0
                        * (len(movie_states) - movie_breakdown["unanalyzed"])
                        / len(movie_states)
                    ),
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
                "widescreen": sum(1 for item in tv_items if item.bucket == "widescreen"),
                "sampled_widescreen": sum(
                    1 for item in tv_items if item.bucket == "sampled_widescreen"
                ),
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
                    100.0
                    * sum(1 for item in tv_items if item.bucket != "unanalyzed")
                    / len(tv_items),
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
        }
        content_types = {content_type["type"] for content_type in rollup["content_types"]}
        if verdict and rollup["verdict"] != verdict and verdict not in content_types:
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


@router.post("/tv/dev/reset-all")
async def dev_reset_all_tv(db: Annotated[AsyncSession, Depends(get_db)]):
    """Dev: delete all TV letterbox workflow rows and cached episode previews."""
    event_result = await db.execute(
        delete(LetterboxEvent).where(LetterboxEvent.media_type == "episode")
    )
    state_result = await db.execute(
        delete(LetterboxState).where(LetterboxState.media_type == "episode")
    )
    await db.commit()
    previews_purged = letterbox_preview.purge_all_episode_previews()
    return {
        "states_deleted": state_result.rowcount or 0,
        "events_deleted": event_result.rowcount or 0,
        "previews_purged": previews_purged,
    }


class TvLibraryDetectRequest(BaseModel):
    exhaustive: bool = False
    force: bool = False


@router.post("/tv/detect", status_code=202)
async def detect_tv_batch(
    body: TvLibraryDetectRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    limiter: Annotated[RateLimiter, Depends(get_rate_limiter)],
) -> JobSubmissionResponse:
    enforce_rate_limit(limiter, "lb_detect_batch_tv", settings.RATE_LETTERBOX_BATCH_SECONDS)
    try:
        async with db.begin():
            series_rows = sorted(
                {
                    series.id: series
                    for _episode, series, _state, _media_file_id in await _load_tv_episode_rows(db)
                }.values(),
                key=lambda series: ((series.title or "").lower(), series.id),
            )
            if not series_rows:
                raise HTTPException(status_code=400, detail="No downloaded TV series to analyze")
            nonce = uuid4().hex
            initiator = Initiator(kind="system", identifier="letterbox-api")
            children = [
                SubmissionIntent(
                    job_type="letterbox_detect_tv_scope",
                    request={
                        "series_id": series.id,
                        "exhaustive": body.exhaustive,
                        "force": body.force,
                        "detection_config": _letterbox_detection_config_snapshot(),
                    },
                    subject=SubjectLocator(kind="series", reference=str(series.id)),
                    trigger=TriggerKind.BATCH,
                    initiator=initiator,
                    idempotency_key=f"letterbox_detect_tv_scope:batch-{nonce}-{series.id}",
                )
                for series in series_rows
            ]
            result = await create_fixed_batch(
                db,
                parent_job_type="letterbox_detect_tv_batch",
                parent_request={
                    "series_ids": [series.id for series in series_rows],
                    "exhaustive": body.exhaustive,
                    "force": body.force,
                    "detection_config": _letterbox_detection_config_snapshot(),
                },
                scope=BatchScope(reference=nonce, display_name="Letterbox detection · TV library"),
                trigger=TriggerKind.BATCH,
                initiator=initiator,
                idempotency_key=f"letterbox_detect_tv_batch:manual-{nonce}",
                children=children,
            )
    except SubmissionError as exc:
        raise HTTPException(status_code=422, detail=exc.code) from exc
    limiter.record("lb_detect_batch_tv")
    return submission_response(result.parent)


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
    detail["preview_minute"] = preview_minute
    detail["sample_previews"] = _sample_preview_entries(
        samples,
        url_for=lambda minute: (
            f"/api/letterbox/movies/{movie_id}/preview?mode=before&minute={minute}"
        ),
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
    initiator: Initiator,
):
    movie_ids = await _resolve_batch_movie_ids(body, db)
    if not movie_ids:
        raise HTTPException(status_code=400, detail="No matching candidate movies")

    nonce = uuid4().hex
    children: list[SubmissionIntent] = []
    for movie_id in movie_ids:
        movie = await db.get(Movie, movie_id)
        if movie is None:
            continue
        media_file = await db.scalar(
            select(MediaFile).where(MediaFile.movie_id == movie.id, MediaFile.is_active.is_(True))
        )
        if media_file is None:
            continue
        children.append(
            SubmissionIntent(
                job_type="letterbox_detect",
                request={
                    "movie_id": movie.id,
                    "media_file_id": media_file.id,
                    "detection_config": _letterbox_detection_config_snapshot(),
                },
                subject=SubjectLocator(kind="media_file", reference=str(media_file.id)),
                trigger=TriggerKind.BATCH,
                initiator=initiator,
                idempotency_key=f"letterbox_detect:batch-{nonce}-{media_file.id}",
            )
        )
    return await create_fixed_batch(
        db,
        parent_job_type="letterbox_detect_batch",
        parent_request={
            "movie_ids": movie_ids,
            "detection_config": _letterbox_detection_config_snapshot(),
        },
        scope=BatchScope(reference=nonce, display_name="Letterbox detection · movies"),
        trigger=TriggerKind.BATCH,
        initiator=initiator,
        idempotency_key=f"letterbox_detect_batch:manual-{nonce}",
        children=children,
    )


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


@router.post("/movies/{movie_id}/detect", status_code=202)
async def detect_one(
    movie_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    limiter: Annotated[RateLimiter, Depends(get_rate_limiter)],
    thorough: bool = Query(False),
) -> JobSubmissionResponse:
    """Submit one read-only movie/file observation through the canonical runtime."""
    enforce_rate_limit(limiter, f"lb_detect:{movie_id}", settings.RATE_LETTERBOX_DETECT_SECONDS)
    movie = await _load_movie(db, movie_id)
    media_file = await ensure_media_file_for_movie(db, movie)
    if media_file is None:
        raise HTTPException(status_code=409, detail="movie has no active media file")
    # The legacy projection helper commits when it creates a missing MediaFile and leaves
    # read-only lookups in an autobegun transaction.  Close that tiny projection scope
    # before opening the canonical submission transaction that owns PgQueuer enqueueing.
    if db.in_transaction():
        await db.commit()
    try:
        async with db.begin():
            result = await submit_job(
                db,
                job_type="letterbox_detect",
                request={
                    "movie_id": movie.id,
                    "media_file_id": media_file.id,
                    "thorough": thorough,
                    "detection_config": _letterbox_detection_config_snapshot(),
                },
                subject=SubjectLocator(kind="media_file", reference=str(media_file.id)),
                trigger=TriggerKind.MANUAL,
                initiator=Initiator(kind="system", identifier="letterbox-api"),
                idempotency_key=f"letterbox_detect:manual-{uuid4().hex}",
            )
    except SubmissionError as exc:
        raise HTTPException(status_code=422, detail=exc.code) from exc
    limiter.record(f"lb_detect:{movie_id}")
    return submission_response(result)


@router.post("/detect", status_code=202)
async def detect_batch(
    body: BatchDetectRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    limiter: Annotated[RateLimiter, Depends(get_rate_limiter)],
) -> JobSubmissionResponse:
    """Submit a sealed batch of read-only movie/file observations."""
    enforce_rate_limit(limiter, "lb_detect_batch", settings.RATE_LETTERBOX_BATCH_SECONDS)
    try:
        async with db.begin():
            result = await _start_detect_job(
                body, db, initiator=Initiator(kind="system", identifier="letterbox-api")
            )
    except SubmissionError as exc:
        raise HTTPException(status_code=422, detail=exc.code) from exc
    limiter.record("lb_detect_batch")
    return submission_response(result.parent)


@router.post("/tv/{series_id}/detect", status_code=202)
async def detect_tv_series(
    series_id: int,
    body: TvDetectRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> JobSubmissionResponse:
    try:
        async with db.begin():
            rows = await _tv_scope_rows(
                db,
                series_id,
                season_number=body.season_number,
                episode_id=body.episode_id,
            )
            if not rows:
                raise HTTPException(status_code=404, detail=f"Series id={series_id} not found")
            nonce = uuid4().hex
            scopes = (
                [(body.season_number, body.episode_id)]
                if body.season_number is not None or body.episode_id is not None
                else [
                    (season_number, None)
                    for season_number in sorted({row[0].season_number for row in rows})
                ]
            )
            initiator = Initiator(kind="system", identifier="letterbox-api")
            children = [
                SubmissionIntent(
                    job_type="letterbox_detect_tv_scope",
                    request={
                        "series_id": series_id,
                        "season_number": season_number,
                        "episode_id": episode_id,
                        "exhaustive": body.exhaustive,
                        "force": body.force,
                        "include_open_matte": body.include_open_matte,
                        "detection_config": _letterbox_detection_config_snapshot(),
                    },
                    subject=SubjectLocator(kind="series", reference=str(series_id)),
                    trigger=TriggerKind.BATCH,
                    initiator=initiator,
                    idempotency_key=(
                        f"letterbox_detect_tv_scope:batch-{nonce}-{season_number}-{episode_id}"
                    ),
                )
                for season_number, episode_id in scopes
            ]
            result = await create_fixed_batch(
                db,
                parent_job_type="letterbox_detect_tv_batch",
                parent_request={
                    "series_id": series_id,
                    "season_number": body.season_number,
                    "episode_id": body.episode_id,
                    "exhaustive": body.exhaustive,
                    "force": body.force,
                    "detection_config": _letterbox_detection_config_snapshot(),
                },
                scope=BatchScope(reference=nonce, display_name="Letterbox detection · TV"),
                trigger=TriggerKind.BATCH,
                initiator=initiator,
                idempotency_key=f"letterbox_detect_tv_batch:manual-{nonce}",
                children=children,
            )
    except SubmissionError as exc:
        raise HTTPException(status_code=422, detail=exc.code) from exc
    return submission_response(result.parent)


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
    eligibility = await asyncio.to_thread(check_movie_eligibility, movie)
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
    from marquee.core.filesystem import (  # noqa: PLC0415
        FilesystemBoundaryError,
        boundary_for_roots,
    )

    try:
        boundary = boundary_for_roots(
            {"preview": settings.letterbox_preview_path}, purpose="letterbox-preview"
        )
        classified = boundary.classify(out, require_file=True)
        return boundary.response(classified, media_type="image/webp")
    except FilesystemBoundaryError as exc:
        raise HTTPException(status_code=403, detail="Preview path outside cache tree") from exc


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
        raise HTTPException(status_code=404, detail="Media file unavailable for preview") from exc

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
    from marquee.core.filesystem import (  # noqa: PLC0415
        FilesystemBoundaryError,
        boundary_for_roots,
    )

    try:
        boundary = boundary_for_roots(
            {"preview": settings.letterbox_preview_path}, purpose="letterbox-preview"
        )
        classified = boundary.classify(out, require_file=True)
        return boundary.response(classified, media_type="image/webp")
    except FilesystemBoundaryError as exc:
        raise HTTPException(status_code=403, detail="Preview path outside cache tree") from exc


# ---------------------------------------------------------------------------
# Apply / remove / ignore
# ---------------------------------------------------------------------------


class ApplyRequest(BaseModel):
    top: int | None = None
    bottom: int | None = None


class TvApplyRequest(BaseModel):
    season_number: int | None = None
    episode_id: int | None = None
    confidence_levels: list[str] | None = None


class TvRevertRequest(BaseModel):
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


class TvBatchReencodeRequest(BaseModel):
    season_number: int | None = None
    confidence_levels: list[str] | None = None
    settings: BatchReencodeSettings


class TvReplaceReadyRequest(BaseModel):
    season_number: int | None = None


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


def _validate_confidence_levels(levels: list[str] | None) -> list[str] | None:
    try:
        normalize_confidence_levels(levels)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return ["high"] if levels is None else levels


@router.post("/tv/{series_id}/apply", status_code=202)
async def apply_tv_scope(
    series_id: int,
    body: TvApplyRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> JobSubmissionResponse:
    confidence_levels = tuple(_validate_confidence_levels(body.confidence_levels) or ())
    rows = await _tv_scope_rows(
        db,
        series_id,
        season_number=body.season_number if body.episode_id is None else None,
    )
    if not rows:
        raise HTTPException(status_code=404, detail=f"Series id={series_id} not found")
    if body.episode_id is not None:
        rows = _scope_episode_group(rows, body.episode_id)
    return await _submit_tv_mutation_parent(
        db,
        series_id=series_id,
        season_number=body.season_number,
        rows=rows,
        operation="apply",
        confidence_levels=confidence_levels,
    )


@router.post("/tv/{series_id}/revert", status_code=202)
async def revert_tv_scope(
    series_id: int,
    body: TvRevertRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> JobSubmissionResponse:
    rows = await _tv_scope_rows(
        db,
        series_id,
        season_number=body.season_number if body.episode_id is None else None,
    )
    if not rows:
        raise HTTPException(status_code=404, detail=f"Series id={series_id} not found")
    if body.episode_id is not None:
        rows = _scope_episode_group(rows, body.episode_id)
    return await _submit_tv_mutation_parent(
        db,
        series_id=series_id,
        season_number=body.season_number,
        rows=rows,
        operation="remove",
    )


@router.post("/movies/{movie_id}/apply", status_code=202)
async def apply_one(
    movie_id: int,
    body: ApplyRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> JobSubmissionResponse:
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
    media_file, before = await _letterbox_mutation_snapshot(db, movie, state)
    if db.in_transaction():
        await db.commit()
    try:
        async with db.begin():
            result = await submit_job(
                db,
                job_type="letterbox_apply",
                request={
                    "media_file_id": media_file.id,
                    "subject_kind": "movie",
                    "subject_ids": [movie.id],
                    "before": before,
                    "crop_top": top or 0,
                    "crop_bottom": bottom or 0,
                    "source": "api",
                },
                subject=SubjectLocator(kind="media_file", reference=str(media_file.id)),
                trigger=TriggerKind.MANUAL,
                initiator=Initiator(kind="system", identifier="letterbox-api"),
                idempotency_key=f"letterbox_apply:manual-{uuid4().hex}",
                priority=80,
            )
    except SubmissionError as exc:
        raise HTTPException(status_code=422, detail=exc.code) from exc
    return submission_response(result)


@router.post("/apply", status_code=202)
async def apply_batch(
    body: BatchApplyRequest, db: Annotated[AsyncSession, Depends(get_db)]
) -> JobSubmissionResponse:
    """Seal one canonical apply child per eligible physical movie file."""
    initiator = Initiator(kind="system", identifier="letterbox-api")
    children: list[SubmissionIntent] = []
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
            continue
        if state.status == "variable_unsafe" or not state.recommended_crop_top:
            continue
        if body.only_high and state.confidence != "high":
            continue
        media_file, before = await _letterbox_mutation_snapshot(db, movie, state)
        children.append(
            SubmissionIntent(
                job_type="letterbox_apply",
                request={
                    "media_file_id": media_file.id,
                    "subject_kind": "movie",
                    "subject_ids": [movie.id],
                    "before": before,
                    "crop_top": state.recommended_crop_top or 0,
                    "crop_bottom": state.recommended_crop_bottom or 0,
                    "source": "api",
                },
                subject=SubjectLocator(kind="media_file", reference=str(media_file.id)),
                trigger=TriggerKind.BATCH,
                initiator=initiator,
                idempotency_key=f"letterbox_apply:batch-{uuid4().hex}",
                priority=70,
            )
        )
    if db.in_transaction():
        await db.commit()
    scope_key = uuid4().hex[:12]
    try:
        async with db.begin():
            result = await create_fixed_batch(
                db,
                parent_job_type="letterbox_apply_batch",
                parent_request={
                    "operation": "apply",
                    "confidence_levels": ["high"] if body.only_high else [],
                    "sealed_file_count": len(children),
                },
                scope=BatchScope(
                    reference=f"movies-apply-{scope_key}",
                    display_name="Letterbox apply: selected movies",
                    summary=f"{len(children)} sealed physical media files",
                ),
                trigger=TriggerKind.MANUAL,
                initiator=initiator,
                idempotency_key=f"letterbox_apply_batch:manual-{uuid4().hex}",
                children=children,
                priority=70,
            )
    except SubmissionError as exc:
        raise HTTPException(status_code=422, detail=exc.code) from exc
    return submission_response(result.parent)


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


def _map_reencode_error(exc: letterbox_transcode.ReencodePlanError) -> HTTPException:
    return HTTPException(
        status_code=422,
        detail={"code": exc.code, "message": str(exc), "warnings": exc.warnings},
    )


def _canonical_reencode_artifact(artifact: JobArtifact) -> dict[str, object]:
    return {
        "id": artifact.id,
        "job_id": artifact.job_id,
        "status": artifact.status,
        "kind": artifact.kind,
        "name": artifact.name,
        "content_type": artifact.content_type,
        "size_bytes": artifact.size_bytes,
        "checksum": artifact.checksum,
        "metadata": artifact.artifact_metadata,
        "created_at": artifact.created_at.isoformat(),
        "expires_at": artifact.expires_at.isoformat() if artifact.expires_at else None,
    }


async def _load_reencode_artifact(db: AsyncSession, artifact_id: int) -> JobArtifact:
    artifact = await db.get(JobArtifact, artifact_id)
    if artifact is None:
        raise HTTPException(status_code=404, detail=f"Re-encode artifact {artifact_id} not found")
    if artifact.kind != "media_candidate":
        raise HTTPException(status_code=404, detail=f"Re-encode artifact {artifact_id} not found")
    return artifact


def _artifact_subject(artifact: JobArtifact) -> tuple[str, int, int]:
    metadata = artifact.artifact_metadata or {}
    try:
        media_file_id = int(metadata["media_file_id"])
        subject_kind = str(metadata["subject_kind"])
        subject_id = int(metadata["subject_id"])
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=409, detail="Candidate ownership evidence is incomplete"
        ) from exc
    if subject_kind not in {"movie", "episode"} or media_file_id < 1 or subject_id < 1:
        raise HTTPException(status_code=409, detail="Candidate ownership evidence is invalid")
    return subject_kind, subject_id, media_file_id


def _candidate_probes(artifact: JobArtifact) -> tuple[ReencodeProbeV1, ReencodeProbeV1]:
    metadata = artifact.artifact_metadata or {}
    try:
        return (
            ReencodeProbeV1.model_validate(metadata["source_probe"]),
            ReencodeProbeV1.model_validate(metadata["output_probe"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=409, detail="Candidate probe evidence is incomplete"
        ) from exc


async def _plan_reencode_decision(
    db: AsyncSession,
    *,
    operation: str,
    job_type: str,
    request: (
        LetterboxReencodePublishRequestV1
        | LetterboxReencodeRestoreRequestV1
        | LetterboxReencodeDiscardRequestV1
    ),
    input_signature: str,
) -> tuple[Job, datetime, str]:
    target = MutationTargetV1(
        key=f"media-file:{request.media_file_id}:reencode-{operation}",
        kind="media_file" if operation != "discard" else "media_candidate",
        label=f"Letterbox re-encode {operation}",
        operation=operation,
        selector_facts={
            "media_file_id": request.media_file_id,
            "candidate_artifact_id": request.candidate_artifact_id,
        },
    )
    try:
        result = await plan_mutation(
            db,
            job_type=job_type,
            request=request.model_dump(mode="json", exclude_none=True),
            subject=SubjectLocator(kind=request.subject_kind, reference=str(request.subject_id)),
            initiator=Initiator(kind="user", identifier="letterbox-api"),
            idempotency_key=f"letterbox_reencode_{operation}:plan-{uuid4().hex}",
            priority=70,
            plan=MutationPlan(
                operation_kind=f"letterbox_reencode_{operation}",
                media_file_id=request.media_file_id,
                media_snapshot={
                    "media_file_id": request.media_file_id,
                    "subject_kind": request.subject_kind,
                    "subject_id": request.subject_id,
                    "candidate_artifact_id": request.candidate_artifact_id,
                },
                before_targets=(target,),
                requested_targets=(target,),
                expected_targets=(target,),
                input_signature=input_signature,
                confirmation_requirements={
                    "explicit_confirmation": True,
                    "operation": operation,
                },
            ),
        )
    except SubmissionError as exc:
        raise HTTPException(status_code=422, detail=exc.code) from exc
    job = await db.get(Job, result.job_id)
    detail = await db.get(MediaOperationDetail, result.job_id)
    if job is None or detail is None or detail.plan_expires_at is None:
        raise HTTPException(status_code=500, detail="Artifact decision plan was not persisted")
    await db.commit()
    return job, detail.plan_expires_at, plan_version(detail)


def _reencode_request(
    plan: dict,
    *,
    media_file_id: int,
    subject_kind: str,
    subject_id: int,
) -> LetterboxReencodeRequestV1:
    source = plan["source"]
    encoder = plan["encoder"]
    crop = plan["crop"]
    return LetterboxReencodeRequestV1.model_validate(
        {
            "media_file_id": media_file_id,
            "subject_kind": subject_kind,
            "subject_id": subject_id,
            "crop_top": crop["top"],
            "crop_bottom": crop["bottom"],
            "output_height": crop["output_height"],
            "source": {
                "signature": source["signature"],
                "size_bytes": source["size_bytes"],
                "codec": source["codec"],
                "width": source["width"],
                "height": source["height"],
                "duration_seconds": source["duration_seconds"],
                "pixel_format": source["pix_fmt"],
                "color_transfer": source["color_transfer"],
                "color_primaries": source["color_primaries"],
                "color_space": source["color_space"],
                "has_hdr": source["has_hdr"],
                "has_dolby_vision": source["has_dovi"],
                "video_streams": source["video_streams"],
                "audio_streams": source["audio_streams"],
                "subtitle_streams": source["subtitle_streams"],
                "attachment_streams": source["attachment_streams"],
            },
            "encoder": {
                "codec": encoder["codec"],
                "encoder": encoder["encoder"],
                "family": encoder["family"],
                "quality": encoder["quality"],
                "preset": encoder["preset"],
                "used_cpu_fallback": encoder["used_cpu_fallback"],
            },
        }
    )


async def _plan_reencode(
    db: AsyncSession,
    *,
    request: LetterboxReencodeRequestV1,
    subject: SubjectLocator,
    idempotency_key: str,
):
    target = MutationTargetV1(
        key=f"media-file:{request.media_file_id}:reencode-candidate",
        kind="media_candidate",
        label="Permanent letterbox crop candidate",
        operation="reencode",
        selector_facts={
            "media_file_id": request.media_file_id,
            "crop_top": request.crop_top,
            "crop_bottom": request.crop_bottom,
        },
    )
    return await plan_mutation(
        db,
        job_type="letterbox_reencode",
        request=request.model_dump(mode="json"),
        subject=subject,
        initiator=Initiator(kind="user", identifier="letterbox-api"),
        idempotency_key=idempotency_key,
        priority=70,
        plan=MutationPlan(
            operation_kind="letterbox_reencode",
            media_file_id=request.media_file_id,
            media_snapshot=request.source.model_dump(mode="json"),
            before_targets=(target,),
            requested_targets=(target,),
            expected_targets=(target,),
            input_signature=request.source.signature,
            confirmation_requirements={"candidate_only": True, "source_publish": False},
        ),
    )


async def _create_reencode_plan_job(
    db: AsyncSession,
    movie_id: int,
    body: ReencodePlanRequest,
) -> tuple[Job, dict, datetime]:
    movie = await _load_movie(db, movie_id)
    state = await _load_state(db, movie_id)
    media_file = await ensure_media_file_for_movie(db, movie)
    if media_file is None:
        raise HTTPException(status_code=422, detail="Movie has no active media file")
    resolved = await resolve_media_file(db, media_file.id)
    top = body.top if body.top is not None else state.recommended_crop_top or 0
    bottom = body.bottom if body.bottom is not None else state.recommended_crop_bottom or 0
    try:
        plan = await letterbox_transcode.build_plan(
            db,
            resolved,
            top=top,
            bottom=bottom,
            allow_cpu_fallback=body.allow_cpu_fallback,
            encoder=body.encoder,
            quality=body.quality,
            preset=body.preset,
            codec=body.codec,
        )
        request = _reencode_request(
            plan, media_file_id=media_file.id, subject_kind="movie", subject_id=movie.id
        )
        result = await _plan_reencode(
            db,
            request=request,
            subject=SubjectLocator(kind="movie", reference=str(movie.id)),
            idempotency_key=f"letterbox-reencode-plan:{movie.id}:{uuid4().hex}",
        )
    except letterbox_transcode.ReencodePlanError as exc:
        raise _map_reencode_error(exc) from exc
    job = await db.get(Job, result.job_id)
    detail = await db.get(MediaOperationDetail, result.job_id)
    if job is None or detail is None or detail.plan_expires_at is None:
        raise HTTPException(status_code=500, detail="Re-encode plan evidence was not persisted")
    plan["plan_version"] = plan_version(detail)
    plan["configuration_version"] = job.configuration_version
    await db.commit()
    return job, plan, detail.plan_expires_at


async def _create_tv_reencode_plan_job(
    db: AsyncSession,
    series_id: int,
    episode_id: int,
    body: ReencodePlanRequest,
) -> tuple[Job, dict, datetime]:
    rows = await _tv_scope_rows(db, series_id)
    row = next((item for item in rows if item[0].id == episode_id), None)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Episode id={episode_id} not found")
    episode, _series, state, media_file_id = row
    if state is None or media_file_id is None:
        raise HTTPException(status_code=422, detail="Episode lacks detection or active media")
    resolved = await resolve_media_file(db, media_file_id)
    top = body.top if body.top is not None else state.recommended_crop_top or 0
    bottom = body.bottom if body.bottom is not None else state.recommended_crop_bottom or 0
    try:
        plan = await letterbox_transcode.build_plan(
            db,
            resolved,
            top=top,
            bottom=bottom,
            allow_cpu_fallback=body.allow_cpu_fallback,
            encoder=body.encoder,
            quality=body.quality,
            preset=body.preset,
            codec=body.codec,
        )
        request = _reencode_request(
            plan,
            media_file_id=media_file_id,
            subject_kind="episode",
            subject_id=episode.id,
        )
        result = await _plan_reencode(
            db,
            request=request,
            subject=SubjectLocator(kind="episode", reference=str(episode.id)),
            idempotency_key=f"letterbox-reencode-plan:{episode.id}:{uuid4().hex}",
        )
    except letterbox_transcode.ReencodePlanError as exc:
        raise _map_reencode_error(exc) from exc
    job = await db.get(Job, result.job_id)
    detail = await db.get(MediaOperationDetail, result.job_id)
    if job is None or detail is None or detail.plan_expires_at is None:
        raise HTTPException(status_code=500, detail="Re-encode plan evidence was not persisted")
    plan["plan_version"] = plan_version(detail)
    plan["configuration_version"] = job.configuration_version
    await db.commit()
    return job, plan, detail.plan_expires_at


async def _confirm_media_job_plan(db: AsyncSession, job: object) -> None:
    if not isinstance(job, Job):
        raise HTTPException(status_code=500, detail="Invalid canonical re-encode plan")
    detail = await db.get(MediaOperationDetail, job.id)
    if detail is None:
        raise HTTPException(status_code=409, detail="Re-encode plan evidence is missing")
    await confirm_mutation(
        db,
        job_id=job.id,
        expected_plan_version=plan_version(detail),
        current_input_signature=detail.input_signature,
        confirmed_by=Initiator(kind="user", identifier="letterbox-api"),
        expected_configuration_version=job.configuration_version,
    )
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
        "job_id": job.id,
        "status": "planned",
        "expires_at": expires_at.isoformat(),
    }


@router.post("/tv/{series_id}/episodes/{episode_id}/reencode-plan", status_code=201)
async def create_tv_reencode_plan(
    series_id: int,
    episode_id: int,
    body: ReencodePlanRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    _require_ffmpeg()
    job, plan, expires_at = await _create_tv_reencode_plan_job(db, series_id, episode_id, body)
    return {
        **plan,
        "job_id": job.id,
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
            job_ids.append(job.id)
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


@router.post("/tv/{series_id}/reencode", status_code=202)
async def tv_batch_reencode(
    series_id: int,
    body: TvBatchReencodeRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    _require_ffmpeg()
    confidence_levels = _validate_confidence_levels(body.confidence_levels)
    normalized_levels = normalize_confidence_levels(confidence_levels)
    rows = await _tv_scope_rows(db, series_id, season_number=body.season_number)
    if not rows:
        raise HTTPException(status_code=404, detail=f"Series id={series_id} not found")

    plan_body = ReencodePlanRequest(
        top=body.settings.crop_top_override,
        bottom=body.settings.crop_bottom_override,
        allow_cpu_fallback=body.settings.allow_cpu,
        encoder=body.settings.encoder,
        quality=body.settings.quality,
        preset=body.settings.preset,
        codec=body.settings.codec,
    )
    groups: dict[int, tuple[Episode, LetterboxState]] = {}
    skipped: list[dict] = []
    for episode, _series, state, media_file_id in rows:
        if state is None:
            skipped.append(
                {
                    "episode_id": episode.id,
                    "code": "missing_state",
                    "reason": "Run detect before re-encoding.",
                    "sXXeYY": f"S{episode.season_number:02d}E{episode.episode_number:02d}",
                }
            )
            continue
        if state.status != "candidate":
            continue
        if not (state.recommended_crop_top or state.recommended_crop_bottom):
            skipped.append(
                {
                    "episode_id": episode.id,
                    "code": "missing_crop",
                    "reason": "No crop to apply (recommendation is 0).",
                    "sXXeYY": f"S{episode.season_number:02d}E{episode.episode_number:02d}",
                }
            )
            continue
        if normalized_levels is not None and state.confidence not in normalized_levels:
            skipped.append(
                {
                    "episode_id": episode.id,
                    "code": "confidence_filtered",
                    "reason": "Candidate confidence is outside the requested levels.",
                    "sXXeYY": f"S{episode.season_number:02d}E{episode.episode_number:02d}",
                }
            )
            continue
        if media_file_id is None:
            skipped.append(
                {
                    "episode_id": episode.id,
                    "code": "missing_media_file",
                    "reason": "Episode has no media file path.",
                    "sXXeYY": f"S{episode.season_number:02d}E{episode.episode_number:02d}",
                }
            )
            continue
        groups.setdefault(media_file_id, (episode, state))

    nonce = uuid4().hex
    initiator = Initiator(kind="user", identifier="letterbox-api")
    children: list[SubmissionIntent] = []
    for media_file_id, (episode, state) in groups.items():
        try:
            resolved = await resolve_media_file(db, media_file_id)
            top = plan_body.top if plan_body.top is not None else state.recommended_crop_top or 0
            bottom = (
                plan_body.bottom
                if plan_body.bottom is not None
                else state.recommended_crop_bottom or 0
            )
            plan = await letterbox_transcode.build_plan(
                db,
                resolved,
                top=top,
                bottom=bottom,
                allow_cpu_fallback=plan_body.allow_cpu_fallback,
                encoder=plan_body.encoder,
                quality=plan_body.quality,
                preset=plan_body.preset,
                codec=plan_body.codec,
            )
            request = _reencode_request(
                plan,
                media_file_id=media_file_id,
                subject_kind="episode",
                subject_id=episode.id,
            )
            children.append(
                SubmissionIntent(
                    job_type="letterbox_reencode",
                    request=request.model_dump(mode="json"),
                    subject=SubjectLocator(kind="episode", reference=str(episode.id)),
                    trigger=TriggerKind.BATCH,
                    initiator=initiator,
                    idempotency_key=f"letterbox_reencode:tv-{nonce}-{media_file_id}",
                    priority=70,
                )
            )
        except (HTTPException, letterbox_transcode.ReencodePlanError) as exc:
            detail_value = exc.detail if isinstance(exc, HTTPException) else str(exc)
            detail = (
                detail_value if isinstance(detail_value, dict) else {"message": str(detail_value)}
            )
            skipped.append(
                {
                    "episode_id": episode.id,
                    "code": detail.get("code"),
                    "reason": detail.get("message") or str(exc.detail),
                    "sXXeYY": f"S{episode.season_number:02d}E{episode.episode_number:02d}",
                }
            )
    if db.in_transaction():
        await db.commit()
    try:
        async with db.begin():
            result = await create_fixed_batch(
                db,
                parent_job_type="letterbox_reencode_tv_batch",
                parent_request={
                    "series_id": series_id,
                    "season_number": body.season_number,
                    "confidence_levels": confidence_levels,
                },
                scope=BatchScope(
                    reference=f"series-{series_id}-reencode-{nonce[:12]}",
                    display_name=f"Letterbox re-encode: series {series_id}",
                    summary=f"{len(children)} sealed physical media files",
                ),
                trigger=TriggerKind.MANUAL,
                initiator=initiator,
                idempotency_key=f"letterbox_reencode_tv_batch:manual-{nonce}",
                children=children,
                priority=70,
            )
    except SubmissionError as exc:
        raise HTTPException(status_code=422, detail=exc.code) from exc
    return {
        "job_ids": [child.job_id for child in result.children],
        "count": len(result.children),
        "skipped": skipped,
        "parent_job_id": result.parent.job_id,
    }


@router.get("/reencode-artifacts")
async def list_reencode_artifacts(
    db: Annotated[AsyncSession, Depends(get_db)],
    status: str | None = None,
    movie_id: int | None = None,
    series_id: int | None = None,
    season_number: int | None = None,
    limit: int = Query(100, ge=1, le=500),
):
    query = (
        select(JobArtifact)
        .join(Job, Job.id == JobArtifact.job_id)
        .where(JobArtifact.kind == "media_candidate", Job.type == "letterbox_reencode")
        .order_by(JobArtifact.created_at.desc(), JobArtifact.id.desc())
        .limit(limit)
    )
    if status:
        query = query.where(JobArtifact.status == status)
    if movie_id is not None:
        query = query.where(Job.subject_kind == "movie", Job.subject_reference == str(movie_id))
    if series_id is not None or season_number is not None:
        query = query.join(
            Episode,
            cast(Episode.id, String) == Job.subject_reference,
        ).where(Job.subject_kind == "episode")
        if series_id is not None:
            query = query.where(Episode.series_id == series_id)
        if season_number is not None:
            query = query.where(Episode.season_number == season_number)
    rows = (await db.execute(query)).scalars().unique().all()
    counts = {
        "total": len(rows),
        "available": sum(row.status == "available" for row in rows),
        "pending": sum(row.status == "pending" for row in rows),
        "failed": sum(row.status == "failed" for row in rows),
    }
    return {"summary": counts, "items": [_canonical_reencode_artifact(row) for row in rows]}


@router.post("/tv/{series_id}/reencode-artifacts/replace-ready")
async def replace_ready_tv_reencode_artifacts(
    series_id: int,
    body: TvReplaceReadyRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    rows = await _tv_scope_rows(db, series_id, season_number=body.season_number)
    if not rows:
        raise HTTPException(status_code=404, detail=f"Series id={series_id} not found")
    media_file_ids = {
        media_file_id for _episode, _series, _state, media_file_id in rows if media_file_id
    }
    artifacts = list(
        (
            await db.scalars(
                select(JobArtifact)
                .where(
                    JobArtifact.kind == "media_candidate",
                    JobArtifact.status == "available",
                )
                .order_by(JobArtifact.created_at.desc(), JobArtifact.id.desc())
                .limit(10_000)
            )
        ).all()
    )
    latest: dict[int, JobArtifact] = {}
    for artifact in artifacts:
        metadata = artifact.artifact_metadata or {}
        try:
            media_file_id = int(metadata.get("media_file_id") or 0)
        except (TypeError, ValueError):
            continue
        if media_file_id in media_file_ids and media_file_id not in latest:
            latest[media_file_id] = artifact

    initiator = Initiator(kind="user", identifier="letterbox-api")
    children: list[SubmissionIntent] = []
    skipped: list[dict[str, object]] = []
    for media_file_id in sorted(media_file_ids):
        artifact = latest.get(media_file_id)
        if artifact is None or not artifact.checksum or not artifact.size_bytes:
            skipped.append({"media_file_id": media_file_id, "reason": "no_ready_candidate"})
            continue
        metadata = artifact.artifact_metadata or {}
        if metadata.get("published") and not metadata.get("restored"):
            skipped.append({"media_file_id": media_file_id, "reason": "already_published"})
            continue
        try:
            subject_kind, subject_id, _ = _artifact_subject(artifact)
            source_probe, candidate_probe = _candidate_probes(artifact)
            resolved = await resolve_media_file(db, media_file_id)
            if resolved.path.suffix.lower() != ".mkv":
                raise ValueError("unsupported_container")
            expected_source_signature = str(metadata.get("source_signature") or "")
            if resolved.signature != expected_source_signature:
                raise ValueError("source_changed")
            request = LetterboxReencodePublishRequestV1(
                media_file_id=media_file_id,
                subject_kind=subject_kind,
                subject_id=subject_id,
                candidate_artifact_id=artifact.id,
                candidate_job_id=artifact.job_id,
                candidate_checksum=artifact.checksum,
                candidate_size_bytes=artifact.size_bytes,
                expected_source_signature=expected_source_signature,
                crop_top=int(metadata.get("crop_top") or 0),
                crop_bottom=int(metadata.get("crop_bottom") or 0),
                source_probe=source_probe,
                candidate_probe=candidate_probe,
            )
        except (HTTPException, ValueError):
            skipped.append({"media_file_id": media_file_id, "reason": "stale_candidate"})
            continue
        children.append(
            SubmissionIntent(
                job_type="letterbox_reencode_publish",
                request=request.model_dump(mode="json"),
                subject=SubjectLocator(kind=subject_kind, reference=str(subject_id)),
                trigger=TriggerKind.BATCH,
                initiator=initiator,
                idempotency_key=f"letterbox_reencode_publish:tv-{uuid4().hex}",
                priority=70,
            )
        )
    if db.in_transaction():
        await db.commit()
    try:
        async with db.begin():
            result = await create_fixed_batch(
                db,
                parent_job_type="letterbox_reencode_publish_batch",
                parent_request={
                    "series_id": series_id,
                    "season_number": body.season_number,
                },
                scope=BatchScope(
                    reference=f"series-{series_id}-publish-{uuid4().hex[:12]}",
                    display_name=f"Publish letterbox candidates: series {series_id}",
                    summary=f"{len(children)} sealed physical media files",
                ),
                trigger=TriggerKind.MANUAL,
                initiator=initiator,
                idempotency_key=f"letterbox_reencode_publish_batch:manual-{uuid4().hex}",
                children=children,
                priority=70,
            )
    except SubmissionError as exc:
        raise HTTPException(status_code=422, detail=exc.code) from exc
    return {
        "parent_job_id": result.parent.job_id,
        "job_ids": [child.job_id for child in result.children],
        "count": len(result.children),
        "skipped": skipped,
    }


@router.post("/reencode-artifacts/{artifact_id}/replace-original")
async def replace_reencode_original(
    artifact_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    artifact = await _load_reencode_artifact(db, artifact_id)
    if artifact.status != "available" or not artifact.checksum or not artifact.size_bytes:
        raise HTTPException(status_code=409, detail="Candidate is not available for publication")
    metadata = artifact.artifact_metadata or {}
    if metadata.get("published") and not metadata.get("restored"):
        raise HTTPException(status_code=409, detail="Candidate is already published")
    subject_kind, subject_id, media_file_id = _artifact_subject(artifact)
    source_probe, candidate_probe = _candidate_probes(artifact)
    resolved = await resolve_media_file(db, media_file_id)
    if resolved.path.suffix.lower() != ".mkv":
        raise HTTPException(status_code=422, detail="Only Matroska publication is certified")
    expected_source_signature = str(metadata.get("source_signature") or "")
    if not expected_source_signature or resolved.signature != expected_source_signature:
        raise HTTPException(status_code=409, detail="Source changed after candidate creation")
    request = LetterboxReencodePublishRequestV1(
        media_file_id=media_file_id,
        subject_kind=subject_kind,
        subject_id=subject_id,
        candidate_artifact_id=artifact.id,
        candidate_job_id=artifact.job_id,
        candidate_checksum=artifact.checksum,
        candidate_size_bytes=artifact.size_bytes,
        expected_source_signature=expected_source_signature,
        crop_top=int(metadata.get("crop_top") or 0),
        crop_bottom=int(metadata.get("crop_bottom") or 0),
        source_probe=source_probe,
        candidate_probe=candidate_probe,
    )
    job, expires_at, expected_plan_version = await _plan_reencode_decision(
        db,
        operation="publish",
        job_type="letterbox_reencode_publish",
        request=request,
        input_signature=resolved.signature,
    )
    return {
        "job_id": job.id,
        "status": "planned",
        "expires_at": expires_at.isoformat(),
        "plan_version": expected_plan_version,
        "configuration_version": job.configuration_version,
        "artifact_id": artifact.id,
        "operation": "publish",
    }


@router.post("/reencode-artifacts/{artifact_id}/restore-original")
async def restore_reencode_original(
    artifact_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    artifact = await _load_reencode_artifact(db, artifact_id)
    metadata = artifact.artifact_metadata or {}
    backup_artifact_id = int(metadata.get("backup_artifact_id") or 0)
    backup = await db.get(JobArtifact, backup_artifact_id) if backup_artifact_id else None
    if (
        not metadata.get("published")
        or metadata.get("restored")
        or backup is None
        or backup.kind != "media_backup"
        or backup.status != "available"
        or not backup.checksum
        or not backup.size_bytes
        or not artifact.checksum
    ):
        raise HTTPException(status_code=409, detail="Restorable canonical backup is unavailable")
    subject_kind, subject_id, media_file_id = _artifact_subject(artifact)
    resolved = await resolve_media_file(db, media_file_id)
    if resolved.path.suffix.lower() != ".mkv":
        raise HTTPException(status_code=422, detail="Only Matroska restoration is certified")
    request = LetterboxReencodeRestoreRequestV1(
        media_file_id=media_file_id,
        subject_kind=subject_kind,
        subject_id=subject_id,
        candidate_artifact_id=artifact.id,
        backup_artifact_id=backup.id,
        backup_checksum=backup.checksum,
        backup_size_bytes=backup.size_bytes,
        expected_destination_signature=resolved.signature,
        published_checksum=artifact.checksum,
    )
    job, expires_at, expected_plan_version = await _plan_reencode_decision(
        db,
        operation="restore",
        job_type="letterbox_reencode_restore",
        request=request,
        input_signature=resolved.signature,
    )
    return {
        "job_id": job.id,
        "status": "planned",
        "expires_at": expires_at.isoformat(),
        "plan_version": expected_plan_version,
        "configuration_version": job.configuration_version,
        "artifact_id": artifact.id,
        "operation": "restore",
        "candidate_retained": True,
    }


@router.delete("/reencode-artifacts/{artifact_id}")
async def delete_reencode_artifact(
    artifact_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    artifact = await _load_reencode_artifact(db, artifact_id)
    if artifact.status != "available" or not artifact.checksum:
        raise HTTPException(status_code=409, detail="Candidate is not available for discard")
    subject_kind, subject_id, media_file_id = _artifact_subject(artifact)
    resolved = await resolve_media_file(db, media_file_id)
    request = LetterboxReencodeDiscardRequestV1(
        media_file_id=media_file_id,
        subject_kind=subject_kind,
        subject_id=subject_id,
        candidate_artifact_id=artifact.id,
        candidate_checksum=artifact.checksum,
    )
    job, expires_at, expected_plan_version = await _plan_reencode_decision(
        db,
        operation="discard",
        job_type="letterbox_reencode_discard",
        request=request,
        input_signature=resolved.signature,
    )
    return {
        "job_id": job.id,
        "status": "planned",
        "expires_at": expires_at.isoformat(),
        "plan_version": expected_plan_version,
        "configuration_version": job.configuration_version,
        "artifact_id": artifact.id,
        "operation": "discard",
    }


@router.post("/tv/{series_id}/episodes/{episode_id}/remove", status_code=202)
async def remove_tv_episode(
    series_id: int,
    episode_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> JobSubmissionResponse:
    rows = await _tv_scope_rows(db, series_id)
    group_rows = _scope_episode_group(rows, episode_id)
    return await _submit_tv_mutation_parent(
        db,
        series_id=series_id,
        season_number=None,
        rows=group_rows,
        operation="remove",
    )


@router.post("/movies/{movie_id}/remove", status_code=202)
async def remove_one(
    movie_id: int, db: Annotated[AsyncSession, Depends(get_db)]
) -> JobSubmissionResponse:
    movie = await _load_movie(db, movie_id)
    state = await _load_state(db, movie_id)
    media_file, before = await _letterbox_mutation_snapshot(db, movie, state)
    if db.in_transaction():
        await db.commit()
    try:
        async with db.begin():
            result = await submit_job(
                db,
                job_type="letterbox_remove",
                request={
                    "media_file_id": media_file.id,
                    "subject_kind": "movie",
                    "subject_ids": [movie.id],
                    "before": before,
                    "source": "api",
                },
                subject=SubjectLocator(kind="media_file", reference=str(media_file.id)),
                trigger=TriggerKind.MANUAL,
                initiator=Initiator(kind="system", identifier="letterbox-api"),
                idempotency_key=f"letterbox_remove:manual-{uuid4().hex}",
                priority=80,
            )
    except SubmissionError as exc:
        raise HTTPException(status_code=422, detail=exc.code) from exc
    return submission_response(result)


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


@router.post("/heal", status_code=202)
async def letterbox_heal(
    db: Annotated[AsyncSession, Depends(get_db)],
) -> JobSubmissionResponse:
    """Seal tagged files; ordinary apply leaves probe and repair only verified drift."""
    initiator = Initiator(kind="system", identifier="letterbox-heal-api")
    children = await _letterbox_heal_intents(db, initiator=initiator)
    if db.in_transaction():
        await db.commit()
    scope_key = uuid4().hex[:12]
    try:
        async with db.begin():
            result = await create_fixed_batch(
                db,
                parent_job_type="letterbox_heal",
                parent_request={
                    "operation": "heal_apply",
                    "sealed_file_count": len(children),
                },
                scope=BatchScope(
                    reference=f"letterbox-heal-{scope_key}",
                    display_name="Letterbox metadata healing",
                    summary=f"{len(children)} tagged physical media files",
                ),
                trigger=TriggerKind.MANUAL,
                initiator=initiator,
                idempotency_key=f"letterbox_heal:manual-{uuid4().hex}",
                children=children,
                priority=20,
            )
    except SubmissionError as exc:
        raise HTTPException(status_code=422, detail=exc.code) from exc
    return submission_response(result.parent)


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
