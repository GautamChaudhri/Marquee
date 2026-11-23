"""Canonical cold-start onboarding over ordinary analysis and deployment jobs."""

from __future__ import annotations

import logging
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from marquee.api.job_submission import JobSubmissionResponse, submission_response
from marquee.core.jobs.contracts import TriggerKind
from marquee.core.jobs.control import JobControlError, retry
from marquee.core.jobs.submission import (
    Initiator,
    SubjectLocator,
    SubmissionResult,
    submit_job,
)
from marquee.core.movie_queries import movie_downloaded
from marquee.core.onboarding_review import (
    OnboardingReviewError,
    bind_onboarding_decision,
    load_onboarding_review,
)
from marquee.core.runtime_settings import effective_settings as settings
from marquee.core.taste_preferences import (
    TastePreferenceError,
    TasteReadiness,
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


async def _require_onboarding_enabled() -> None:
    if not settings.ONBOARDING_ENABLED:
        raise HTTPException(status_code=404, detail="Not found")


router = APIRouter(
    prefix="/api/onboarding",
    tags=["onboarding"],
    dependencies=[Depends(_require_onboarding_enabled)],
)


class StartRequest(BaseModel):
    pass


class ChoosePosterRequest(BaseModel):
    run_id: str = Field(min_length=1, max_length=80)
    candidate_id: str = Field(min_length=1, max_length=64, pattern=r"^[a-f0-9]+$")
    review_revision: str = Field(min_length=64, max_length=64, pattern=r"^[a-f0-9]{64}$")
    idempotency_key: str = Field(min_length=1, max_length=120)


class HatePosterRequest(ChoosePosterRequest):
    pass


class OnboardingResponseModel(BaseModel):
    """Closed public response base for the onboarding API boundary."""

    model_config = ConfigDict(extra="forbid")

    def __getitem__(self, key: str) -> object:
        """Preserve mapping reads for direct legacy route consumers.

        HTTP responses stay closed Pydantic documents; this shim only keeps
        in-process callers from treating the C2 response-model migration as a
        behavioral wire-format change.
        """
        return self.model_dump(mode="json")[key]


class OnboardingSubjectResponse(OnboardingResponseModel):
    kind: Literal["movie"]
    id: int
    title: str
    year: int | None


class OnboardingReviewSubjectResponse(OnboardingResponseModel):
    kind: str
    id: int | None
    title: str


class OnboardingThresholdsResponse(OnboardingResponseModel):
    required: int
    encouraged: int
    strong_target: int


class OnboardingActivePublicationResponse(OnboardingResponseModel):
    generation: int | None
    checksum: str | None
    revision: str | None
    compatible: bool


class OnboardingBuildResponse(OnboardingResponseModel):
    id: str | None
    job_id: str | None
    state: str | None
    revision: str | None
    expected_generation: int | None
    retry_of: str | None
    failure: str | None


class OnboardingReloadStateResponse(OnboardingResponseModel):
    expected_checksum: str | None
    observed_checksum: str | None
    ready: bool
    instance_id: str | None
    reason: str | None


class OnboardingResidualResponse(OnboardingResponseModel):
    active: bool
    compatible: bool
    dormant: bool
    reason: str | None


class OnboardingProfileLibraryResponse(OnboardingResponseModel):
    active: OnboardingActivePublicationResponse
    desired_revision: str | None
    desired_generation: int | None
    build: OnboardingBuildResponse
    reload_state: OnboardingReloadStateResponse
    residual: OnboardingResidualResponse
    rebuild_due: bool
    update_attention: bool


class OnboardingLibrariesResponse(OnboardingResponseModel):
    movies: OnboardingProfileLibraryResponse
    tv: OnboardingProfileLibraryResponse


class OnboardingProfileGenerationsResponse(OnboardingResponseModel):
    movies: int | None = None
    tv: int | None = None


class OnboardingActiveJobResponse(OnboardingResponseModel):
    job_id: str
    job_type: str
    phase: str
    subject_kind: str | None
    subject_reference: str | None
    activity_link: str


class OnboardingReviewReferenceResponse(OnboardingResponseModel):
    run_id: str
    analysis_job_id: str
    url: str


class OnboardingJobLineageResponse(OnboardingResponseModel):
    job_id: str
    predecessor_job_id: str | None
    phase: str
    outcome: str | None
    fence_token: int
    retryable: bool
    activity_link: str


class OnboardingPostEffectValidationResponse(OnboardingResponseModel):
    validated: Literal[True]
    outcome: Literal["succeeded", "no_change"]
    reason_code: str | None
    expected_checksum: str
    actual_checksum: str


class OnboardingDeploymentLineageResponse(OnboardingJobLineageResponse):
    exemplar_id: str
    ordinal: int
    state: str
    post_effect_validation: OnboardingPostEffectValidationResponse | None


class OnboardingLineageResponse(OnboardingResponseModel):
    analysis: list[OnboardingJobLineageResponse]
    deployment: list[OnboardingDeploymentLineageResponse]


class OnboardingStatusResponse(OnboardingResponseModel):
    state: Literal["collecting", "eligible", "building", "personalized", "degraded"]
    active_positive_subjects: int
    active_negative_subjects: int
    pending_positive_subjects: int
    revision: str
    thresholds: OnboardingThresholdsResponse
    build_revision: str | None
    build_job_id: str | None
    profile_generations: OnboardingProfileGenerationsResponse
    consumer_reloaded: bool
    next_action: str
    failure: str | None
    libraries: OnboardingLibrariesResponse
    initial_profiles_ready: bool
    personalized_scoring_available: bool
    rebuild_due: bool
    residual_dormant: bool
    active_jobs: list[OnboardingActiveJobResponse]
    review: OnboardingReviewReferenceResponse | None
    lineage: OnboardingLineageResponse


class OnboardingStartResponse(OnboardingResponseModel):
    subject: OnboardingSubjectResponse
    analysis_job: JobSubmissionResponse
    status: OnboardingStatusResponse


class OnboardingCandidateEligibilityResponse(OnboardingResponseModel):
    status: Literal["survived_objective_filters"]
    ocr_summary: str | None


class OnboardingCandidateFactsResponse(OnboardingResponseModel):
    width: int | None
    height: int | None
    language: str | None


class OnboardingReviewCandidateResponse(OnboardingResponseModel):
    candidate_id: str
    image_url: str
    source: str
    eligibility: OnboardingCandidateEligibilityResponse
    facts: OnboardingCandidateFactsResponse


class OnboardingRejectionsResponse(OnboardingResponseModel):
    available: bool
    eligible: int | None
    archived: int | None
    truncated: int | None


class OnboardingReviewActionsResponse(OnboardingResponseModel):
    choose: bool
    hate: bool


class OnboardingReviewLinksResponse(OnboardingResponseModel):
    activity: str
    detail: str
    run: str


class OnboardingReviewResponse(OnboardingResponseModel):
    version: Literal[1]
    run_id: str
    analysis_job_id: str
    status: str
    subject: OnboardingReviewSubjectResponse
    review_revision: str
    candidates: list[OnboardingReviewCandidateResponse]
    rejections: OnboardingRejectionsResponse
    allowed_actions: OnboardingReviewActionsResponse
    links: OnboardingReviewLinksResponse


class OnboardingDecisionResponse(OnboardingResponseModel):
    decision: Literal["choose", "hate"]
    event_id: str
    exemplar_id: str | None
    deployment_job_id: str | None
    disposition: str
    status: OnboardingStatusResponse


class OnboardingCompletionResponse(OnboardingResponseModel):
    revision: str
    build_jobs: list[JobSubmissionResponse]
    status: OnboardingStatusResponse


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
            select(Movie.id, Movie.genres).where(Movie.tmdb_id.is_not(None), movie_downloaded())
        )
    ).all()
    return [(row[0], row[1]) for row in rows]


