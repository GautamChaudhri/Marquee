"""The job queue: list, inspect, cancel, and retry jobs, poll their events, queue a sync, and
see whether a worker runs. Every route needs the API key. No route queues an arbitrary kind.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Response, status

from marquee.api.schemas.jobs import (
    JobEventFeed,
    JobEventItem,
    JobEventList,
    JobItem,
    JobList,
    JobState,
    WorkerInfo,
)
from marquee.api.sessions import Session
from marquee.jobs import queries, queue
from marquee.jobs.registry import default_registry

router = APIRouter(tags=["jobs"])

_NOT_FOUND = {404: {"description": "No such job"}}
_CONFLICT = {409: {"description": "The job's state does not allow it"}}


@router.get("/jobs")
async def list_jobs(
    session: Session,
    state: JobState | None = None,
    kind: Annotated[str | None, Query(max_length=100)] = None,
    parent_id: Annotated[int | None, Query(description="Only the jobs of this batch")] = None,
    page: Annotated[int, Query(ge=1, description="Page number, from 1")] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 50,
) -> JobList:
    """Jobs, newest first."""
    result = await queries.list_jobs(
        session, state=state, kind=kind, parent_id=parent_id, page=page, page_size=page_size
    )
    return JobList(
        total=result.total,
        page=page,
        page_size=page_size,
        items=[JobItem.model_validate(job) for job in result.items],
    )


# Declared before /jobs/{job_id}, so "events" is never read as a job id.
@router.get("/jobs/events")
async def job_event_feed(
    session: Session,
    after: Annotated[int, Query(ge=0, description="The last event id already seen")] = 0,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> JobEventFeed:
    """Events of every job after a cursor, oldest first: a feed a page can poll."""
    events = await queries.events_after(session, after, limit)
    return JobEventFeed(
        items=[JobEventItem.model_validate(event) for event in events],
        next_after=events[-1].id if events else after,
    )


@router.get("/jobs/{job_id}", responses=_NOT_FOUND)
async def get_job(session: Session, job_id: int) -> JobItem:
    job = await queries.get_job(session, job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="job not found")
    return JobItem.model_validate(job)


@router.get("/jobs/{job_id}/events", responses=_NOT_FOUND)
async def get_job_events(session: Session, job_id: int) -> JobEventList:
    """One job's events, oldest first."""
    if await queries.get_job(session, job_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="job not found")
    events = await queries.job_events(session, job_id)
    return JobEventList(items=[JobEventItem.model_validate(event) for event in events])


@router.post("/jobs/{job_id}/cancel", responses={**_NOT_FOUND, **_CONFLICT})
async def cancel_job(session: Session, job_id: int) -> JobItem:
    """Cancel a queued job now, or ask a running one to stop; a batch takes its children."""
    try:
        job = await queue.request_cancel(session, job_id)
    except queue.JobNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except queue.JobStateError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return JobItem.model_validate(job)


@router.post("/jobs/{job_id}/retry", responses={**_NOT_FOUND, **_CONFLICT})
async def retry_job(session: Session, job_id: int) -> JobItem:
    """Queue a failed or cancelled job again, as new; its events keep its history."""
    try:
        job = await queue.retry(session, job_id)
    except queue.JobNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except queue.JobStateError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return JobItem.model_validate(job)


@router.post(
    "/sync",
    status_code=status.HTTP_202_ACCEPTED,
    responses={200: {"description": "A sync was already queued or running; that job"}},
)
async def queue_sync(session: Session, response: Response) -> JobItem:
    """Queue a library sync (202), or return the one already queued or running (200)."""
    job, created = await queue.enqueue(session, default_registry()["library_sync"])
    if not created:
        response.status_code = status.HTTP_200_OK
    return JobItem.model_validate(job)


@router.get("/worker")
async def get_worker(session: Session) -> WorkerInfo:
    """Whether a worker is running, and how many jobs are queued, running, and failed."""
    found = await queries.worker_status(session)
    return WorkerInfo(
        running=found.running,
        since=found.since,
        queued=found.queued,
        running_jobs=found.running_jobs,
        failed=found.failed,
    )
