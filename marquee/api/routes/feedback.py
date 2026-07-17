"""Feedback routes — approve / override / reject, plus undo (design 09).

Every submission maps to the scenarios in design 09 §3–6:

  - approve     → 1 positive label for the auto-pick (scenario A)
  - override    → negative for the auto-pick + positive for the user's pick
                  (scenarios B/C); retro features + gate snapshot if the pick
                  was rejected before its features were computed
  - reject_all  → 1 ``explicit_reject`` negative for the auto-pick (scenario D)

A single ``event_id`` ties together all rows a submission writes, so undo is
exact. Approve/override append the chosen poster to the taste profile and
(when thresholds are met) retrain the learned head.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from marquee.api.job_submission import submission_response
from marquee.core.jobs.contracts import TriggerKind
from marquee.core.jobs.mutation_documents import PosterCandidateSelectionV1
from marquee.core.jobs.poster_submission import (
    PosterSelectionError,
    pipeline_candidate_selection,
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
from marquee.ml import feedback_store, profile_updater
from marquee.ml.namespaces import TasteNamespace, get_namespace
from marquee.models import MlActivePublication, Movie, PipelineRun, Season, Series
from marquee.pipeline.features import load_cached_embedding
from marquee.pipeline.run_manager import run_manager
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


async def _normalized_for(
    request: Request, run: PipelineRun, candidate: dict, subject: PosterSubject | None
) -> dict | None:
    """Normalized features for a candidate, backfilled via retro features when
    it was rejected before the style stage (same as the override path)."""
    _, normalized, _ = _archive_features(candidate)
    if normalized is None:
        _, normalized, _ = await _retro_features(
            request=request, run=run, candidate=candidate, subject=subject
        )
    return normalized


async def _retro_features(
    *,
    request: Request,
    run: PipelineRun,
    candidate: dict,
    subject: PosterSubject | None,
) -> tuple[dict | None, dict | None, dict | None]:
    """Backfill features for a pick that was rejected before detail features."""
    orig = candidate["orig_filename"]
    originals = Path(run.output_dir or "") / "0-originals" / orig
    if not originals.is_file():
        logger.warning("RETRO | no source image for %s — label written without features", orig)
        return None, None, None

    from marquee.core.poster_sources.tmdb import PosterCandidate  # noqa: PLC0415
    from marquee.pipeline.retro_features import (  # noqa: PLC0415
        candidate_from_image,
        compute_full_features,
    )

    movie = subject.movie if subject and subject.media_type == MEDIA_TYPE_MOVIE else None
    poster_candidate: PosterCandidate | None = None
    tmdb = getattr(request.app.state, "tmdb_client", None)
    if tmdb is not None and movie is not None and movie.tmdb_id is not None:
        try:
            images = await tmdb.get_movie_images(movie.tmdb_id)
            poster_candidate = next(
                (c for c in images if c.file_path.lstrip("/").split("/")[-1] == orig),
                None,
            )
        except Exception as exc:  # noqa: BLE001 — fall back to image-only metadata
            logger.warning("RETRO | TMDB lookup failed (%s) — using image dims", exc)
    if poster_candidate is None:
        poster_candidate = candidate_from_image(originals)

    # OCR title tokens use the bare series title for TV (no season suffix),
    # matching how the batch runner feeds title tokens for TV assets (§9.2).
    if subject is None:
        title = ""
    elif subject.media_type == MEDIA_TYPE_MOVIE:
        title = subject.movie.title
    else:
        title = subject.series.title

    def _compute():
        extractor = run_manager._ensure_extractor()
        return compute_full_features(
            image_path=originals,
            candidate=poster_candidate,
            movie_title=title,
            extractor=extractor,
        )

    features = await asyncio.to_thread(_compute)
    return features.raw_values(), features.normalized, features.extended


def _add_to_profile(
    orig_filename: str,
    image_path: Path,
    subject: PosterSubject | None,
    namespace: TasteNamespace,
    asset_kind: str,
) -> str | None:
    """Append the pick to the taste profile; returns the exemplar filename."""
    embedding = load_cached_embedding(orig_filename)
    if embedding is None:
        logger.warning(
            "PROFILE | no cached embedding for %s — skipping exemplar add", orig_filename
        )
        return None
    source = image_path if image_path.is_file() else None
    if source is None:
        logger.warning("PROFILE | no source image for %s — skipping exemplar add", orig_filename)
        return None
    return profile_updater.add_exemplar(
        source_image=source,
        title=subject.title if subject else orig_filename,
        year=_subject_year(subject),
        clip_embedding=embedding,
        namespace=namespace,
        asset_kind=asset_kind,
    )


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
        idempotency_key=idempotency_key,
        initiator="feedback-selection",
    )


async def _load_feedback_subject(
    db: AsyncSession,
    run: PipelineRun,
) -> PosterSubject:
    if run.media_type == MEDIA_TYPE_MOVIE:
        movie = (await db.execute(select(Movie).where(Movie.id == run.movie_id))).scalar_one_or_none()
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

    series = (await db.execute(select(Series).where(Series.id == run.series_id))).scalar_one_or_none()
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

    archive = run_manager.load_archive(run_id, run.archive_path)
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
    request: Request,
    db: AsyncSession,
) -> dict:
    run, archive, subject, by_name, auto = await _load_feedback_run(db, body.run_id)
    namespace, asset_kind = _namespace_and_kind(run.media_type)
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

        # Positive for the pick — backfill features if it was rejected early.
        raw, norm, ext = _archive_features(pick)
        if norm is None:
            raw, norm, ext = await _retro_features(
                request=request, run=run, candidate=pick, subject=subject
            )

        # Add to the taste profile (source = the recorded image, original-res
        # for ranked picks).
        image_path = Path(pick.get("image_path") or "")
        exemplar_added = await asyncio.to_thread(
            _add_to_profile,
            pick["orig_filename"],
            image_path,
            subject,
            namespace,
            asset_kind,
        )

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

        # Optional: the overridden auto-pick joins the negative exemplars.
        if (
            body.action == "override"
            and pipeline_settings.FEEDBACK_NEGATIVES_FROM_OVERRIDES
            and auto is not None
        ):
            await asyncio.to_thread(_copy_negative, auto, namespace)

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

        # Embed each candidate with its (backfilled) normalized features and
        # baseline (pipeline) rank, for the trainer's inversion math.
        async def _entry(candidate: dict) -> dict | None:
            normalized = await _normalized_for(request, run, candidate, subject)
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

        # Channel 1 (positive exemplars): top slice of the final order.
        n_pos = feedback_store.positive_exemplar_count(len(order_cands))
        for candidate in order_cands[:n_pos]:
            added = await asyncio.to_thread(
                _add_to_profile,
                candidate["orig_filename"],
                Path(candidate.get("image_path") or ""),
                subject,
                namespace,
                asset_kind,
            )
            if added:
                favorites_exemplars.append(added)

        # Channel 1 (hard negatives): hated posters the pipeline ranked high.
        rank_max = pipeline_settings.FEEDBACK_HARD_NEGATIVE_RANK_MAX
        for candidate in hated_cands:
            rank = candidate.get("rank")
            if rank is not None and rank <= rank_max:
                added = await asyncio.to_thread(_copy_negative, candidate, namespace)
                if added:
                    negatives_added.append(added)

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

    # Profile changed → next pipeline run must reload the taste store.
    if exemplar_added is not None:
        run_manager.reset_extractor()

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

    removed_exemplars: list[str] = []
    removed_negatives: list[str] = []
    for row in removed:
        # v2 approve/override carry one exemplar; v3 ranking events carry lists
        # of added positive exemplars + hard-negative files.
        exemplar_names = [row.get("exemplar_filename"), *(row.get("favorites_exemplars") or [])]
        for exemplar in exemplar_names:
            if exemplar and await asyncio.to_thread(
                profile_updater.remove_exemplar, exemplar, namespace
            ):
                removed_exemplars.append(exemplar)
        for negative in row.get("negatives_added") or []:
            if await asyncio.to_thread(_remove_negative, negative, namespace):
                removed_negatives.append(negative)

    if removed_exemplars:
        run_manager.reset_extractor()

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
        "exemplars_removed": removed_exemplars,
        "negatives_removed": removed_negatives,
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


def _copy_negative(candidate: dict, namespace: TasteNamespace) -> str | None:
    """Copy a disliked poster into NEGATIVE_DATA_DIR. Returns the filename
    added (for undo), or None if the source was missing or already present."""
    from marquee.config import settings  # noqa: PLC0415
    from marquee.core.filesystem import (  # noqa: PLC0415
        FilesystemBoundary,
        FilesystemBoundaryError,
        RootSpec,
    )

    source = Path(candidate.get("image_path") or "")
    dest_dir = namespace.negative_dir
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / source.name
    if dest.exists():
        return None
    roots = {
        "runs": RootSpec("runs", settings.runs_work_path, "pipeline-run"),
        "negative": RootSpec("negative", dest_dir, "negative-exemplar", access="read_write"),
    }
    boundary = FilesystemBoundary(roots)
    try:
        source_file = boundary.classify(source, roots=("runs",), require_file=True)
        destination = boundary.classify(
            dest, roots=("negative",), require_exists=False, write=True
        )
        boundary.copy_file(source_file, destination)
    except FilesystemBoundaryError:
        return None
    logger.info("NEGATIVE | added %s to negative exemplars", source.name)
    return dest.name


def _remove_negative(filename: str, namespace: TasteNamespace | None) -> bool:
    """Remove a negative exemplar file added by a ranking event (undo)."""
    if namespace is None:
        return False
    from marquee.core.filesystem import FilesystemBoundaryError, boundary_for_roots  # noqa: PLC0415

    boundary = boundary_for_roots(
        {"negative": namespace.negative_dir}, access="read_write", purpose="negative-exemplar"
    )
    target = namespace.negative_dir / filename
    try:
        classified = boundary.classify(target, require_file=True, write=True)
        boundary.delete_file(classified, missing_ok=False)
        logger.info("NEGATIVE | removed %s", filename)
        return True
    except (FilesystemBoundaryError, FileNotFoundError):
        return False