def _failure_message(value: object) -> str | None:
    """Expose one safe, stable failure summary instead of an open-ended job payload."""
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for key in ("message", "detail", "reason", "error", "code"):
            candidate = value.get(key)
            if isinstance(candidate, str) and candidate:
                return candidate
    return "Profile build failed; inspect the linked job detail."


def _profile_library_response(value: dict[str, object]) -> OnboardingProfileLibraryResponse:
    build = value["build"]
    if not isinstance(build, dict):
        raise ValueError("taste readiness build state is invalid")
    return OnboardingProfileLibraryResponse.model_validate(
        {
            **value,
            "build": {**build, "failure": _failure_message(build.get("failure"))},
        }
    )


def _job_lineage_item(job: Job, *, predecessor_job_id: str | None) -> OnboardingJobLineageResponse:
    return OnboardingJobLineageResponse(
        job_id=job.id,
        predecessor_job_id=predecessor_job_id,
        phase=job.phase,
        outcome=job.outcome,
        fence_token=job.fence_token,
        retryable=job.phase == "terminal" and job.outcome in {"failed", "cancelled"},
        activity_link=f"/projection-room/jobs/{job.id}",
    )


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


async def _onboarding_lineage_status(db: AsyncSession) -> OnboardingLineageResponse:
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
    analysis_items: list[OnboardingJobLineageResponse] = []
    for row in analyses:
        job = await db.get(Job, row.job_id)
        if job is not None:
            analysis_items.append(_job_lineage_item(job, predecessor_job_id=row.predecessor_job_id))
    deployment_items: list[OnboardingDeploymentLineageResponse] = []
    for row in deployments:
        job = await db.get(Job, row.job_id)
        if job is None:
            continue
        item = _job_lineage_item(job, predecessor_job_id=row.predecessor_job_id)
        deployment_items.append(
            OnboardingDeploymentLineageResponse(
                **item.model_dump(),
                exemplar_id=row.exemplar_id,
                ordinal=row.ordinal,
                state=row.state,
                post_effect_validation=row.post_effect_validation,
            )
        )
    return OnboardingLineageResponse(analysis=analysis_items, deployment=deployment_items)


