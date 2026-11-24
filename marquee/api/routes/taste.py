"""Review, feedback, exemplars, and the taste profile. Every route needs the API key."""

from __future__ import annotations

import asyncio
import logging
from typing import Annotated, BinaryIO

from fastapi import APIRouter, Header, HTTPException, Query, Response, status
from fastapi.responses import Response as FileResponse
from sqlalchemy import func, select

from marquee.api.files import file_response
from marquee.api.routes.jobs import job_item
from marquee.api.schemas.evaluation import (
    EvaluationHeadline,
    EvaluationItem,
    EvaluationList,
    EvaluationQueued,
    EvaluationRequest,
    EvaluationResultItem,
    EvaluationResultList,
    EvaluationSummary,
    ExemplarSource,
    Outcome,
)
from marquee.api.schemas.jobs import JobItem
from marquee.api.schemas.library import Poster
from marquee.api.schemas.taste import (
    EventItem,
    EventList,
    ExemplarChanges,
    ExemplarItem,
    ExemplarList,
    ExemplarState,
    FeedbackJobs,
    FeedbackList,
    FeedbackListItem,
    FeedbackRequest,
    FeedbackResult,
    PendingItem,
    PendingList,
    Polarity,
    RebuildRequest,
    RevokeRequest,
    TasteStatus,
)
from marquee.api.sessions import Session
from marquee.artwork import runs
from marquee.config import settings
from marquee.db.models import Evaluation, Exemplar
from marquee.fs.boundary import FilesystemBoundaryError, boundary_for_roots
from marquee.jobs import review
from marquee.jobs.handlers.poster_run import (
    EvaluationMoviesError,
    EvaluationRefusedError,
    enqueue_evaluation,
)
from marquee.jobs.handlers.taste_rebuild import enqueue_rebuild
from marquee.library.queries import movie_names
from marquee.taste import agreement, evaluation, exemplars, feedback

logger = logging.getLogger(__name__)

router = APIRouter(tags=["review"])

_FEEDBACK_RESPONSES = {
    200: {"description": "The key was used before: the same event, and nothing changed"},
    404: {"description": "No such run, candidate, or event"},
    409: {"description": "Refused, with the reason (not the latest run, no file, ...)"},
}


async def _changes(session: Session, changes: exemplars.Changes) -> ExemplarChanges:
    every = [*changes.created, *changes.ended, *changes.reactivated]
    if every:
        # Their updated_at was set by the database on commit: read the rows again, in one query.
        await session.scalars(
            select(Exemplar)
            .where(Exemplar.id.in_([row.id for row in every]))
            .execution_options(populate_existing=True)
        )
    names = await movie_names(session, {row.movie_id for row in every})

    def items(rows: list[Exemplar]) -> list[ExemplarItem]:
        return [ExemplarItem.build(row, names) for row in sorted(rows, key=lambda r: r.id)]

    return ExemplarChanges(
        created=items(changes.created),
        ended=items(changes.ended),
        reactivated=items(changes.reactivated),
    )


async def _result(session: Session, result: review.ReviewResult) -> FeedbackResult:
    revoked = await feedback.revoked_ids(session)
    event = result.outcome.event
    return FeedbackResult(
        event=EventItem.build(event, revoked.get(event.id)),
        exemplars=await _changes(session, result.outcome.changes),
        jobs=FeedbackJobs(
            deploy=result.deploy_job.id if result.deploy_job is not None else None,
            rebuild=result.rebuild_job.id if result.rebuild_job is not None else None,
        ),
        deploy_note=result.deploy_note,
    )


def _http(exc: feedback.FeedbackError) -> HTTPException:
    if isinstance(
        exc, feedback.UnknownRunError | feedback.UnknownCandidateError | feedback.UnknownEventError
    ):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))


