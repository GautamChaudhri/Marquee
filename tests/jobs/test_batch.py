"""Batches against a real PostgreSQL server (see tests/conftest.py)."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from marquee.db.engine import create_session_factory
from marquee.db.models import Job, JobEvent
from marquee.jobs import batch, queue
from marquee.jobs.registry import CLASS_LIMITS, JobContext, JobKind

pytestmark = pytest.mark.db


async def _noop(ctx: JobContext) -> None:
    return None


NET = JobKind("fake_net", "network", handler=_noop)


@pytest.fixture
def sessions(db_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return create_session_factory(db_engine)


@pytest.fixture
async def session(sessions: async_sessionmaker[AsyncSession]) -> AsyncIterator[AsyncSession]:
    async with sessions() as session:
        yield session


async def _job(session: AsyncSession, job_id: int) -> Job:
    job = await session.scalar(
        select(Job).where(Job.id == job_id), execution_options={"populate_existing": True}
    )
    assert job is not None
    await session.commit()
    return job


async def _events(session: AsyncSession, job_id: int) -> list[str]:
    rows = await session.scalars(
        select(JobEvent.type).where(JobEvent.job_id == job_id).order_by(JobEvent.id)
    )
    return list(rows)


async def _batch(session: AsyncSession, size: int) -> tuple[Job, list[Job]]:
    return await batch.enqueue_batch(
        session, [(NET, {"n": n}) for n in range(size)], payload={"label": "test"}
    )


async def _claim_all(session: AsyncSession) -> list[int]:
    claimed = []
    while (job := await queue.claim(session, list(CLASS_LIMITS))) is not None:
        claimed.append(job.id)
    return claimed


async def test_a_batch_is_a_running_parent_with_queued_children(session: AsyncSession) -> None:
    parent, children = await _batch(session, 3)

    assert (parent.kind, parent.job_class, parent.state) == ("batch", "batch", "running")
    assert parent.payload == {"label": "test"}
    assert [c.payload for c in children] == [{"n": 0}, {"n": 1}, {"n": 2}]
    assert {(c.state, c.parent_id) for c in children} == {("queued", parent.id)}
    assert await _events(session, parent.id) == ["queued"]
    # The worker never runs the parent itself.
    assert sorted(await _claim_all(session)) == sorted(c.id for c in children)


async def test_the_last_child_to_succeed_finishes_the_batch(session: AsyncSession) -> None:
    parent, children = await _batch(session, 2)
    await _claim_all(session)

    await queue.succeed(session, children[0].id, None)
    assert (await _job(session, parent.id)).state == "running"
    await queue.succeed(session, children[1].id, None)

    done = await _job(session, parent.id)
    assert done.state == "succeeded"
    assert done.result == {"total": 2, "succeeded": 2, "failed": 0, "cancelled": 0}
    assert done.finished_at is not None
    assert await _events(session, parent.id) == ["queued", "succeeded"]


async def test_a_failed_child_fails_the_batch(session: AsyncSession) -> None:
    parent, children = await _batch(session, 2)
    await _claim_all(session)

    await queue.fail(session, children[0].id, "boom")
    await queue.succeed(session, children[1].id, None)

    done = await _job(session, parent.id)
    assert done.state == "failed"
    assert done.result == {"total": 2, "succeeded": 1, "failed": 1, "cancelled": 0}


async def test_a_child_waiting_for_a_retry_keeps_the_batch_open(session: AsyncSession) -> None:
    kind = JobKind("fake_retry", "network", handler=_noop, max_attempts=2)
    parent, (child,) = await batch.enqueue_batch(session, [(kind, {})])
    await _claim_all(session)

    assert await queue.fail(session, child.id, "boom") == "queued"

    assert (await _job(session, parent.id)).state == "running"


async def test_cancelling_a_batch(session: AsyncSession) -> None:
    parent, children = await _batch(session, 3)
    running = await queue.claim(session, ["network"])
    assert running is not None

    asked = await queue.request_cancel(session, parent.id)

    assert (asked.state, asked.cancel_requested) == ("running", True)
    states = {c.id: (await _job(session, c.id)) for c in children}
    assert states[running.id].state == "running" and states[running.id].cancel_requested
    assert {j.state for i, j in states.items() if i != running.id} == {"cancelled"}

    await queue.mark_cancelled(session, running.id, "stopped")

    done = await _job(session, parent.id)
    assert done.state == "cancelled"
    assert done.result == {"total": 3, "succeeded": 0, "failed": 0, "cancelled": 3}
    assert await _events(session, parent.id) == ["queued", "cancel_requested", "cancelled"]
    assert await _events(session, running.id) == [
        "queued",
        "started",
        "cancel_requested",
        "cancelled",
    ]


async def test_cancelling_a_batch_of_queued_children_finishes_it_at_once(
    session: AsyncSession,
) -> None:
    parent, _ = await _batch(session, 2)

    cancelled = await queue.request_cancel(session, parent.id)

    assert cancelled.state == "cancelled"
    assert cancelled.result == {"total": 2, "succeeded": 0, "failed": 0, "cancelled": 2}
    with pytest.raises(queue.JobStateError, match="is cancelled"):
        await queue.request_cancel(session, parent.id)


async def test_an_empty_batch_is_done_at_once(session: AsyncSession) -> None:
    parent, children = await batch.enqueue_batch(session, [])

    assert children == []
    done = await _job(session, parent.id)
    assert (done.state, done.result["total"]) == ("succeeded", 0)


async def test_a_batch_cannot_be_retried_and_is_never_an_orphan(session: AsyncSession) -> None:
    parent, children = await _batch(session, 1)

    assert await queue.recover_orphans(session) == []
    assert (await _job(session, parent.id)).state == "running"
    await queue.request_cancel(session, parent.id)
    with pytest.raises(queue.JobStateError):
        await queue.retry(session, parent.id)


async def test_an_orphaned_child_that_fails_finishes_its_batch(session: AsyncSession) -> None:
    parent, _ = await _batch(session, 1)
    await _claim_all(session)

    (orphan,) = await queue.recover_orphans(session)

    assert orphan.state == "failed"
    assert (await _job(session, parent.id)).state == "failed"


async def test_children_finishing_at_once_still_finish_their_batch(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    async with sessions() as setup:
        batches = [await _batch(setup, 2) for _ in range(10)]
        await _claim_all(setup)

    async def finish(job_id: int) -> None:
        async with sessions() as session:
            await queue.succeed(session, job_id, None)

    await asyncio.gather(*(finish(c.id) for _, children in batches for c in children))

    async with sessions() as check:
        for parent, _ in batches:
            done = await _job(check, parent.id)
            assert done.state == "succeeded", done.id
            assert await _events(check, parent.id) == ["queued", "succeeded"]