async def _onboarding_active_jobs(
    db: AsyncSession,
    *,
    readiness_libraries: dict[str, dict[str, object]],
    lineage: OnboardingLineageResponse,
) -> list[OnboardingActiveJobResponse]:
    """Load only jobs linked by onboarding lineage or its two explicit profile builds."""
    job_ids = {item.job_id for item in lineage.analysis}
    job_ids.update(item.job_id for item in lineage.deployment)
    for library in readiness_libraries.values():
        build = library.get("build")
        if isinstance(build, dict) and isinstance(build.get("job_id"), str):
            job_ids.add(build["job_id"])
    if not job_ids:
        return []
    rows = list(
        (
            await db.scalars(
                select(Job)
                .where(Job.id.in_(job_ids), Job.phase != "terminal")
                .order_by(Job.created_at.desc())
            )
        ).all()
    )
    return [
        OnboardingActiveJobResponse(
            job_id=row.id,
            job_type=row.type,
            phase=row.phase,
            subject_kind=row.subject_kind,
            subject_reference=row.subject_reference,
            activity_link=f"/projection-room?view=queue&job={row.id}",
        )
        for row in rows
    ]


def _onboarding_status_response(
    *,
    readiness: TasteReadiness,
    active_jobs: list[OnboardingActiveJobResponse],
    review: OnboardingReviewReferenceResponse | None,
    lineage: OnboardingLineageResponse,
) -> OnboardingStatusResponse:
    """Project internal readiness data through the closed public onboarding contract."""
    libraries = readiness.libraries
    profile_generations = readiness.profile_generations
    if not isinstance(libraries, dict) or not all(
        isinstance(libraries.get(library), dict) for library in ("movies", "tv")
    ):
        raise ValueError("taste readiness libraries are invalid")
    if not isinstance(profile_generations, dict):
        raise ValueError("taste readiness profile generations are invalid")
    return OnboardingStatusResponse(
        state=readiness.state,
        active_positive_subjects=readiness.active_positive_subjects,
        active_negative_subjects=readiness.active_negative_subjects,
        pending_positive_subjects=readiness.pending_positive_subjects,
        revision=readiness.revision,
        thresholds=OnboardingThresholdsResponse(
            required=readiness.thresholds.required,
            encouraged=readiness.thresholds.encouraged,
            strong_target=readiness.thresholds.strong_target,
        ),
        build_revision=readiness.build_revision,
        build_job_id=readiness.build_job_id,
        profile_generations=OnboardingProfileGenerationsResponse(
            movies=profile_generations.get("movies"), tv=profile_generations.get("tv")
        ),
        consumer_reloaded=readiness.consumer_reloaded,
        next_action=readiness.next_action,
        failure=_failure_message(readiness.failure),
        libraries=OnboardingLibrariesResponse(
            movies=_profile_library_response(libraries["movies"]),
            tv=_profile_library_response(libraries["tv"]),
        ),
        initial_profiles_ready=readiness.initial_profiles_ready,
        personalized_scoring_available=readiness.personalized_scoring_available,
        rebuild_due=readiness.rebuild_due,
        residual_dormant=readiness.residual_dormant,
        active_jobs=active_jobs,
        review=review,
        lineage=lineage,
    )