@router.get("/review")
async def review_list(session: Session) -> PendingList:
    """Movies whose latest completed run has no feedback that stands; policy deploys first."""
    rows = await agreement.pending_reviews(session)
    return PendingList(
        items=[
            PendingItem(
                movie_id=row.movie_id,
                title=row.title,
                year=row.year,
                run_id=row.run_id,
                ranked=row.ranked,
                finished_at=row.finished_at,
                policy_deployed=row.policy_deployed,
                blind=row.blind,
                poster=Poster.of(row.movie_id, row.poster),
                pick_image_url=(
                    f"/api/runs/{row.run_id}/candidates/{row.pick_id}/image"
                    if row.pick_id is not None
                    else None
                ),
            )
            for row in rows
        ]
    )


@router.post(
    "/runs/{run_id}/feedback",
    status_code=status.HTTP_201_CREATED,
    responses=_FEEDBACK_RESPONSES,
)
async def post_feedback(
    session: Session, run_id: int, request: FeedbackRequest, response: Response
) -> FeedbackResult:
    """Record one act of feedback on a run: the event, its exemplar changes, and the jobs."""
    try:
        result = await review.submit(
            session,
            run_id=run_id,
            action=request.action,
            idempotency_key=request.idempotency_key,
            candidate_id=request.candidate_id,
            order=request.order,
            hated=request.hated,
            shown=request.shown,
            deploy=request.deploy,
        )
    except feedback.FeedbackError as exc:
        raise _http(exc) from exc
    if not result.created:
        response.status_code = status.HTTP_200_OK
    return await _result(session, result)


@router.get("/runs/{run_id}/feedback", responses={404: {"description": "No such run"}})
async def get_feedback(session: Session, run_id: int) -> EventList:
    """A run's events, and the revokes of them, oldest first."""
    if await runs.get_run(session, run_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="run not found")
    revoked = await feedback.revoked_ids(session)
    events = await feedback.events_of_run(session, run_id)
    return EventList(items=[EventItem.build(e, revoked.get(e.id)) for e in events])


@router.get("/feedback")
async def list_feedback(
    session: Session,
    movie_id: int | None = None,
    page: Annotated[int, Query(ge=1, description="Page number, from 1")] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 50,
) -> FeedbackList:
    """Every act of feedback, revokes included, newest first, each with its movie's title and
    year and the revoke that undid it."""
    found = await agreement.list_events(session, movie_id=movie_id, page=page, page_size=page_size)
    revoked = await feedback.revoked_ids(session)
    return FeedbackList(
        total=found.total,
        page=page,
        page_size=page_size,
        items=[
            FeedbackListItem.build(event, title, year, revoked.get(event.id))
            for event, title, year in found.items
        ],
    )


@router.post(
    "/feedback/{event_id}/revoke",
    status_code=status.HTTP_201_CREATED,
    responses=_FEEDBACK_RESPONSES,
)
async def revoke_feedback(
    session: Session, event_id: int, response: Response, request: RevokeRequest | None = None
) -> FeedbackResult:
    """Revoke one event: its exemplars are revoked, the one its positive replaced comes
    back, and a rebuild is queued. The library is left alone."""
    try:
        result = await review.revoke(
            session,
            event_id=event_id,
            idempotency_key=request.idempotency_key if request is not None else None,
        )
    except feedback.FeedbackError as exc:
        raise _http(exc) from exc
    if not result.created:
        response.status_code = status.HTTP_200_OK
    return await _result(session, result)


async def _evaluation_item(session: Session, row: Evaluation) -> EvaluationItem:
    view = await evaluation.view(session, row)
    return EvaluationItem(
        id=row.id,
        job_id=row.job_id,
        state=view.state,
        profile_id=row.profile_id,
        profile_sha256=row.profile_sha256,
        residual_id=row.residual_id,
        scorer=row.scorer,  # type: ignore[arg-type]
        movies=row.movies,
        queued=row.queued,
        results=view.results,
        not_run=view.not_run,
        progress=view.progress,
        created_at=row.created_at,
        finished_at=view.finished_at,
        summary=EvaluationSummary.model_validate(view.summary),
    )


async def _headline(session: Session) -> EvaluationHeadline:
    latest = await evaluation.latest_succeeded(session)
    previous = (
        await evaluation.latest_succeeded(session, before=latest.id) if latest is not None else None
    )
    running = await evaluation.running(session)
    return EvaluationHeadline(
        latest=await _evaluation_item(session, latest) if latest else None,
        previous=await _evaluation_item(session, previous) if previous else None,
        running=await _evaluation_item(session, running) if running else None,
    )


