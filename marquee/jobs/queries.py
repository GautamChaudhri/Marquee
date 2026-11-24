"""Read queries over the job tables, for the API, the command line, and the doctor."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from marquee.db.models import Job, JobEvent, Movie
from marquee.jobs.registry import BATCH
from marquee.jobs.store import WORKER_LOCK_KEY

_LOCK_HOLDER = text(
    "SELECT a.backend_start FROM pg_locks l JOIN pg_stat_activity a ON a.pid = l.pid "
    "WHERE l.locktype = 'advisory' AND l.granted AND l.objsubid = 1 "
    "AND l.database = (SELECT oid FROM pg_database WHERE datname = current_database()) "
    "AND ((l.classid::bigint << 32) | l.objid::bigint) = :key"
)


@dataclass(frozen=True)
class JobPage:
    total: int
    items: list[Job]


@dataclass(frozen=True)
class WorkerStatus:
    running: bool
    """Whether a worker holds the single-worker lock on this database."""
    since: datetime | None
    queued: int
    running_jobs: int
    failed: int
    """Batch parents are not counted: the worker never runs them."""


async def list_jobs(
    session: AsyncSession,
    *,
    state: str | None = None,
    kind: str | None = None,
    parent_id: int | None = None,
    page: int = 1,
    page_size: int = 50,
) -> JobPage:
    """One page of jobs, newest first, with the total that match."""
    conditions = []
    if state is not None:
        conditions.append(Job.state == state)
    if kind is not None:
        conditions.append(Job.kind == kind)
    if parent_id is not None:
        conditions.append(Job.parent_id == parent_id)
    total = await session.scalar(select(func.count()).select_from(Job).where(*conditions))
    rows = await session.scalars(
        select(Job)
        .where(*conditions)
        .order_by(Job.id.desc())
        .limit(page_size)
        .offset((page - 1) * page_size)
    )
    return JobPage(total=total or 0, items=list(rows))


@dataclass(frozen=True)
class JobSubject:
    """The movie a job is about."""

    movie_id: int
    title: str
    year: int | None


def payload_movie_id(payload: dict[str, Any] | None) -> int | None:
    """The movie a job's payload names (runs, deploys, restores), or ``None``."""
    value = (payload or {}).get("movie_id")
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    return None


async def subjects(session: AsyncSession, jobs: Sequence[Job]) -> dict[int, JobSubject]:
    """The movie each job is about, by job id, in one query for all of them. A job whose
    payload names no movie, or a movie that no longer exists, is left out."""
    wanted = {movie_id for job in jobs if (movie_id := payload_movie_id(job.payload))}
    if not wanted:
        return {}
    rows = await session.execute(
        select(Movie.id, Movie.title, Movie.year).where(Movie.id.in_(wanted))
    )
    movies = {row.id: JobSubject(row.id, row.title, row.year) for row in rows}
    found: dict[int, JobSubject] = {}
    for job in jobs:
        movie_id = payload_movie_id(job.payload)
        if movie_id is not None and movie_id in movies:
            found[job.id] = movies[movie_id]
    return found


async def get_job(session: AsyncSession, job_id: int) -> Job | None:
    return await session.get(Job, job_id)


async def job_events(session: AsyncSession, job_id: int) -> list[JobEvent]:
    rows = await session.scalars(
        select(JobEvent).where(JobEvent.job_id == job_id).order_by(JobEvent.id)
    )
    return list(rows)


async def events_after(session: AsyncSession, after: int, limit: int) -> list[JobEvent]:
    """Events of every job after the event id ``after``, oldest first: a feed to poll."""
    rows = await session.scalars(
        select(JobEvent).where(JobEvent.id > after).order_by(JobEvent.id).limit(limit)
    )
    return list(rows)


async def worker_status(session: AsyncSession) -> WorkerStatus:
    since = await session.scalar(_LOCK_HOLDER, {"key": WORKER_LOCK_KEY})
    counts = dict(
        (
            await session.execute(
                select(Job.state, func.count())
                .where(Job.job_class != BATCH, Job.state.in_(("queued", "running", "failed")))
                .group_by(Job.state)
            )
        ).all()
    )
    return WorkerStatus(
        running=since is not None,
        since=since,
        queued=counts.get("queued", 0),
        running_jobs=counts.get("running", 0),
        failed=counts.get("failed", 0),
    )


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def job_dict(job: Job) -> dict[str, Any]:
    """A job as plain JSON, for ``marquee jobs ... --json``."""
    return {
        "id": job.id,
        "kind": job.kind,
        "job_class": job.job_class,
        "state": job.state,
        "priority": job.priority,
        "payload": job.payload,
        "result": job.result,
        "error": job.error,
        "progress": job.progress,
        "attempt": job.attempt,
        "max_attempts": job.max_attempts,
        "run_after": _iso(job.run_after),
        "dedupe_key": job.dedupe_key,
        "parent_id": job.parent_id,
        "cancel_requested": job.cancel_requested,
        "created_at": _iso(job.created_at),
        "updated_at": _iso(job.updated_at),
        "started_at": _iso(job.started_at),
        "finished_at": _iso(job.finished_at),
    }


def event_dict(event: JobEvent) -> dict[str, Any]:
    return {
        "id": event.id,
        "job_id": event.job_id,
        "at": _iso(event.at),
        "type": event.type,
        "message": event.message,
        "data": event.data,
    }
