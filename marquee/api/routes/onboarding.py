"""Canonical cold-start onboarding over ordinary analysis and deployment jobs."""

from __future__ import annotations

import logging
from typing import Annotated

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
from marquee.core.taste_preferences import (
    append_preference_event,
    create_pending_exemplar,
    derive_readiness,
    schedule_initial_profile_build,
    snapshot_profile_revision,
)
from marquee.database import get_db
from marquee.models import Job, JobArtifact, Movie, PipelineRun, TasteExemplar

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/onboarding", tags=["onboarding"])


class StartRequest(BaseModel):
    pass


class ChoosePosterRequest(BaseModel):
    movie_id: int = Field(ge=1)
    artifact_id: int = Field(ge=1)
    run_id: str = Field(min_length=1, max_length=80)
    candidate_reference: str = Field(min_length=1, max_length=160)
    presentation_order: list[str] = Field(min_length=1, max_length=100)
    idempotency_key: str = Field(min_length=1, max_length=120)


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
                "activity_link": f"/activity?job={row.id}",
            }
            for row in active_jobs
        ],
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
        "status": (await derive_readiness(db)).to_dict(),
    }


@router.post("/choose")
async def onboarding_choose(
    body: ChoosePosterRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    movie = await db.get(Movie, body.movie_id)
    artifact = await db.get(JobArtifact, body.artifact_id)
    pipeline_run = await db.get(PipelineRun, body.run_id)
    if movie is None:
        raise HTTPException(status_code=404, detail="movie is unavailable")
    if (
        artifact is None
        or artifact.status != "available"
        or artifact.kind != "evidence_image"
        or not artifact.storage_key
        or not artifact.checksum
        or pipeline_run is None
        or pipeline_run.movie_id != body.movie_id
        or pipeline_run.job_id != artifact.job_id
        or body.candidate_reference not in body.presentation_order
    ):
        raise HTTPException(status_code=400, detail="candidate artifact is unavailable")
    submission = await submit_job(
        db,
        job_type="poster_deploy",
        request={
            "target_kind": "movie",
            "target_id": movie.id,
            "candidate": {
                "source": "pipeline_run",
                "storage_key": artifact.storage_key,
                "artifact_id": artifact.id,
                "run_id": body.run_id,
                "candidate_reference": body.candidate_reference,
                "expected_checksum": artifact.checksum,
                "selection_facts": {"personalization_mode": "collecting"},
            },
            "ai_selected": False,
            "user_approved": True,
        },
        subject=SubjectLocator(kind="movie", reference=str(movie.id)),
        trigger=TriggerKind.MANUAL,
        initiator=Initiator(kind="user", identifier="onboarding-api"),
        idempotency_key=f"poster_deploy:onboarding:{body.idempotency_key}",
        priority=90,
    )
    exposed = [{"candidate_id": candidate_id} for candidate_id in body.presentation_order]
    event = await append_preference_event(
        db,
        idempotency_key=f"onboarding-selection:{body.idempotency_key}",
        namespace="global",
        subject_kind="movie",
        subject_reference=str(movie.id),
        subject_snapshot={"title": movie.title, "year": movie.year, "tmdb_id": movie.tmdb_id},
        action="selection",
        exposed_candidates=exposed,
        presentation_order=body.presentation_order,
        training_context={"personalization_mode": "collecting", "residual_eligible": False},
        confidence="explicit",
        initiator={"kind": "user", "identifier": "onboarding-api"},
        pipeline_run_id=body.run_id,
        candidate_artifact_id=artifact.id,
    )
    exemplar = await create_pending_exemplar(
        db,
        event=event,
        polarity="positive",
        evidence_source="explicit_selection",
        evidence_weight=1.0,
        deployment_job_id=submission.job_id,
    )
    await db.commit()
    return {
        "deployment_job": submission_response(submission),
        "exemplar_id": exemplar.id,
        "status": (await derive_readiness(db)).to_dict(),
    }


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
        "status": (await derive_readiness(db)).to_dict(),
    }