@router.get("/taste")
async def taste_status(session: Session) -> TasteStatus:
    """Exemplars, the active profile, whether a rebuild is due, agreement, the residual's
    state, and the latest blind evaluation's headline."""
    status_ = (await agreement.status(session)).to_dict()
    return TasteStatus.model_validate({**status_, "evaluation": await _headline(session)})


@router.post(
    "/taste/evaluations",
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        409: {"description": "An evaluation is running, or the profile cannot be measured now"},
        422: {"description": "A movie named cannot be evaluated"},
    },
)
async def queue_evaluation(
    session: Session, request: EvaluationRequest | None = None
) -> EvaluationQueued:
    """Queue a blind evaluation: a masked run of every movie with an active positive
    exemplar (or of the movies named), as one batch. It changes nothing in the library, the
    feedback, or the taste profile."""
    try:
        row, parent, _ = await enqueue_evaluation(
            session,
            request.movie_ids if request is not None else None,
            limit=request.limit if request is not None else None,
        )
    except EvaluationMoviesError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    except EvaluationRefusedError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return EvaluationQueued(
        evaluation=await _evaluation_item(session, row), batch=await job_item(session, parent)
    )


@router.get("/taste/evaluations")
async def list_evaluations(
    session: Session,
    page: Annotated[int, Query(ge=1, description="Page number, from 1")] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> EvaluationList:
    """Blind evaluations, newest first, each with its summary."""
    total = await session.scalar(select(func.count()).select_from(Evaluation))
    rows = list(
        await session.scalars(
            select(Evaluation)
            .order_by(Evaluation.id.desc())
            .limit(page_size)
            .offset((page - 1) * page_size)
        )
    )
    return EvaluationList(
        total=total or 0,
        page=page,
        page_size=page_size,
        items=[await _evaluation_item(session, row) for row in rows],
    )


@router.get("/taste/evaluations/{evaluation_id}", responses={404: {"description": "No such"}})
async def get_evaluation(session: Session, evaluation_id: int) -> EvaluationItem:
    """One blind evaluation and its summary."""
    row = await session.get(Evaluation, evaluation_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="no such evaluation")
    return await _evaluation_item(session, row)


@router.get(
    "/taste/evaluations/{evaluation_id}/results", responses={404: {"description": "No such"}}
)
async def evaluation_results(
    session: Session,
    evaluation_id: int,
    outcome: Outcome | None = None,
    source: ExemplarSource | None = None,
    page: Annotated[int, Query(ge=1, description="Page number, from 1")] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 50,
) -> EvaluationResultList:
    """One page of an evaluation's results, by title: for each movie, where the owner's
    poster landed, with the images to show."""
    if await session.get(Evaluation, evaluation_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="no such evaluation")
    total, rows = await evaluation.results_page(
        session, evaluation_id, outcome=outcome, source=source, page=page, page_size=page_size
    )

    def image(run_id: int | None, candidate_id: int | None) -> str | None:
        if run_id is None or candidate_id is None:
            return None
        return f"/api/runs/{run_id}/candidates/{candidate_id}/image"

    items = []
    for row in rows:
        result = row.result
        run_id = result.run_id if row.run_exists else None
        items.append(
            EvaluationResultItem(
                movie_id=result.movie_id,
                title=row.title,
                year=row.year,
                exemplar_id=result.exemplar_id,
                exemplar_source=result.exemplar_source,  # type: ignore[arg-type]
                exemplar_image_url=(
                    f"/api/taste/exemplars/{result.exemplar_id}/image"
                    if result.exemplar_id is not None
                    else None
                ),
                run_id=run_id,
                outcome=result.outcome,  # type: ignore[arg-type]
                matched_by=result.matched_by,  # type: ignore[arg-type]
                match_file=result.match_file,
                match_cosine=result.match_cosine,
                best_cosine=result.best_cosine,
                best_file=result.best_file,
                unembedded=result.unembedded,
                rank=result.rank,
                ranked_count=result.ranked_count,
                card=result.card,
                cards=result.cards,
                pick_file=result.pick_file,
                pick_is_match=result.pick_is_match,
                same_design=result.same_design,
                stage=result.stage,
                reason=result.reason,
                match_image_url=image(run_id, row.match_candidate_id),
                pick_image_url=image(run_id, row.pick_candidate_id),
            )
        )
    return EvaluationResultList(total=total, page=page, page_size=page_size, items=items)


@router.post(
    "/taste/rebuild",
    status_code=status.HTTP_202_ACCEPTED,
    responses={200: {"description": "A rebuild of this revision was already queued; that job"}},
)
async def queue_rebuild(
    session: Session, response: Response, request: RebuildRequest | None = None
) -> JobItem:
    """Queue a rebuild of the taste profile from the active exemplars."""
    job, created = await enqueue_rebuild(
        session, force=request.force if request is not None else False
    )
    if not created:
        response.status_code = status.HTTP_200_OK
    return await job_item(session, job)


@router.get("/taste/exemplars")
async def list_exemplars(
    session: Session,
    movie_id: int | None = None,
    state: ExemplarState | None = None,
    polarity: Polarity | None = None,
    page: Annotated[int, Query(ge=1, description="Page number, from 1")] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 50,
) -> ExemplarList:
    """Exemplars, newest first."""
    conditions = []
    if movie_id is not None:
        conditions.append(Exemplar.movie_id == movie_id)
    if state is not None:
        conditions.append(Exemplar.state == state)
    if polarity is not None:
        conditions.append(Exemplar.polarity == polarity)
    total = await session.scalar(select(func.count()).select_from(Exemplar).where(*conditions))
    rows = list(
        await session.scalars(
            select(Exemplar)
            .where(*conditions)
            .order_by(Exemplar.id.desc())
            .limit(page_size)
            .offset((page - 1) * page_size)
        )
    )
    names = await movie_names(session, {row.movie_id for row in rows})
    return ExemplarList(
        total=total or 0,
        page=page,
        page_size=page_size,
        items=[ExemplarItem.build(row, names) for row in rows],
    )


_MEDIA_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}


