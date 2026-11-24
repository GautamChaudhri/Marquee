"""Every change of a job's state.

Each change is one ``UPDATE`` guarded by the state it expects, so a change that arrives
late (the job moved on meanwhile) changes nothing and says so instead of undoing a newer
state. Each function works in the caller's session and commits before it returns: one
call, one transaction, with its events written and the worker notified in that same
transaction. A change that finds its guard no longer holds ends the transaction with a
commit, not a rollback, so the objects the caller holds stay loaded.

States: ``queued`` -> ``running`` -> ``succeeded``, ``failed``, or ``cancelled``. A failed
attempt with attempts left goes back to ``queued`` with a delay (30 s, doubling, at most
10 minutes); ``attempt`` counts starts.
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import and_, case, func, literal_column, null, or_, select, true, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from marquee.db.models import Job
from marquee.db.models.job import ACTIVE_STATES
from marquee.jobs import batch
from marquee.jobs.registry import BATCH, JobKind
from marquee.jobs.store import (
    FRESH,
    JobNotFoundError,
    JobStateError,
    add_event,
    check_json,
    notify,
)

BACKOFF_BASE_S = 30
BACKOFF_CAP_S = 600
ORPHANED = "the worker stopped while the job was running"


def backoff_seconds(attempt: int) -> int:
    """The delay before the next attempt, after attempt number ``attempt`` failed."""
    return min(BACKOFF_BASE_S * 2 ** max(attempt - 1, 0), BACKOFF_CAP_S)


def _backoff() -> Any:
    """:func:`backoff_seconds` as SQL, on the row's own ``attempt``."""
    seconds = func.least(
        BACKOFF_BASE_S * func.power(2, func.greatest(Job.attempt - 1, 0)), BACKOFF_CAP_S
    )
    return seconds * literal_column("interval '1 second'")


async def _missing_or_state(session: AsyncSession, job_id: int, wanted: str) -> JobStateError:
    state = await session.scalar(select(Job.state).where(Job.id == job_id))
    await session.commit()
    if state is None:
        raise JobNotFoundError(f"no job {job_id}")
    return JobStateError(f"job {job_id} is {state}; {wanted}")


async def _finished(session: AsyncSession, job: Job) -> None:
    """A job reached a final state: its batch may be done too (same transaction)."""
    if job.parent_id is not None:
        await batch.finish_parent_if_done(session, job.parent_id)


async def add_job(
    session: AsyncSession,
    kind: JobKind,
    payload: dict[str, Any] | None = None,
    *,
    priority: int = 0,
    dedupe_key: str | None = None,
    run_after: datetime | None = None,
) -> tuple[Job, bool]:
    """:func:`enqueue` without the commit, for a caller composing a larger transaction."""
    payload = payload or {}
    check_json(payload, "payload")
    key = dedupe_key if dedupe_key is not None else kind.dedupe_key
    values: dict[str, Any] = {
        "kind": kind.name,
        "job_class": kind.job_class,
        "state": "queued",
        "priority": priority,
        "payload": payload,
        "max_attempts": kind.max_attempts,
        "dedupe_key": key,
    }
    if run_after is not None:
        values["run_after"] = run_after
    statement = insert(Job).values(**values)
    if key is not None:
        statement = statement.on_conflict_do_nothing(
            index_elements=[Job.dedupe_key], index_where=Job.state.in_(ACTIVE_STATES)
        )
    # The active job a conflict found may finish before it is read; then insert again.
    for _ in range(3):
        job = await session.scalar(statement.returning(Job), execution_options=FRESH)
        if job is not None:
            await add_event(session, job.id, "queued", data={"priority": priority})
            await notify(session, f"new {job.id}")
            return job, True
        existing = await session.scalar(
            select(Job).where(Job.dedupe_key == key, Job.state.in_(ACTIVE_STATES)),
            execution_options=FRESH,
        )
        if existing is not None:
            return existing, False
    raise JobStateError(f"could not queue a {kind.name} job under the key {key!r}")


async def enqueue(
    session: AsyncSession,
    kind: JobKind,
    payload: dict[str, Any] | None = None,
    *,
    priority: int = 0,
    dedupe_key: str | None = None,
    run_after: datetime | None = None,
) -> tuple[Job, bool]:
    """Queue a job; returns it and whether it is new.

    With a dedupe key (the argument, else the kind's), a job already queued or running
    under that key is returned instead of adding another.
    """
    job, created = await add_job(
        session, kind, payload, priority=priority, dedupe_key=dedupe_key, run_after=run_after
    )
    await session.commit()
    return job, created