@router.get("/status", response_model=OnboardingStatusResponse, status_code=200)
async def onboarding_status(
    db: Annotated[AsyncSession, Depends(get_db)],
) -> OnboardingStatusResponse:
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
    lineage = await _onboarding_lineage_status(db)
    active_jobs = await _onboarding_active_jobs(
        db, readiness_libraries=readiness.libraries, lineage=lineage
    )
    return _onboarding_status_response(
        readiness=readiness,
        active_jobs=active_jobs,
        review=(
            OnboardingReviewReferenceResponse(
                run_id=review.run_id,
                analysis_job_id=review.job_id,
                url=f"/onboarding?review={review.run_id}",
            )
            if review is not None
            else None
        ),
        lineage=lineage,
    )


@router.post("/start", response_model=OnboardingStartResponse, status_code=200)
async def onboarding_start(
    db: Annotated[AsyncSession, Depends(get_db)],
    body: StartRequest | None = None,
) -> OnboardingStartResponse:
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
    subject = OnboardingSubjectResponse(
        kind="movie", id=movie.id, title=movie.title, year=movie.year
    )
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
                "source_descriptors": [{"provider": "tmdb", "reference": f"movie:{movie.tmdb_id}"}],
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
    return OnboardingStartResponse(
        subject=subject,
        analysis_job=submission_response(submission),
        status=await onboarding_status(db),
    )


