"""Canonical, bounded onboarding review and intent binding.

The browser receives a neutral projection of a terminal pipeline archive.  It never supplies
candidate membership, artifact identity, presentation order, storage keys, or evidence facts.
"""

from __future__ import annotations

import json
import re
from hashlib import sha256
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from marquee.core.jobs.artifact_service import ArtifactError, verify_physical_artifact
from marquee.core.jobs.contracts import TriggerKind
from marquee.core.jobs.pipeline_archives import load_pipeline_archive
from marquee.core.jobs.poster_submission import PosterSelectionError, pipeline_candidate_selection
from marquee.core.jobs.submission import Initiator, SubjectLocator, submit_job
from marquee.core.taste_preferences import (
    TastePreferenceError,
    append_preference_event,
    create_active_negative_exemplar,
    create_pending_exemplar,
    pin_candidate_artifact,
)
from marquee.models import JobArtifact, Movie, PipelineRun, PosterPreferenceEvent, TasteExemplar

_MAX_REVIEW_CANDIDATES = 100


class OnboardingReviewError(ValueError):
    """A requested onboarding review or decision cannot be safely reconstructed."""


def _candidate_id(run_id: str, reference: str) -> str:
    return sha256(f"{run_id}:{reference}".encode()).hexdigest()[:32]


def _review_revision(run: PipelineRun, candidates: list[dict[str, Any]]) -> str:
    document = {
        "version": 1,
        "run_id": run.run_id,
        "archive_artifact_id": run.archive_artifact_id,
        "candidates": [
            {
                "candidate_id": item["candidate_id"],
                "artifact_id": item["artifact_id"],
                "checksum": item["checksum"],
            }
            for item in candidates
        ],
    }
    return sha256(json.dumps(document, separators=(",", ":"), sort_keys=True).encode()).hexdigest()


