"""Batches: a parent row grouping child jobs, finished by its last child.

The worker never runs a parent. It is ``running`` from the moment it is queued, and the
transaction that moves its last active child to a final state also finishes it: succeeded
if every child did, cancelled if a cancel was asked for, otherwise failed, with the counts
in its ``result``. The parent's row is locked before its children are counted, so two
children finishing at once cannot each see the other still running.

A child keeps its kind's dedupe key: a child whose key already has an active job (or that
repeats another child's key) is left out, and the parent's ``queued`` event counts it as
skipped.

Retrying a child after its batch has finished does not reopen the batch.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from sqlalchemy import func, insert, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from marquee.db.models import Job, JobEvent
from marquee.db.models.job import ACTIVE_STATES
from marquee.jobs.registry import BATCH, JobKind
from marquee.jobs.store import FRESH, add_event, check_json, notify

_ACTIVE = text(f"state IN ({', '.join(repr(state) for state in ACTIVE_STATES)})")


async def enqueue_batch(
    session: AsyncSession,
    children: Sequence[tuple[JobKind, dict[str, Any]]],
    *,
    payload: dict[str, Any] | None = None,
    priority: int = 0,
    commit: bool = True,
) -> tuple[Job, list[Job]]:
    """Queue a parent and its children in one transaction.

    Returns the parent and the children actually queued: one whose dedupe key is taken is
    left out. With ``commit`` false the caller commits, so rows of its own can join the
    same transaction.
    """
    payload = payload or {}
    for value in (payload, *(child for _, child in children)):
        check_json(value, "payload")
    rows: list[dict[str, Any]] = []
    keys: set[str] = set()
    for kind, child_payload in children:
        key = kind.dedupe_key_for(child_payload)
        if key is not None:
            if key in keys:
                continue
            keys.add(key)
        rows.append(
            {
                "kind": kind.name,
                "job_class": kind.job_class,
                "state": "queued",
                "payload": child_payload,
                "priority": priority,
                "max_attempts": kind.max_attempts,
                "dedupe_key": key,
            }
        )
    parent = await session.scalar(
        insert(Job)
        .values(
            kind=BATCH,
            job_class=BATCH,
            state="running",
            payload=payload,
            priority=priority,
            started_at=func.now(),
        )
        .returning(Job),
        execution_options=FRESH,
    )
    assert parent is not None
    jobs: list[Job] = []
    if rows:
        statement = (
            pg_insert(Job)
            # A literal predicate: an expanding IN cannot be used with executemany.
            .on_conflict_do_nothing(index_elements=[Job.dedupe_key], index_where=_ACTIVE)
            .returning(Job)
        )
        inserted = await session.scalars(
            statement, [{**row, "parent_id": parent.id} for row in rows]
        )
        jobs = sorted(inserted, key=lambda job: job.id)
    await add_event(
        session,
        parent.id,
        "queued",
        data={"children": len(jobs), "skipped": len(children) - len(jobs)},
    )
    if jobs:
        await session.execute(
            insert(JobEvent),
            [
                {"job_id": job.id, "type": "queued", "data": {"parent_id": parent.id}}
                for job in jobs
            ],
        )
    await finish_parent_if_done(session, parent.id)  # an empty batch is done at once
    await notify(session, f"new {parent.id}")
    if commit:
        await session.commit()
    return parent, jobs


async def finish_parent_if_done(session: AsyncSession, parent_id: int) -> str | None:
    """Finish the parent if no child is still active; returns its final state, if any.

    Runs inside the transaction that finished a child, and does not commit.
    """
    cancel_requested = await session.scalar(
        select(Job.cancel_requested)
        .where(Job.id == parent_id, Job.job_class == BATCH, Job.state == "running")
        .with_for_update()
    )
    if cancel_requested is None:
        return None
    counts = (
        await session.execute(
            select(
                func.count().label("total"),
                func.count().filter(Job.state == "succeeded").label("succeeded"),
                func.count().filter(Job.state == "failed").label("failed"),
                func.count().filter(Job.state == "cancelled").label("cancelled"),
                func.count().filter(Job.state.in_(ACTIVE_STATES)).label("active"),
            ).where(Job.parent_id == parent_id)
        )
    ).one()
    if counts.active:
        return None
    result = {
        "total": counts.total,
        "succeeded": counts.succeeded,
        "failed": counts.failed,
        "cancelled": counts.cancelled,
    }
    if cancel_requested:
        state = "cancelled"
    else:
        state = "succeeded" if counts.succeeded == counts.total else "failed"
    await session.execute(
        update(Job)
        .where(Job.id == parent_id, Job.state == "running")
        .values(state=state, result=result, finished_at=func.now())
    )
    await add_event(session, parent_id, state, data=result)
    return state


async def cancel_batch(session: AsyncSession, parent_id: int) -> Job | None:
    """Cancel a running batch: its queued children now, its running ones on request.

    Returns the parent, or ``None`` when ``parent_id`` is not a running batch. Does not
    commit.
    """
    parent = await session.scalar(
        update(Job)
        .where(Job.id == parent_id, Job.job_class == BATCH, Job.state == "running")
        .where(Job.cancel_requested.is_(False))
        .values(cancel_requested=True)
        .returning(Job),
        execution_options=FRESH,
    )
    if parent is None:
        return await session.scalar(
            select(Job).where(Job.id == parent_id, Job.job_class == BATCH, Job.state == "running"),
            execution_options=FRESH,
        )
    await add_event(session, parent_id, "cancel_requested")
    cancelled = (
        await session.scalars(
            update(Job)
            .where(Job.parent_id == parent_id, Job.state == "queued")
            .values(state="cancelled", cancel_requested=True, finished_at=func.now())
            .returning(Job.id)
        )
    ).all()
    asked = (
        await session.scalars(
            update(Job)
            .where(
                Job.parent_id == parent_id,
                Job.state == "running",
                Job.cancel_requested.is_(False),
            )
            .values(cancel_requested=True)
            .returning(Job.id)
        )
    ).all()
    events = [
        {"job_id": job_id, "type": "cancelled", "message": "its batch was cancelled"}
        for job_id in cancelled
    ] + [{"job_id": job_id, "type": "cancel_requested"} for job_id in asked]
    if events:
        await session.execute(insert(JobEvent), events)
    if asked:
        await notify(session, f"cancel {parent_id}")
    await finish_parent_if_done(session, parent_id)
    return await session.scalar(select(Job).where(Job.id == parent_id), execution_options=FRESH)