async def claim(session: AsyncSession, classes: Collection[str]) -> Job | None:
    """Start the best queued job of these classes: highest priority, then oldest."""
    if not classes:
        return None
    best = (
        select(Job.id)
        .where(
            Job.state == "queued",
            Job.run_after <= func.now(),
            Job.job_class.in_(list(classes)),
        )
        .order_by(Job.priority.desc(), Job.id)
        .limit(1)
        .with_for_update(skip_locked=True)
        .scalar_subquery()
    )
    job = await session.scalar(
        update(Job)
        .where(Job.id == best, Job.state == "queued")
        .values(
            state="running",
            attempt=Job.attempt + 1,
            started_at=func.now(),
            finished_at=null(),
            progress=null(),
        )
        .returning(Job),
        execution_options=FRESH,
    )
    if job is None:
        await session.commit()
        return None
    await add_event(session, job.id, "started", data={"attempt": job.attempt})
    await session.commit()
    return job


async def succeed(session: AsyncSession, job_id: int, result: Any) -> bool:
    check_json(result, "result")
    job = await session.scalar(
        update(Job)
        .where(Job.id == job_id, Job.state == "running")
        .values(state="succeeded", result=result, error=null(), finished_at=func.now())
        .returning(Job),
        execution_options=FRESH,
    )
    if job is None:
        await session.commit()
        return False
    await add_event(session, job_id, "succeeded")
    await _finished(session, job)
    await session.commit()
    return True


async def fail(
    session: AsyncSession, job_id: int, error: str, *, permanent: bool = False
) -> str | None:
    """Fail this attempt. Returns the new state (``queued`` for a retry), or ``None``.

    The job is retried after a delay while it has attempts left, unless the error is
    permanent or a cancel was requested.
    """
    final = true() if permanent else or_(Job.attempt >= Job.max_attempts, Job.cancel_requested)
    job = await session.scalar(
        update(Job)
        .where(Job.id == job_id, Job.state == "running")
        .values(
            state=case((final, "failed"), else_="queued"),
            run_after=case((final, Job.run_after), else_=func.now() + _backoff()),
            finished_at=case((final, func.now()), else_=null()),
            error=error,
        )
        .returning(Job),
        execution_options=FRESH,
    )
    if job is None:
        await session.commit()
        return None
    if job.state == "failed":
        await add_event(session, job_id, "failed", error, {"attempt": job.attempt})
        await _finished(session, job)
    else:
        data = {"attempt": job.attempt, "run_after": job.run_after.isoformat()}
        await add_event(session, job_id, "retry_scheduled", error, data)
    await session.commit()
    return job.state


async def mark_cancelled(session: AsyncSession, job_id: int, message: str) -> bool:
    """A running job stopped because it was asked to."""
    job = await session.scalar(
        update(Job)
        .where(Job.id == job_id, Job.state == "running")
        .values(state="cancelled", finished_at=func.now())
        .returning(Job),
        execution_options=FRESH,
    )
    if job is None:
        await session.commit()
        return False
    await add_event(session, job_id, "cancelled", message)
    await _finished(session, job)
    await session.commit()
    return True


async def request_cancel(session: AsyncSession, job_id: int) -> Job:
    """Cancel a queued job now, or ask a running one to stop.

    For a batch, its queued children are cancelled and its running ones asked to stop.
    Asking again changes nothing. Raises :class:`JobStateError` for a finished job.
    """
    job = await batch.cancel_batch(session, job_id)
    if job is not None:
        await session.commit()
        return job
    job = await session.scalar(
        update(Job)
        .where(Job.id == job_id, Job.state == "queued")
        .values(state="cancelled", cancel_requested=True, finished_at=func.now())
        .returning(Job),
        execution_options=FRESH,
    )
    if job is not None:
        await add_event(session, job_id, "cancelled", "cancelled before it started")
        await _finished(session, job)
        await session.commit()
        return job
    job = await session.scalar(
        update(Job)
        .where(Job.id == job_id, Job.state == "running", Job.job_class != BATCH)
        .where(Job.cancel_requested.is_(False))
        .values(cancel_requested=True)
        .returning(Job),
        execution_options=FRESH,
    )
    if job is not None:
        await add_event(session, job_id, "cancel_requested")
        await notify(session, f"cancel {job_id}")
        await session.commit()
        return job
    job = await session.scalar(
        select(Job).where(Job.id == job_id, Job.state == "running"), execution_options=FRESH
    )
    if job is not None:
        await session.commit()
        return job
    raise await _missing_or_state(session, job_id, "only a queued or running job can be cancelled")