async def _archive_candidates(
    session: AsyncSession, run: PipelineRun
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if run.status not in {"completed", "flagged_manual"}:
        raise OnboardingReviewError("analysis is not available for review")
    try:
        archive = await load_pipeline_archive(session, run)
    except ArtifactError as exc:
        raise OnboardingReviewError("canonical run archive is unavailable") from exc
    if archive is None:
        raise OnboardingReviewError("canonical run archive is unavailable")
    raw_candidates = archive.get("candidates")
    if not isinstance(raw_candidates, list):
        raise OnboardingReviewError("canonical run archive has no review candidates")

    review: list[dict[str, Any]] = []
    canonical: list[dict[str, Any]] = []
    seen_references: set[str] = set()
    for candidate in raw_candidates[:_MAX_REVIEW_CANDIDATES]:
        if not isinstance(candidate, dict):
            continue
        reference = candidate.get("orig_filename")
        artifact_id = candidate.get("artifact_id")
        artifact_key = candidate.get("artifact_storage_key")
        checksum = candidate.get("artifact_checksum")
        if (
            not isinstance(reference, str)
            or not re.fullmatch(r"[A-Za-z0-9._-]+", reference)
            or not isinstance(artifact_id, int)
            or not isinstance(artifact_key, str)
            or not isinstance(checksum, str)
            or reference in seen_references
        ):
            continue
        artifact = await session.get(JobArtifact, artifact_id)
        metadata = artifact.artifact_metadata if artifact is not None else None
        if (
            artifact is None
            or artifact.job_id != run.job_id
            or artifact.kind != "evidence_image"
            or artifact.status != "available"
            or artifact.storage_key is None
            or artifact.storage_key != artifact_key
            or artifact.checksum != checksum
            or not isinstance(metadata, dict)
            or metadata.get("family") != "poster_pipeline"
            or metadata.get("role") != "review_candidate"
            or metadata.get("candidate_reference") != reference
        ):
            continue
        try:
            await verify_physical_artifact(artifact)
        except ArtifactError:
            continue
        seen_references.add(reference)
        candidate_id = _candidate_id(run.run_id, reference)
        canonical.append(
            {
                "candidate_id": candidate_id,
                "reference": reference,
                "artifact_id": artifact.id,
                "checksum": artifact.checksum,
                "candidate": candidate,
                "artifact": artifact,
            }
        )
        review.append(
            {
                "candidate_id": candidate_id,
                "image_url": f"/api/pipeline/runs/{run.run_id}/posters/{reference}",
                "source": str(candidate.get("source") or "Poster source"),
                "eligibility": {
                    "status": "survived_objective_filters",
                    "ocr": candidate.get("ocr_summary") or candidate.get("ocr") or None,
                },
                "facts": {
                    key: candidate[key]
                    for key in ("width", "height", "language")
                    if isinstance(candidate.get(key), (str, int, float, bool))
                },
            }
        )
    if not canonical:
        raise OnboardingReviewError("canonical review candidates are unavailable")
    return canonical, review


async def load_onboarding_review(session: AsyncSession, run_id: str) -> dict[str, Any]:
    """Return a neutral, bounded review projection from verified immutable evidence."""
    run = await session.get(PipelineRun, run_id)
    if run is None:
        raise OnboardingReviewError("pipeline run is unavailable")
    canonical, review = await _archive_candidates(session, run)
    revision = _review_revision(run, canonical)
    return {
        "version": 1,
        "run_id": run.run_id,
        "analysis_job_id": run.job_id,
        "status": run.status,
        "subject": run.subject_snapshot,
        "review_revision": revision,
        "candidates": review,
        "rejections": {"available": True},
        "allowed_actions": {
            "choose": run.feedback_event_id is None,
            "hate": run.feedback_event_id is None,
        },
        "links": {
            "activity": f"/projection-room?view=queue&job={run.job_id}",
            "detail": f"/projection-room/jobs/{run.job_id}",
            "run": f"/api/pipeline/runs/{run.run_id}",
        },
    }


async def _decision_context(
    session: AsyncSession,
    *,
    run_id: str,
    candidate_id: str,
    review_revision: str,
) -> tuple[PipelineRun, dict[str, Any], dict[str, Any], str]:
    run = await session.scalar(
        select(PipelineRun).where(PipelineRun.run_id == run_id).with_for_update()
    )
    if run is None:
        raise OnboardingReviewError("pipeline run is unavailable")
    canonical, _review = await _archive_candidates(session, run)
    actual_revision = _review_revision(run, canonical)
    if review_revision != actual_revision:
        raise OnboardingReviewError("review has changed; reload the candidate choices")
    selected = next((item for item in canonical if item["candidate_id"] == candidate_id), None)
    if selected is None:
        raise OnboardingReviewError("selected candidate was not presented for this run")
    order = [item["candidate_id"] for item in canonical]
    return run, selected, {"order": order, "revision": actual_revision}, actual_revision


async def _existing_decision(
    session: AsyncSession,
    *,
    idempotency_key: str,
    run: PipelineRun,
    selected: dict[str, Any],
    action: Literal["selection", "hate"],
) -> tuple[PosterPreferenceEvent | None, TasteExemplar | None]:
    event = await session.scalar(
        select(PosterPreferenceEvent).where(PosterPreferenceEvent.idempotency_key == idempotency_key)
    )
    if event is None:
        return None, None
    if (
        event.action != action
        or event.pipeline_run_id != run.run_id
        or event.candidate_artifact_id != selected["artifact_id"]
    ):
        raise OnboardingReviewError("idempotency key conflicts with an existing decision")
    exemplar = await session.scalar(
        select(TasteExemplar).where(TasteExemplar.preference_event_id == event.id)
    )
    return event, exemplar


async def bind_onboarding_decision(
    session: AsyncSession,
    *,
    run_id: str,
    candidate_id: str,
    review_revision: str,
    idempotency_key: str,
    decision: Literal["choose", "hate"],
) -> dict[str, Any]:
    """Atomically bind intent to canonical review evidence and create the requested projection."""
    run, selected, review, _actual_revision = await _decision_context(
        session,
        run_id=run_id,
        candidate_id=candidate_id,
        review_revision=review_revision,
    )
    action: Literal["selection", "hate"] = "selection" if decision == "choose" else "hate"
    existing, existing_exemplar = await _existing_decision(
        session,
        idempotency_key=f"onboarding-{action}:{idempotency_key}",
        run=run,
        selected=selected,
        action=action,
    )
    if existing is not None:
        return {
            "event_id": existing.id,
            "exemplar_id": existing_exemplar.id if existing_exemplar is not None else None,
            "deployment_job_id": existing_exemplar.deployment_job_id
            if existing_exemplar is not None
            else None,
            "disposition": "reused",
        }
    if run.feedback_event_id is not None:
        raise OnboardingReviewError("this review already has a recorded decision")

    subject_reference = str(run.movie_id) if run.movie_id is not None else ""
    if not subject_reference:
        raise OnboardingReviewError("pipeline run is not a movie onboarding subject")
    subject_snapshot = run.subject_snapshot if isinstance(run.subject_snapshot, dict) else {}
    exposed = [
        {
            "candidate_id": item_id,
            "position": index,
        }
        for index, item_id in enumerate(review["order"])
    ]
    event = await append_preference_event(
        session,
        idempotency_key=f"onboarding-{action}:{idempotency_key}",
        namespace="global",
        subject_kind="movie",
        subject_reference=subject_reference,
        subject_snapshot=subject_snapshot,
        action=action,
        exposed_candidates=exposed,
        presentation_order=review["order"],
        training_context={
            "personalization_mode": "collecting",
            "neutral_onboarding": True,
            "review_revision": review["revision"],
            "selected_candidate": selected["candidate_id"],
        },
        confidence="explicit",
        initiator={"kind": "user", "identifier": "onboarding-api"},
        pipeline_run_id=run.run_id,
        candidate_artifact_id=selected["artifact_id"],
    )
    run.feedback_event_id = event.id
    if decision == "choose":
        movie = await session.get(Movie, run.movie_id)
        if movie is None:
            raise OnboardingReviewError("movie is unavailable; review evidence remains recoverable")
        try:
            candidate = pipeline_candidate_selection(
                run,
                selected["candidate"],
                selection_facts={"personalization_mode": "collecting"},
            )
        except PosterSelectionError as exc:
            raise OnboardingReviewError("selected canonical candidate is unavailable") from exc
        submission = await submit_job(
            session,
            job_type="poster_deploy",
            request={
                "target_kind": "movie",
                "target_id": movie.id,
                "candidate": candidate.model_dump(mode="json"),
                "ai_selected": False,
                "user_approved": True,
            },
            subject=SubjectLocator(kind="movie", reference=str(movie.id)),
            trigger=TriggerKind.MANUAL,
            initiator=Initiator(kind="user", identifier="onboarding-api"),
            idempotency_key=f"poster_deploy:onboarding:{idempotency_key}",
            priority=90,
        )
        exemplar = await create_pending_exemplar(
            session,
            event=event,
            polarity="positive",
            evidence_source="explicit_selection",
            evidence_weight=1.0,
            deployment_job_id=submission.job_id,
        )
        return {
            "event_id": event.id,
            "exemplar_id": exemplar.id,
            "deployment_job_id": submission.job_id,
            "disposition": submission.disposition,
        }

    artifact = selected["artifact"]
    try:
        pinned = await pin_candidate_artifact(
            artifact,
            deployment_job_id=run.job_id,
            deployment_attempt_id=run.attempt_id,
            deployment_fence_token=run.fence_token,
        )
        exemplar = await create_active_negative_exemplar(
            session,
            event=event,
            retained_artifact=pinned,
            evidence_source="explicit_hate",
            evidence_weight=1.0,
        )
    except (ArtifactError, TastePreferenceError) as exc:
        raise OnboardingReviewError("selected canonical candidate is unavailable") from exc
    return {
        "event_id": event.id,
        "exemplar_id": exemplar.id,
        "deployment_job_id": None,
        "disposition": "created",
    }