def _as_int_or_none(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _review_ocr_summary(value: object) -> str | None:
    if isinstance(value, str) and value:
        return value
    if isinstance(value, dict):
        summary = value.get("summary")
        if isinstance(summary, str) and summary:
            return summary
    return None


def _review_response(payload: dict[str, object]) -> OnboardingReviewResponse:
    """Convert archived evidence into the closed display contract used by the browser."""
    raw_subject = payload.get("subject")
    subject = raw_subject if isinstance(raw_subject, dict) else {}
    raw_candidates = payload.get("candidates")
    if not isinstance(raw_candidates, list):
        raise OnboardingReviewError("canonical review candidates are invalid")
    candidates: list[OnboardingReviewCandidateResponse] = []
    for raw_candidate in raw_candidates:
        if not isinstance(raw_candidate, dict):
            raise OnboardingReviewError("canonical review candidate is invalid")
        eligibility = raw_candidate.get("eligibility")
        facts = raw_candidate.get("facts")
        if not isinstance(eligibility, dict) or not isinstance(facts, dict):
            raise OnboardingReviewError("canonical review candidate fields are invalid")
        candidates.append(
            OnboardingReviewCandidateResponse(
                candidate_id=str(raw_candidate["candidate_id"]),
                image_url=str(raw_candidate["image_url"]),
                source=str(raw_candidate["source"]),
                eligibility=OnboardingCandidateEligibilityResponse(
                    status="survived_objective_filters",
                    ocr_summary=_review_ocr_summary(eligibility.get("ocr")),
                ),
                facts=OnboardingCandidateFactsResponse(
                    width=_as_int_or_none(facts.get("width")),
                    height=_as_int_or_none(facts.get("height")),
                    language=(
                        facts.get("language") if isinstance(facts.get("language"), str) else None
                    ),
                ),
            )
        )
    raw_rejections = payload.get("rejections")
    raw_actions = payload.get("allowed_actions")
    raw_links = payload.get("links")
    if not all(isinstance(value, dict) for value in (raw_rejections, raw_actions, raw_links)):
        raise OnboardingReviewError("canonical review metadata is invalid")
    return OnboardingReviewResponse(
        version=1,
        run_id=str(payload["run_id"]),
        analysis_job_id=str(payload["analysis_job_id"]),
        status=str(payload["status"]),
        subject=OnboardingReviewSubjectResponse(
            kind=(subject.get("kind") if isinstance(subject.get("kind"), str) else "movie"),
            id=_as_int_or_none(subject.get("id")),
            title=(
                subject.get("title")
                if isinstance(subject.get("title"), str)
                else (
                    subject.get("display_name")
                    if isinstance(subject.get("display_name"), str)
                    else "Current movie"
                )
            ),
        ),
        review_revision=str(payload["review_revision"]),
        candidates=candidates,
        rejections=OnboardingRejectionsResponse(
            available=bool(raw_rejections.get("available")),
            eligible=_as_int_or_none(raw_rejections.get("eligible")),
            archived=_as_int_or_none(raw_rejections.get("archived")),
            truncated=_as_int_or_none(raw_rejections.get("truncated")),
        ),
        allowed_actions=OnboardingReviewActionsResponse(
            choose=bool(raw_actions.get("choose")), hate=bool(raw_actions.get("hate"))
        ),
        links=OnboardingReviewLinksResponse(
            activity=str(raw_links["activity"]),
            detail=str(raw_links["detail"]),
            run=str(raw_links["run"]),
        ),
    )


@router.get("/runs/{run_id}/review", response_model=OnboardingReviewResponse, status_code=200)
async def onboarding_review(
    run_id: str, db: Annotated[AsyncSession, Depends(get_db)]
) -> OnboardingReviewResponse:
    """Render neutral choice cards from the verified terminal run archive."""
    try:
        return _review_response(await load_onboarding_review(db, run_id))
    except OnboardingReviewError as exc:
        raise _review_http_error(exc) from exc


async def _decision_response(
    body: ChoosePosterRequest,
    db: AsyncSession,
    *,
    decision: Literal["choose", "hate"],
) -> OnboardingDecisionResponse:
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
    return OnboardingDecisionResponse(
        decision=decision,
        event_id=bound["event_id"],
        exemplar_id=bound["exemplar_id"],
        deployment_job_id=bound["deployment_job_id"],
        disposition=bound["disposition"],
        status=await onboarding_status(db),
    )


@router.post("/choose", response_model=OnboardingDecisionResponse, status_code=200)
async def onboarding_choose(
    body: ChoosePosterRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> OnboardingDecisionResponse:
    return await _decision_response(body, db, decision="choose")


@router.post("/hate", response_model=OnboardingDecisionResponse, status_code=200)
async def onboarding_hate(
    body: HatePosterRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> OnboardingDecisionResponse:
    return await _decision_response(body, db, decision="hate")


@router.post("/complete", response_model=OnboardingCompletionResponse, status_code=200)
async def onboarding_complete(
    db: Annotated[AsyncSession, Depends(get_db)],
) -> OnboardingCompletionResponse:
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
    builds = await schedule_initial_profile_build(db, initiator_identifier="onboarding-api")
    await db.commit()
    return OnboardingCompletionResponse(
        revision=revision.digest,
        build_jobs=[submission_response(build) for build in builds],
        status=await onboarding_status(db),
    )
