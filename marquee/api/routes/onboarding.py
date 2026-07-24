"""Canonical cold-start onboarding over ordinary analysis and deployment jobs."""

from __future__ import annotations

import logging
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from marquee.api.job_submission import submission_response
from marquee.api.routes.pipeline import _downloaded
from marquee.core.jobs.contracts import TriggerKind
from marquee.core.jobs.control import JobControlError, retry
from marquee.core.jobs.submission import (
    Initiator,
    SubjectLocator,
    SubmissionResult,
    submit_job,
)
from marquee.core.onboarding_review import (
    OnboardingReviewError,
    bind_onboarding_decision,
    load_onboarding_review,
)
from marquee.core.taste_preferences import (
    TastePreferenceError,
    derive_readiness,
    reconcile_pending_onboarding_deployments,
    record_onboarding_analysis_submission,
    schedule_initial_profile_build,
    snapshot_profile_revision,
)
from marquee.database import get_db
from marquee.models import (
    Job,
    Movie,
    OnboardingAnalysisSuccessor,
    PipelineRun,
    TasteDeploymentSuccessor,
    TasteExemplar,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/onboarding", tags=["onboarding"])


class StartRequest(BaseModel):
    pass


class ChoosePosterRequest(BaseModel):
    run_id: str = Field(min_length=1, max_length=80)
    candidate_id: str = Field(min_length=1, max_length=64, pattern=r"^[a-f0-9]+$")
    review_revision: str = Field(min_length=64, max_length=64, pattern=r"^[a-f0-9]{64}$")
    idempotency_key: str = Field(min_length=1, max_length=120)


class HatePosterRequest(ChoosePosterRequest):
    pass


def _review_http_error(exc: OnboardingReviewError) -> HTTPException:
    message = str(exc)
    if "unavailable" in message and "candidate" not in message:
        return HTTPException(status_code=404, detail=message)
    if "conflict" in message or "changed" in message:
        return HTTPException(status_code=409, detail=message)
    return HTTPException(status_code=400, detail=message)


async def _downloaded_movies(db: AsyncSession) -> list[tuple[int, list[str] | None]]:
    rows = (
        await db.execute(
            select(Movie.id, Movie.genres).where(Movie.tmdb_id.is_not(None), _downloaded())
        )
    ).all()
    return [(row[0], row[1]) for row in rows]


def _job_lineage_item(job: Job, *, predecessor_job_id: str | None) -> dict[str, object]:
    return {
        "job_id": job.id,
        "predecessor_job_id": predecessor_job_id,
        "phase": job.phase,
        "outcome": job.outcome,
        "fence_token": job.fence_token,
        "retryable": job.phase == "terminal" and job.outcome in {"failed", "cancelled"},
        "activity_link": f"/projection-room/jobs/{job.id}",
    }


def _submission_result(job: Job, *, disposition: Literal["created", "reused"]) -> SubmissionResult:
    """Present an existing canonical job through the ordinary submission contract."""
    return SubmissionResult(
        job_id=job.id,
        disposition=disposition,
        phase=job.phase,
        snapshot_link=f"/api/jobs/{job.id}/snapshot",
        detail_link=f"/projection-room/jobs/{job.id}",
        activity_link=f"/projection-room?view=queue&job={job.id}",
        idempotent=disposition == "reused",
    )


async def _onboarding_lineage_status(db: AsyncSession) -> dict[str, list[dict[str, object]]]:
    analyses = list(
        await db.scalars(
            select(OnboardingAnalysisSuccessor)
            .join(Job, Job.id == OnboardingAnalysisSuccessor.job_id)
            .order_by(OnboardingAnalysisSuccessor.created_at, OnboardingAnalysisSuccessor.job_id)
        )
    )
    deployments = list(
        await db.scalars(
            select(TasteDeploymentSuccessor)
            .join(Job, Job.id == TasteDeploymentSuccessor.job_id)
            .order_by(TasteDeploymentSuccessor.created_at, TasteDeploymentSuccessor.ordinal)
        )
    )
    analysis_items: list[dict[str, object]] = []
    for row in analyses:
        job = await db.get(Job, row.job_id)
        if job is not None:
            analysis_items.append(_job_lineage_item(job, predecessor_job_id=row.predecessor_job_id))
    deployment_items: list[dict[str, object]] = []
    for row in deployments:
        job = await db.get(Job, row.job_id)
        if job is None:
            continue
        item = _job_lineage_item(job, predecessor_job_id=row.predecessor_job_id)
        item.update(
            {
                "exemplar_id": row.exemplar_id,
                "ordinal": row.ordinal,
                "state": row.state,
                "post_effect_validation": row.post_effect_validation,
            }
        )
        deployment_items.append(item)
    return {"analysis": analysis_items, "deployment": deployment_items}


@router.get("/status")
async def onboarding_status(db: Annotated[AsyncSession, Depends(get_db)]):
    await reconcile_pending_onboarding_deployments(db)
    await db.commit()
    readiness = await derive_readiness(db)
    review = await db.scalar(
        select(PipelineRun)
        .join(Job, Job.id == PipelineRun.job_id)
        .where(
            PipelineRun.media_type == "movie",
            PipelineRun.status.in_(("completed", "flagged_manual")),
            PipelineRun.feedback_event_id.is_(None),
            Job.initiator["identifier"].as_string() == "onboarding-api",
        )
        .order_by(PipelineRun.completed_at.desc().nullslast(), PipelineRun.run_id.desc())
        .limit(1)
    )
    active_jobs = list(
        (
            await db.scalars(
                select(Job)
                .where(
                    Job.type.in_(("poster_pipeline", "poster_deploy", "taste_rebuild")),
                    Job.phase != "terminal",
                )
                .order_by(Job.created_at.desc())
                .limit(20)
            )
        ).all()
    )
    lineage = await _onboarding_lineage_status(db)
    return {
        **readiness.to_dict(),
        "active_jobs": [
            {
                "job_id": row.id,
                "job_type": row.type,
                "phase": row.phase,
                "subject_kind": row.subject_kind,
                "subject_reference": row.subject_reference,
                "activity_link": f"/projection-room?view=queue&job={row.id}",
            }
            for row in active_jobs
        ],
        "review": (
            {
                "run_id": review.run_id,
                "analysis_job_id": review.job_id,
                "url": f"/onboarding?review={review.run_id}",
            }
            if review is not None
            else None
        ),
        "lineage": lineage,
    }


@router.post("/start")
async def onboarding_start(
    db: Annotated[AsyncSession, Depends(get_db)],
    body: StartRequest | None = None,
):
    _body = body or StartRequest()
    downloaded = await _downloaded_movies(db)
    if not downloaded:
        raise HTTPException(
            status_code=400,
            detail="sync or connect a movie library before choosing poster examples",
        )
    confirmed = set(
        await db.scalars(
            select(TasteExemplar.subject_reference).where(
                TasteExemplar.namespace == "global",
                TasteExemplar.status.in_(("pending_deploy", "active")),
                TasteExemplar.polarity == "positive",
            )
        )
    )
    movie_id = next((row[0] for row in downloaded if str(row[0]) not in confirmed), None)
    if movie_id is None:
        raise HTTPException(status_code=409, detail="all downloaded movies are already confirmed")
    movie = await db.get(Movie, movie_id)
    assert movie is not None
    subject = {"kind": "movie", "id": movie.id, "title": movie.title, "year": movie.year}
    previous = await db.scalar(
        select(OnboardingAnalysisSuccessor)
        .join(Job, Job.id == OnboardingAnalysisSuccessor.job_id)
        .where(
            OnboardingAnalysisSuccessor.subject_kind == "movie",
            OnboardingAnalysisSuccessor.subject_reference == str(movie.id),
        )
        .order_by(OnboardingAnalysisSuccessor.created_at.desc())
        .limit(1)
    )
    previous_job = await db.get(Job, previous.job_id) if previous is not None else None
    if previous_job is not None and previous_job.phase == "terminal":
        if previous_job.outcome not in {"failed", "cancelled"}:
            raise HTTPException(
                status_code=409,
                detail="the latest onboarding analysis is terminal; review its recorded outcome first",
            )
        previous_job_id = previous_job.id
        previous_fence_token = previous_job.fence_token
        await db.rollback()
        try:
            retried = await retry(
                db,
                job_id=previous_job_id,
                expected_fence_token=previous_fence_token,
            )
        except JobControlError as exc:
            raise HTTPException(status_code=409, detail=exc.message) from exc
        submission = _submission_result(retried.job, disposition="created")
    elif previous_job is not None:
        submission = _submission_result(previous_job, disposition="reused")
        await db.commit()
    else:
        submission = await submit_job(
            db,
            job_type="poster_pipeline",
            request={
                "movie_id": movie.id,
                "tmdb_id": movie.tmdb_id,
                "title": movie.title,
                "source_descriptors": [
                    {"provider": "tmdb", "reference": f"movie:{movie.tmdb_id}"}
                ],
            },
            subject=SubjectLocator(kind="movie", reference=str(movie.id)),
            trigger=TriggerKind.MANUAL,
            initiator=Initiator(kind="user", identifier="onboarding-api"),
            idempotency_key=f"poster_pipeline:onboarding:{movie.id}",
            priority=80,
        )
        try:
            await record_onboarding_analysis_submission(
                db,
                job_id=submission.job_id,
                subject_kind="movie",
                subject_reference=str(movie.id),
            )
        except TastePreferenceError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        await db.commit()
    return {
        "subject": subject,
        "analysis_job": submission_response(submission),
        "status": await onboarding_status(db),
    }


@router.get("/runs/{run_id}/review")
async def onboarding_review(run_id: str, db: Annotated[AsyncSession, Depends(get_db)]):
    """Render neutral choice cards from the verified terminal run archive."""
    try:
        return await load_onboarding_review(db, run_id)
    except OnboardingReviewError as exc:
        raise _review_http_error(exc) from exc


async def _decision_response(
    body: ChoosePosterRequest,
    db: AsyncSession,
    *,
    decision: Literal["choose", "hate"],
):
    try:
        bound = await bind_onboarding_decision(
            db,
            run_id=body.run_id,
            candidate_id=body.candidate_id,
            review_revision=body.review_revision,
            idempotency_key=body.idempotency_key,
            decision=decision,
        )
        await db.commit()
    except OnboardingReviewError as exc:
        await db.rollback()
        raise _review_http_error(exc) from exc
    return {
        "decision": decision,
        "event_id": bound["event_id"],
        "exemplar_id": bound["exemplar_id"],
        "deployment_job_id": bound["deployment_job_id"],
        "disposition": bound["disposition"],
        "status": await onboarding_status(db),
    }


@router.post("/choose")
async def onboarding_choose(
    body: ChoosePosterRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    return await _decision_response(body, db, decision="choose")


@router.post("/hate")
async def onboarding_hate(
    body: HatePosterRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    return await _decision_response(body, db, decision="hate")


@router.post("/complete")
async def onboarding_complete(db: Annotated[AsyncSession, Depends(get_db)]):
    readiness = await derive_readiness(db)
    if readiness.active_positive_subjects < readiness.thresholds.required:
        raise HTTPException(
            status_code=400,
            detail=(
                f"need {readiness.thresholds.required} successfully deployed subjects; "
                f"have {readiness.active_positive_subjects}"
            ),
        )
    revision = await snapshot_profile_revision(db, namespaces=("global",))
    builds = await schedule_initial_profile_build(
        db, initiator_identifier="onboarding-api"
    )
    await db.commit()
    return {
        "revision": revision.digest,
        "build_jobs": [submission_response(build) for build in builds],
        "status": await onboarding_status(db),
    }