def _kept_image(file_name: str, sha256: str, if_none_match: str | None) -> FileResponse:
    """Blocking: the kept image, confined to the exemplar store and checked by its hash."""
    import hashlib
    from pathlib import PurePosixPath

    root = settings.exemplars_dir
    boundary = boundary_for_roots({"exemplars": root}, access="read", purpose="exemplars")
    classified = boundary.classify(exemplars.kept_path(file_name, root), require_file=True)

    def same_bytes(handle: BinaryIO) -> None:
        if hashlib.file_digest(handle, "sha256").hexdigest() != sha256:
            raise FilesystemBoundaryError("the file no longer matches its record")

    return file_response(
        boundary,
        classified,
        media_type=_MEDIA_TYPES.get(PurePosixPath(file_name).suffix, "application/octet-stream"),
        etag=sha256,
        if_none_match=if_none_match,
        validator=same_bytes,
        cache_control="private, max-age=86400",
    )


@router.get(
    "/taste/exemplars/{exemplar_id}/image",
    response_class=FileResponse,
    responses={
        200: {"content": {"image/jpeg": {}, "image/png": {}}, "description": "The kept image"},
        304: {"description": "Unchanged since the ETag the client holds"},
        404: {"description": "No such exemplar, or its image is gone or changed"},
    },
)
async def get_exemplar_image(
    session: Session,
    exemplar_id: int,
    if_none_match: Annotated[str | None, Header()] = None,
) -> FileResponse:
    """An exemplar's kept image, with its hash as the ETag."""
    row = await session.get(Exemplar, exemplar_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="no such exemplar")
    try:
        return await asyncio.to_thread(_kept_image, row.file_name, row.sha256, if_none_match)
    except (exemplars.ExemplarError, FilesystemBoundaryError, OSError) as exc:
        logger.warning(
            "API | image of exemplar %s unavailable: %s", exemplar_id, type(exc).__name__
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="the image is gone or changed"
        ) from exc
