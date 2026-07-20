"""Feedback routes — approve / override / reject, plus undo (design 09).

Every submission maps to the scenarios in design 09 §3–6:

  - approve     → 1 positive label for the auto-pick (scenario A)
  - override    → negative for the auto-pick + positive for the user's pick
                  (scenarios B/C), using the immutable candidate snapshot
  - reject_all  → 1 ``explicit_reject`` negative for the auto-pick (scenario D)

A single ``event_id`` ties together all rows a submission writes, so undo is
exact. Feedback schedules a canonical learned-head successor; it never mutates
configured exemplar folders or active publications in the API process.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from marquee.api.job_submission import submission_response
from marquee.core.jobs.contracts import TriggerKind
from marquee.core.jobs.mutation_documents import PosterCandidateSelectionV1
from marquee.core.jobs.pipeline_archives import load_pipeline_archive
from marquee.core.jobs.poster_submission import (
    PosterSelectionError,
    pipeline_candidate_selection,
    poster_child_idempotency_key,
    submit_poster_leaf,
)
from marquee.core.jobs.submission import (
    IdempotencyConflictError,
    Initiator,
    SubjectLocator,
    SubmissionError,
    SubmissionResult,
    submit_job,
)
from marquee.core.pipeline_config import pipeline_settings
from marquee.core.poster_subjects import MEDIA_TYPE_MOVIE, MEDIA_TYPE_SEASON, PosterSubject
from marquee.database import get_db
from marquee.ml import feedback_store
from marquee.ml.namespaces import TasteNamespace, get_namespace
from marquee.models import MlActivePublication, Movie, PipelineRun, Season, Series
from marquee.pipeline.types import find_auto_pick_candidate

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/feedback", tags=["feedback"])


class FeedbackRequest(BaseModel):
    run_id: str
    action: str  # approve | override | reject_all | rank
    selected_filename: str | None = None
    # action="rank": final order (best -> worst) of orig_filenames still in
    # the orderable list, plus the set thrown into the hate pile (design 30).
    order: list[str] | None = None
    hated: list[str] | None = None
    deploy: bool | None = None  # defaults to FEEDBACK_DEPLOY_DEFAULT
    idempotency_key: str | None = None


class UndoRequest(BaseModel):
    event_id: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _namespace_and_kind(media_type: str) -> tuple[TasteNamespace, str]:
    """Taste namespace + asset_kind for a run's media_type (D4 — one combined
    TV namespace shared by show and season posters, separate from movies)."""
    if media_type == MEDIA_TYPE_MOVIE:
        return get_namespace("movies"), "movie"
    elif media_type == MEDIA_TYPE_SEASON:
        return get_namespace("tv"), "season"
    else:
        return get_namespace("tv"), "show"


def _subject_year(subject: PosterSubject | None) -> int | None:
    if subject is None:
        return None
    if subject.media_type == MEDIA_TYPE_MOVIE:
        return subject.movie.year
    return subject.series.year


def _label_record(
    *,
    candidate: dict,
    label: int,
    action: str,
    role: str,
    event_id: str,
    ts: str,
    run: PipelineRun,
    subject: PosterSubject | None,
    raw: dict | None,
    normalized: dict | None,
    extended: dict | None,
    exemplar_filename: str | None = None,
) -> dict:
    return {
        "v": 2,
        "event_id": event_id,
        "ts": ts,
        "run_id": run.run_id,
        # These four keep their v1/v2 names and movie-only semantics — the
        # head trainer parses them by name; do not rename. TV runs fill the
        # analogous identity via the additive media_type/library/series_id/
        # season_id fields below instead.
        "movie_id": run.movie_id,
        "tmdb_id": subject.tmdb_id if subject else None,
        "title": subject.title if subject else None,
        "year": _subject_year(subject),
        "orig_filename": candidate["orig_filename"],
        "label": label,
        "action": action,
        "role": role,
        "rank": candidate.get("rank"),
        "stage_reached": candidate.get("stage_reached"),
        "rejection_reason": candidate.get("rejection_reason"),
        "scorer_name": run.scorer_name,
        "model_name": pipeline_settings.AI_MODEL,
        "gate_snapshot": feedback_store.gate_snapshot(),
        "raw_features": raw,
        "normalized_features": normalized,
        "extended_features": extended,
        "exemplar_filename": exemplar_filename,
        "media_type": run.media_type,
        "library": "movies" if run.media_type == MEDIA_TYPE_MOVIE else "tv",
        "series_id": run.series_id,
        "season_id": run.season_id,
    }


def _archive_features(candidate: dict) -> tuple[dict | None, dict | None, dict | None]:
    return (
        candidate.get("raw_features"),
        candidate.get("normalized_features"),
        candidate.get("extended_features"),
    )


def _ranking_record_v4(
    *,
    event_id: str,
    ts: str,
    run: PipelineRun,
    subject: PosterSubject | None,
    order_entries: list[dict],
    hated_entries: list[dict],
    favorites_exemplars: list[str],
    negatives_added: list[str],
) -> dict:
    """One self-contained v4 ranking event (see design 30). Stores the final
    drag order + hate set, each candidate's baseline (pipeline) rank and full
    normalized features, so the pairwise trainer never depends on a run's
    working dir surviving. Supersedes the v3 tier/bucket schema — the version
    bump alone obsoletes old rows (abandoned, not migrated)."""
    return {
        "v": 4,
        "type": "ranking",
        "source": "live",
        "event_id": event_id,
        "ts": ts,
        "run_id": run.run_id,
        "movie_id": run.movie_id,
        "tmdb_id": subject.tmdb_id if subject else None,
        "title": subject.title if subject else None,
        "year": _subject_year(subject),
        "order": order_entries,
        "hated": hated_entries,
        "favorites_exemplars": favorites_exemplars,
        "negatives_added": negatives_added,
        "scorer_name": run.scorer_name,
        "model_name": pipeline_settings.AI_MODEL,
        "gate_snapshot": feedback_store.gate_snapshot(),
        "media_type": run.media_type,
        "library": "movies" if run.media_type == MEDIA_TYPE_MOVIE else "tv",
        "series_id": run.series_id,
        "season_id": run.season_id,
    }


def _resolve_pick(by_name: dict, name: str) -> dict | None:
    """Resolve a filename to its candidate dict, remapping a dedup-twin onto
    the survivor it collapsed into (mirrors the override remap)."""
    candidate = by_name.get(name)
    if candidate is None:
        return None
    if (candidate.get("rejection_reason") or "").startswith("dedup_") and candidate.get(
        "dedup_kept"
    ):
        survivor = by_name.get(candidate["dedup_kept"])
        if survivor is not None:
            return survivor
    return candidate


def _normalized_for(candidate: dict) -> dict | None:
    """Return only features frozen by the contained poster execution."""
    _, normalized, _ = _archive_features(candidate)
    return normalized


async def _schedule_learned_head_successor(
    db: AsyncSession,
    namespace: TasteNamespace,
    *,
    feedback_revision: str,
    mutation: str,
) -> dict:
    if not pipeline_settings.HEAD_AUTO_RETRAIN:
        return {"scheduled": False, "reason": "auto-retrain disabled", "job": None}
    generation = await db.scalar(
        select(MlActivePublication.generation).where(
            MlActivePublication.family == f"learned_head:{namespace.library}"
        )
    )
    if db.in_transaction():
        await db.commit()
    async with db.begin():
        result = await submit_job(
            db,
            job_type="learned_head_train",
            request={
                "library": namespace.library,
                "expected_generation": int(generation or 0),
                "seed": 0,
                "feedback_revision": feedback_revision,
                "mutation": mutation,
            },
            subject=SubjectLocator(
                kind="model_profile_training",
                reference=f"learned_head:{namespace.library}",
            ),
            trigger=TriggerKind.MANUAL,
            initiator=Initiator(kind="system", identifier="feedback-api"),
            idempotency_key=(
                f"learned_head_train:{namespace.library}:{mutation}:{feedback_revision}"
            ),
            priority=50,
        )
    return {
        "scheduled": True,
        "reason": "feedback revision committed",
        "job": submission_response(result).model_dump(mode="json"),
    }


async def _deploy_pick(
    db: AsyncSession,
    subject: PosterSubject,
    candidate: PosterCandidateSelectionV1,
    *,
    idempotency_key: str,
) -> SubmissionResult:
    """Snapshot a server-owned selection and submit the canonical deploy leaf."""
    return await submit_poster_leaf(
        db,
        job_type="poster_deploy",
        target_kind=subject.media_type,
        target_id=subject.id,
        request={
            "target_kind": subject.media_type,
            "target_id": subject.id,
            "candidate": candidate.model_dump(mode="json"),
            "ai_selected": True,
            "user_approved": True,
        },
        idempotency_key=poster_child_idempotency_key(
            idempotency_key, f"{subject.media_type}:{subject.id}"
        ),
        initiator="feedback-selection",
    )


async def _load_feedback_subject(
    db: AsyncSession,
    run: PipelineRun,
) -> PosterSubject:
    if run.media_type == MEDIA_TYPE_MOVIE:
        movie = (
            await db.execute(select(Movie).where(Movie.id == run.movie_id))
        ).scalar_one_or_none()
        if movie is None:
            raise HTTPException(status_code=404, detail=f"Movie for run {run.run_id} not found")
        return PosterSubject.from_movie(movie)

    if run.media_type == MEDIA_TYPE_SEASON:
        season = (
            await db.execute(select(Season).where(Season.id == run.season_id))
        ).scalar_one_or_none()
        if season is None:
            raise HTTPException(status_code=404, detail=f"Season for run {run.run_id} not found")
        series = (
            await db.execute(select(Series).where(Series.id == season.series_id))
        ).scalar_one_or_none()
        if series is None:
            raise HTTPException(status_code=404, detail=f"Series for run {run.run_id} not found")
        return PosterSubject.from_season(season, series)

    series = (
        await db.execute(select(Series).where(Series.id == run.series_id))
    ).scalar_one_or_none()
    if series is None:
        raise HTTPException(status_code=404, detail=f"Series for run {run.run_id} not found")
    return PosterSubject.from_series(series)


async def _load_feedback_run(
    db: AsyncSession, run_id: str
) -> tuple[PipelineRun, dict, PosterSubject, dict[str, dict], dict | None]:
    run = (
        await db.execute(select(PipelineRun).where(PipelineRun.run_id == run_id))
    ).scalar_one_or_none()
    if run is None:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")

    archive = await load_pipeline_archive(db, run)
    if archive is None:
        raise HTTPException(status_code=404, detail="Run archive unavailable")

    subject = await _load_feedback_subject(db, run)
    by_name = {c["orig_filename"]: c for c in archive.get("candidates", [])}
    auto = by_name.get(run.auto_pick_filename) if run.auto_pick_filename else None
    if auto is None:
        auto = find_auto_pick_candidate(archive.get("candidates", []))
    return run, archive, subject, by_name, auto


async def apply_feedback_request(
    body: FeedbackRequest,
    _request: Request,
    db: AsyncSession,
) -> dict:
    run, archive, subject, by_name, auto = await _load_feedback_run(db, body.run_id)
    namespace, _ = _namespace_and_kind(run.media_type)
    deploy_requested = (
        pipeline_settings.FEEDBACK_DEPLOY_DEFAULT if body.deploy is None else body.deploy
    )
    if (
        deploy_requested
        and body.action in {"approve", "override", "rank"}
        and body.idempotency_key is None
    ):
        raise HTTPException(status_code=422, detail="idempotency_key is required for deploy")

    event_id = uuid4().hex
    ts = datetime.now(UTC).isoformat()
    records: list[dict] = []
    exemplar_added: str | None = None
    remapped_to: str | None = None
    pick: dict | None = None
    favorites_exemplars: list[str] = []
    negatives_added: list[str] = []
    deployment_candidate: PosterCandidateSelectionV1 | None = None

    if body.action == "reject_all":
        if auto is None:
            raise HTTPException(status_code=400, detail="No auto-pick to reject")
        raw, norm, ext = _archive_features(auto)
        records.append(
            _label_record(
                candidate=auto,
                label=0,
                action="reject_all",
                role="explicit_reject",
                event_id=event_id,
                ts=ts,
                run=run,
                subject=subject,
                raw=raw,
                normalized=norm,
                extended=ext,
            )
        )

    elif body.action in ("approve", "override"):
        if body.action == "approve":
            pick = auto
        else:
            if not body.selected_filename:
                raise HTTPException(status_code=400, detail="override requires selected_filename")
            pick = by_name.get(body.selected_filename)
            if pick is None:
                raise HTTPException(
                    status_code=404,
                    detail=f"{body.selected_filename!r} is not a candidate of this run",
                )
            # Remap a dedup-twin pick onto the survivor it collapsed into.
            if (pick.get("rejection_reason") or "").startswith("dedup_") and pick.get("dedup_kept"):
                survivor = by_name.get(pick["dedup_kept"])
                if survivor is not None:
                    remapped_to = pick["dedup_kept"]
                    pick = survivor

        if pick is None:
            raise HTTPException(status_code=400, detail="No auto-pick available to approve")
        if deploy_requested:
            try:
                deployment_candidate = pipeline_candidate_selection(
                    run,
                    pick,
                    selection_facts={
                        "feedback_action": "approved_selection",
                        "profile_version": run.scorer_name,
                    },
                )
            except PosterSelectionError as exc:
                raise HTTPException(status_code=422, detail="poster_selection_invalid") from exc

        # Override = pairwise: negative for the auto-pick the user passed over.
        if (
            body.action == "override"
            and auto is not None
            and auto["orig_filename"] != pick["orig_filename"]
        ):
            raw, norm, ext = _archive_features(auto)
            records.append(
                _label_record(
                    candidate=auto,
                    label=0,
                    action="override",
                    role="auto_pick",
                    event_id=event_id,
                    ts=ts,
                    run=run,
                    subject=subject,
                    raw=raw,
                    normalized=norm,
                    extended=ext,
                )
            )

        # Positive for the pick. Only execution-frozen features are trainable;
        # an early rejection remains a truthful label without a feature vector.
        raw, norm, ext = _archive_features(pick)

        records.append(
            _label_record(
                candidate=pick,
                label=1,
                action=body.action,
                role="user_pick",
                event_id=event_id,
                ts=ts,
                run=run,
                subject=subject,
                raw=raw,
                normalized=norm,
                extended=ext,
                exemplar_filename=exemplar_added,
            )
        )

    elif body.action == "rank":
        # Dedup while preserving order — the frontend's orderable/hate-pile
        # arrays are disjoint and unique by construction, but don't trust that.
        order_in = list(dict.fromkeys(name for name in (body.order or []) if name))
        hated_in = list(dict.fromkeys(name for name in (body.hated or []) if name))
        if not order_in and not hated_in:
            raise HTTPException(status_code=400, detail="rank requires order and/or hated")
        overlap = set(order_in) & set(hated_in)
        if overlap:
            raise HTTPException(
                status_code=400,
                detail=f"a poster cannot be both ordered and hated: {sorted(overlap)}",
            )

        def _resolve_all(names: list[str]) -> list[dict]:
            resolved = []
            for name in names:
                candidate = _resolve_pick(by_name, name)
                if candidate is None:
                    raise HTTPException(
                        status_code=404,
                        detail=f"{name!r} is not a candidate of this run",
                    )
                resolved.append(candidate)
            return resolved

        order_cands = _resolve_all(order_in)
        hated_cands = _resolve_all(hated_in)

        # order ∪ hated must cover every ranked candidate — there's no third
        # "indifferent" bucket anymore; an untouched candidate just sits in
        # its seeded position in `order`. Silently dropping one here would be
        # indistinguishable from "this candidate never existed" to the
        # inversion math, so it's a 400, not a silent skip.
        covered = {c["orig_filename"] for c in order_cands} | {
            c["orig_filename"] for c in hated_cands
        }
        ranked_filenames = {
            c["orig_filename"] for c in archive.get("candidates", []) if c.get("rank") is not None
        }
        missing = ranked_filenames - covered
        if missing:
            raise HTTPException(
                status_code=400,
                detail=f"order/hated must cover every ranked candidate (missing: {sorted(missing)})",
            )
        pick = order_cands[0] if order_cands else None
        if deploy_requested and pick is not None:
            try:
                deployment_candidate = pipeline_candidate_selection(
                    run,
                    pick,
                    selection_facts={
                        "feedback_action": "ranked_selection",
                        "profile_version": run.scorer_name,
                    },
                )
            except PosterSelectionError as exc:
                raise HTTPException(status_code=422, detail="poster_selection_invalid") from exc

        # Embed each candidate with its execution-frozen normalized features and
        # baseline (pipeline) rank, for the trainer's inversion math.
        async def _entry(candidate: dict) -> dict | None:
            normalized = _normalized_for(candidate)
            if not normalized:
                return None  # nothing trainable — skip (still staged below)
            return {
                "orig_filename": candidate["orig_filename"],
                "pipeline_rank": candidate.get("rank"),
                "baseline_rank": candidate.get("rank"),
                "normalized_features": normalized,
            }

        order_entries: list[dict] = []
        for candidate in order_cands:
            entry = await _entry(candidate)
            if entry is not None:
                order_entries.append(entry)

        hated_entries: list[dict] = []
        for candidate in hated_cands:
            entry = await _entry(candidate)
            if entry is not None:
                hated_entries.append(entry)

        records.append(
            _ranking_record_v4(
                event_id=event_id,
                ts=ts,
                run=run,
                subject=subject,
                order_entries=order_entries,
                hated_entries=hated_entries,
                favorites_exemplars=favorites_exemplars,
                negatives_added=negatives_added,
            )
        )
        exemplar_added = favorites_exemplars[0] if favorites_exemplars else None

    else:
        raise HTTPException(status_code=400, detail=f"Unknown action {body.action!r}")

    feedback_store.append_labels(records, namespace)

    # Deploy the chosen poster to the media folder (approve/override only).
    deployment_job = None
    if deploy_requested and pick is not None:
        if deployment_candidate is None:
            raise HTTPException(status_code=422, detail="poster_selection_invalid")
        try:
            deployment_job = await _deploy_pick(
                db,
                subject,
                deployment_candidate,
                idempotency_key=body.idempotency_key,
            )
        except IdempotencyConflictError as exc:
            raise HTTPException(status_code=409, detail=exc.api_detail) from exc
        except PosterSelectionError as exc:
            raise HTTPException(status_code=422, detail="poster_selection_invalid") from exc
        except SubmissionError as exc:
            raise HTTPException(status_code=422, detail=exc.code) from exc

    # Mark the run reviewed.
    run.feedback_event_id = event_id
    await db.commit()

    head_info = await _schedule_learned_head_successor(
        db,
        namespace,
        feedback_revision=event_id,
        mutation="apply",
    )

    gate_override = _gate_override_for(pick, namespace) if pick is not None else None

    return {
        "event_id": event_id,
        "labels_written": len(records),
        "exemplar_added": exemplar_added,
        "favorites_exemplars": favorites_exemplars,
        "negatives_added": negatives_added,
        "remapped_to": remapped_to,
        "gate_override": gate_override,
        "head": head_info,
        "deployment_job": (
            submission_response(deployment_job).model_dump(mode="json")
            if deployment_job is not None
            else None
        ),
    }


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("")
async def submit_feedback(
    body: FeedbackRequest,
    request: Request,
    response: Response,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    result = await apply_feedback_request(body, request, db)
    if result["deployment_job"] is not None or result["head"]["job"] is not None:
        response.status_code = 202
    return result


@router.post("/undo")
async def undo_feedback(
    body: UndoRequest,
    response: Response,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    runs = (
        (
            await db.execute(
                select(PipelineRun).where(PipelineRun.feedback_event_id == body.event_id)
            )
        )
        .scalars()
        .all()
    )
    namespace = None
    if runs:
        namespace, _ = _namespace_and_kind(runs[0].media_type)
        removed = feedback_store.remove_event(body.event_id, namespace)
    else:
        removed = []
        for library in ("movies", "tv"):
            probe_namespace = get_namespace(library)
            removed = feedback_store.remove_event(body.event_id, probe_namespace)
            if removed:
                namespace = probe_namespace
                break
    if not removed:
        raise HTTPException(status_code=404, detail=f"No labels for event {body.event_id}")

    # Clear the reviewed marker on any run that pointed at this event.
    for run in runs:
        run.feedback_event_id = None
    await db.commit()

    head_info = (
        await _schedule_learned_head_successor(
            db,
            namespace,
            feedback_revision=body.event_id,
            mutation="undo",
        )
        if namespace is not None
        else {"scheduled": False, "reason": "namespace not resolved", "job": None}
    )
    if head_info["job"] is not None:
        response.status_code = 202
    return {
        "removed_labels": len(removed),
        "exemplars_removed": [],
        "negatives_removed": [],
        "head": head_info,
    }


def _gate_override_for(pick: dict, namespace: TasteNamespace) -> dict | None:
    reason = (pick.get("rejection_reason") or "").split(":", 1)[0]
    if reason not in feedback_store.GATE_REASON_KNOBS:
        return None
    alerts = {a["gate"]: a for a in feedback_store.gate_override_alerts(namespace)}
    alert = alerts.get(reason)
    return {
        "reason": reason,
        "count_at_current_threshold": alert["overrides"] if alert else 1,
    }