async def retry(session: AsyncSession, job_id: int) -> Job:
    """Put a failed or cancelled job back in the queue as new, keeping its history."""
    try:
        job = await session.scalar(
            update(Job)
            .where(
                Job.id == job_id,
                Job.state.in_(("failed", "cancelled")),
                Job.job_class != BATCH,
            )
            .values(
                state="queued",
                attempt=0,
                run_after=func.now(),
                cancel_requested=False,
                result=null(),
                error=null(),
                progress=null(),
                started_at=null(),
                finished_at=null(),
            )
            .returning(Job),
            execution_options=FRESH,
        )
    except IntegrityError:
        await session.rollback()
        raise JobStateError(
            f"job {job_id} cannot be retried: another job with its key is queued or running"
        ) from None
    if job is None:
        raise await _missing_or_state(
            session, job_id, "only a failed or cancelled job can be retried"
        )
    await add_event(session, job_id, "retried")
    await notify(session, f"new {job_id}")
    await session.commit()
    return job


async def record_progress(
    session: AsyncSession,
    job_id: int,
    current: int | None,
    total: int | None,
    message: str | None,
) -> bool:
    progress = {"current": current, "total": total, "message": message}
    check_json(progress, "progress")
    updated = await session.scalar(
        update(Job)
        .where(Job.id == job_id, Job.state == "running")
        .values(progress=progress)
        .returning(Job.id)
    )
    if updated is None:
        await session.commit()
        return False
    await add_event(session, job_id, "progress", message, progress)
    await session.commit()
    return True


async def recover_orphans(session: AsyncSession) -> list[Job]:
    """At worker start: a job still ``running`` was left by a worker that died.

    It goes back to ``queued`` if it has attempts left, else it fails (or is cancelled, if
    that had been asked). Only safe while holding the single-worker lock.
    """
    exhausted = Job.attempt >= Job.max_attempts
    ends = or_(Job.cancel_requested, exhausted)
    jobs = (
        await session.scalars(
            update(Job)
            .where(Job.state == "running", Job.job_class != BATCH)
            .values(
                state=case(
                    (Job.cancel_requested, "cancelled"), (exhausted, "failed"), else_="queued"
                ),
                run_after=func.now(),
                finished_at=case((ends, func.now()), else_=null()),
                error=case((and_(~Job.cancel_requested, exhausted), ORPHANED), else_=Job.error),
            )
            .returning(Job),
            execution_options=FRESH,
        )
    ).all()
    for job in jobs:
        event = "requeued" if job.state == "queued" else job.state
        await add_event(session, job.id, event, ORPHANED, {"attempt": job.attempt})
        if job.state != "queued":
            await _finished(session, job)
    await session.commit()
    return list(jobs)


async def requeue(session: AsyncSession, job_ids: Sequence[int]) -> list[Job]:
    """At a graceful stop: put unfinished jobs back without counting their attempt.

    A job whose cancel had been asked for is cancelled instead.
    """
    if not job_ids:
        return []
    jobs = (
        await session.scalars(
            update(Job)
            .where(Job.id.in_(list(job_ids)), Job.state == "running")
            .values(
                state=case((Job.cancel_requested, "cancelled"), else_="queued"),
                attempt=case((Job.cancel_requested, Job.attempt), else_=Job.attempt - 1),
                run_after=func.now(),
                started_at=case((Job.cancel_requested, Job.started_at), else_=null()),
                finished_at=case((Job.cancel_requested, func.now()), else_=null()),
            )
            .returning(Job),
            execution_options=FRESH,
        )
    ).all()
    for job in jobs:
        if job.state == "queued":
            await add_event(session, job.id, "requeued", "the worker stopped; not counted")
        else:
            await add_event(session, job.id, "cancelled", "the worker stopped")
            await _finished(session, job)
    await session.commit()
    return list(jobs)
