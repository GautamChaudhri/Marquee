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
from marquee.core.jobs.submission import (
    Initiator,
    SubjectLocator,
    submit_job,
)
from marquee.core.onboarding_review import (
    OnboardingReviewError,
    bind_onboarding_decision,
    load_onboarding_review,
)
from marquee.core.taste_preferences import (
    derive_readiness,
    schedule_initial_profile_build,
    snapshot_profile_revision,
)
from marquee.database import get_db
from marquee.models import Job, Movie, PipelineRun, TasteExemplar

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


@router.get("/status")
async def onboarding_status(db: Annotated[AsyncSession, Depends(get_db)]):
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
    await db.commit()
    return {
        "subject": {"kind": "movie", "id": movie.id, "title": movie.title, "year": movie.year},
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
