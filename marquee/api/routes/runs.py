"""Runs: queue one or a batch, list and inspect them, serve a candidate's image, and queue a
deploy of one of their candidates. Every route needs the API key."""

from __future__ import annotations

import asyncio
import hashlib
import logging
from pathlib import PurePosixPath
from typing import Annotated, BinaryIO

from fastapi import APIRouter, Header, HTTPException, Query, Response, status
from fastapi.responses import Response as FileResponse
from sqlalchemy import select

from marquee.api.files import file_response
from marquee.api.routes.jobs import job_item
from marquee.api.schemas.jobs import JobItem
from marquee.api.schemas.runs import (
    BatchQueued,
    BatchRequest,
    DeployRequest,
    RunDetail,
    RunItem,
    RunList,
    RunState,
)
from marquee.api.sessions import Session
from marquee.artwork import runs
from marquee.config import settings
from marquee.db.models import Movie
from marquee.fs.boundary import FilesystemBoundaryError, boundary_for_roots
from marquee.jobs.handlers.poster_run import enqueue_run, enqueue_runs, enqueue_scope
from marquee.jobs.handlers.poster_write import WriteRefusedError, enqueue_deploy
from marquee.taste import agreement, feedback

logger = logging.getLogger(__name__)

router = APIRouter(tags=["runs"])

_RUN_NOT_FOUND = {404: {"description": "No such run"}}
_ALREADY = {200: {"description": "A job of this kind was already queued or running; that job"}}
_MEDIA_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}


def _accepted(response: Response, created: bool) -> None:
    if not created:
        response.status_code = status.HTTP_200_OK


@router.post(
    "/movies/{movie_id}/run",
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        **_ALREADY,
        404: {"description": "No such movie"},
        409: {"description": "The movie cannot be run (no file, no TMDB details, retired)"},
    },
)
async def queue_run(session: Session, movie_id: int, response: Response) -> JobItem:
    """Queue a run of one movie (202), or return its run already queued or running (200)."""
    movie = await session.get(Movie, movie_id)
    if movie is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="movie not found")
    reason = runs.ineligible_reason(movie)
    if reason is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=reason)
    job, created = await enqueue_run(session, movie_id)
    _accepted(response, created)
    return await job_item(session, job)


@router.post(
    "/runs/batch",
    status_code=status.HTTP_202_ACCEPTED,
    responses={422: {"description": "A movie in movie_ids is unknown or cannot be run"}},
)
async def queue_batch(session: Session, request: BatchRequest) -> BatchQueued:
    """Queue a batch of runs, for a scope or for a list of movies."""
    if request.scope is not None:
        parent, children, found = await enqueue_scope(session, request.scope, limit=request.limit)
        return BatchQueued(
            batch=await job_item(session, parent), movies=found, queued=len(children)
        )
    wanted = list(dict.fromkeys(request.movie_ids or []))
    eligible = set(
        await session.scalars(select(Movie.id).where(Movie.id.in_(wanted), runs.eligible()))
    )
    refused = [movie_id for movie_id in wanted if movie_id not in eligible]
    if refused:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"these movies are unknown or cannot be run: {refused[:50]}",
        )
    parent, children = await enqueue_runs(session, wanted, payload={"movies": len(wanted)})
    return BatchQueued(
        batch=await job_item(session, parent), movies=len(wanted), queued=len(children)
    )


@router.get("/runs")
async def list_runs(
    session: Session,
    movie_id: int | None = None,
    state: RunState | None = None,
    page: Annotated[int, Query(ge=1, description="Page number, from 1")] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 50,
) -> RunList:
    """Runs, newest first."""
    result = await runs.list_runs(
        session, movie_id=movie_id, state=state, page=page, page_size=page_size
    )
    return RunList(
        total=result.total,
        page=page,
        page_size=page_size,
        items=[RunItem.model_validate(run) for run in result.items],
    )


@router.get("/runs/{run_id}", responses=_RUN_NOT_FOUND)
async def get_run(session: Session, run_id: int) -> RunDetail:
    """One run and its candidates: ranked ones by rank, then the rest."""
    run = await runs.get_run(session, run_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="run not found")
    return RunDetail.build(
        run,
        await runs.candidates_of(session, run_id),
        blind=await feedback.blind_for(session, run),
        reviewed=await agreement.run_reviewed(session, run_id),
    )


def _image_response(
    run_folder: str, source_file: str, sha256: str, if_none_match: str | None
) -> FileResponse:
    """Blocking: the 500 px file the run downloaded, confined to the runs directory and
    checked against the hash its record certified."""
    root = settings.runs_dir
    boundary = boundary_for_roots({"runs": root}, access="read", purpose="runs")
    classified = boundary.classify(runs.run_dir(run_folder, root) / source_file, require_file=True)
    if len(classified.key.parts) != 2:
        raise FilesystemBoundaryError("a candidate's image is at the top of its run folder")

    def same_bytes(handle: BinaryIO) -> None:
        if hashlib.file_digest(handle, "sha256").hexdigest() != sha256:
            raise FilesystemBoundaryError("the file no longer matches its record")

    return file_response(
        boundary,
        classified,
        media_type=_MEDIA_TYPES.get(PurePosixPath(source_file).suffix.lower(), "image/jpeg"),
        etag=sha256,
        if_none_match=if_none_match,
        validator=same_bytes,
        cache_control="private, max-age=86400",
    )


@router.get(
    "/runs/{run_id}/candidates/{candidate_id}/image",
    response_class=FileResponse,
    responses={
        200: {"content": {"image/jpeg": {}}, "description": "The 500 px file the run downloaded"},
        304: {"description": "Unchanged since the ETag the client holds"},
        404: {"description": "No such run or candidate, never downloaded, or the file is gone"},
    },
)
async def get_candidate_image(
    session: Session,
    run_id: int,
    candidate_id: int,
    if_none_match: Annotated[str | None, Header()] = None,
) -> FileResponse:
    """The 500 px image a run downloaded for one candidate."""
    run = await runs.get_run(session, run_id)
    candidate = await runs.get_candidate(session, run_id, candidate_id) if run else None
    if run is None or candidate is None or candidate.sha256 is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="no such image")
    try:
        return await asyncio.to_thread(
            _image_response, run.run_folder, candidate.source_file, candidate.sha256, if_none_match
        )
    except (runs.RunRecordError, FilesystemBoundaryError, OSError) as exc:
        logger.warning(
            "API | image of candidate %s unavailable: %s", candidate_id, type(exc).__name__
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="the image is gone or changed"
        ) from exc


@router.post(
    "/runs/{run_id}/deploy",
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        200: {"description": "The same request is the movie's newest write and still queued"},
        404: {"description": "No such run, or the candidate is not one of its candidates"},
        409: {
            "description": "The candidate cannot be deployed (run not completed, never "
            "downloaded, no pick)"
        },
    },
)
async def queue_deploy(
    session: Session, run_id: int, response: Response, request: DeployRequest | None = None
) -> JobItem:
    """Queue the owner's deploy of a candidate of this run: the pick unless one is named."""
    run = await runs.get_run(session, run_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="run not found")
    candidate_id = request.candidate_id if request is not None else None
    if candidate_id is None:
        pick = await runs.pick_of(session, run_id)
        if pick is None:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="the run has no pick")
        candidate_id = pick.id
    elif await runs.get_candidate(session, run_id, candidate_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="not a candidate of this run"
        )
    try:
        job, created = await enqueue_deploy(session, candidate_id)
    except WriteRefusedError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    _accepted(response, created)
    return await job_item(session, job)
