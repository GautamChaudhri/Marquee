"""The queue's state changes against a real PostgreSQL server (see tests/conftest.py)."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from marquee.db.engine import create_session_factory
from marquee.db.models import Job, JobEvent
from marquee.jobs import queue
from marquee.jobs.registry import CLASS_LIMITS, JobContext, JobKind, free_classes

pytestmark = pytest.mark.db


async def _noop(ctx: JobContext) -> None:
    return None


NET = JobKind("fake_net", "network", handler=_noop, max_attempts=3)
ONCE = JobKind("fake_once", "network", handler=_noop)
MEDIA = JobKind("fake_media", "media_write", handler=_noop)
GPU = JobKind("fake_gpu", "gpu", gpu_function="fake:function")
SINGLE = JobKind("fake_single", "network", handler=_noop, dedupe_key="fake_single")


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


async def _claim(session: AsyncSession, classes: list[str] | None = None) -> Job | None:
    return await queue.claim(session, classes or list(CLASS_LIMITS))


async def _running(session: AsyncSession, kind: JobKind = NET, **payload: Any) -> Job:
    job, _ = await queue.enqueue(session, kind, payload)
    claimed = await _claim(session, [kind.job_class])
    assert claimed is not None and claimed.id == job.id
    return claimed


# ── Enqueue and claim ─────────────────────────────────────────────────────


async def test_enqueue_writes_the_row_and_its_event(session: AsyncSession) -> None:
    job, created = await queue.enqueue(session, NET, {"title": "Die Hard"}, priority=2)

    assert created
    assert (job.kind, job.job_class, job.state) == ("fake_net", "network", "queued")
    assert (job.priority, job.payload, job.max_attempts, job.attempt) == (
        2,
        {"title": "Die Hard"},
        3,
        0,
    )
    assert job.result is None and job.progress is None and job.error is None
    assert await _events(session, job.id) == ["queued"]


async def test_enqueue_refuses_a_payload_that_is_not_json(session: AsyncSession) -> None:
    with pytest.raises(ValueError, match="not plain JSON"):
        await queue.enqueue(session, NET, {"when": datetime.now(UTC)})
    with pytest.raises(ValueError, match="not plain JSON"):
        await queue.enqueue(session, NET, {"score": float("nan")})


async def test_claim_takes_the_highest_priority_then_the_oldest(session: AsyncSession) -> None:
    low, _ = await queue.enqueue(session, NET, priority=0)
    high_old, _ = await queue.enqueue(session, NET, priority=5)
    high_new, _ = await queue.enqueue(session, NET, priority=5)

    order = [(await _claim(session)).id for _ in range(3)]  # type: ignore[union-attr]

    assert order == [high_old.id, high_new.id, low.id]
    assert await _claim(session) is None


async def test_claim_counts_the_start_and_writes_an_event(session: AsyncSession) -> None:
    job, _ = await queue.enqueue(session, NET)

    claimed = await _claim(session)

    assert claimed is not None
    assert (claimed.state, claimed.attempt) == ("running", 1)
    assert claimed.started_at is not None
    assert await _events(session, job.id) == ["queued", "started"]


async def test_a_job_due_later_is_not_claimed(session: AsyncSession) -> None:
    await queue.enqueue(session, NET, run_after=datetime.now(UTC) + timedelta(minutes=5))

    assert await _claim(session) is None


async def test_a_class_at_its_limit_is_skipped(session: AsyncSession) -> None:
    media, _ = await queue.enqueue(session, MEDIA, priority=9)
    net, _ = await queue.enqueue(session, NET)

    # One media_write job is already running, so only the other classes are free.
    classes = free_classes({"media_write": 1, "network": 3})
    assert "media_write" not in classes and "network" in classes
    claimed = await _claim(session, classes)

    assert claimed is not None and claimed.id == net.id
    assert (await _job(session, media.id)).state == "queued"
    assert free_classes({"network": 4, "gpu": 1, "media_read": 2, "media_write": 1}) == []


async def test_a_locked_job_is_skipped_not_waited_for(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    async with sessions() as setup:
        first, _ = await queue.enqueue(setup, NET)
        second, _ = await queue.enqueue(setup, NET)
    async with sessions() as holder, sessions() as claimer:
        # Another connection holds the best row in an open transaction.
        await holder.execute(select(Job.id).where(Job.id == first.id).with_for_update())
        claimed = await asyncio.wait_for(_claim(claimer), timeout=5)
        await holder.rollback()

    assert claimed is not None and claimed.id == second.id


async def test_two_connections_never_claim_the_same_job(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    async with sessions() as setup:
        ids = {(await queue.enqueue(setup, NET))[0].id for _ in range(30)}

    async def claimer() -> list[int]:
        got = []
        async with sessions() as session:
            while (job := await _claim(session)) is not None:
                got.append(job.id)
        return got

    results = await asyncio.gather(*(claimer() for _ in range(5)))
    claimed = [job_id for result in results for job_id in result]

    assert sorted(claimed) == sorted(ids)
    assert len(set(claimed)) == len(claimed)


# ── Dedupe ────────────────────────────────────────────────────────────────


async def test_dedupe_returns_the_active_job(session: AsyncSession) -> None:
    first, created = await queue.enqueue(session, SINGLE)
    again, created_again = await queue.enqueue(session, SINGLE)

    assert created and not created_again
    assert again.id == first.id
    assert first.dedupe_key == "fake_single"

    await _claim(session)
    still, _ = await queue.enqueue(session, SINGLE)
    assert still.id == first.id  # running holds the key too

    await queue.succeed(session, first.id, None)
    fresh, created_fresh = await queue.enqueue(session, SINGLE)
    assert created_fresh and fresh.id != first.id


async def test_the_dedupe_index_allows_one_active_job_per_key(session: AsyncSession) -> None:
    await queue.enqueue(session, NET, dedupe_key="k")
    session.add(Job(kind="x", job_class="network", state="queued", dedupe_key="k"))
    with pytest.raises(IntegrityError):
        await session.flush()
    await session.rollback()

    session.add(Job(kind="x", job_class="network", state="succeeded", dedupe_key="k"))
    await session.flush()  # a finished job no longer holds the key
    await session.rollback()


# ── Finishing an attempt ──────────────────────────────────────────────────


async def test_succeed_stores_the_result(session: AsyncSession) -> None:
    job = await _running(session)

    assert await queue.succeed(session, job.id, {"created": 3})

    done = await _job(session, job.id)
    assert (done.state, done.result, done.error) == ("succeeded", {"created": 3}, None)
    assert done.finished_at is not None
    assert await _events(session, job.id) == ["queued", "started", "succeeded"]


async def test_a_failure_with_attempts_left_is_retried_with_backoff(session: AsyncSession) -> None:
    job = await _running(session)

    before = datetime.now(UTC)
    assert await queue.fail(session, job.id, "RuntimeError: boom") == "queued"

    retried = await _job(session, job.id)
    assert (retried.state, retried.attempt, retried.error) == ("queued", 1, "RuntimeError: boom")
    assert retried.finished_at is None
    delay = (retried.run_after - before).total_seconds()
    assert 29 <= delay <= 32
    assert await _events(session, job.id) == ["queued", "started", "retry_scheduled"]
    assert await _claim(session) is None  # not before its time


async def test_backoff_doubles_and_is_capped() -> None:
    assert [queue.backoff_seconds(n) for n in range(1, 8)] == [30, 60, 120, 240, 480, 600, 600]


async def test_backoff_in_the_database_matches(session: AsyncSession) -> None:
    job = await _running(session)
    await session.execute(update(Job).where(Job.id == job.id).values(attempt=2, max_attempts=9))
    await session.commit()

    before = datetime.now(UTC)
    await queue.fail(session, job.id, "again")

    delay = ((await _job(session, job.id)).run_after - before).total_seconds()
    assert 59 <= delay <= 62


async def test_the_last_attempt_fails_for_good(session: AsyncSession) -> None:
    job = await _running(session, ONCE)

    assert await queue.fail(session, job.id, "boom") == "failed"

    failed = await _job(session, job.id)
    assert (failed.state, failed.error) == ("failed", "boom")
    assert failed.finished_at is not None
    assert await _events(session, job.id) == ["queued", "started", "failed"]


async def test_a_permanent_error_does_not_retry(session: AsyncSession) -> None:
    job = await _running(session)

    assert await queue.fail(session, job.id, "bad mapping", permanent=True) == "failed"
    assert (await _job(session, job.id)).attempt == 1


async def test_a_stale_state_change_does_nothing(session: AsyncSession) -> None:
    job = await _running(session)
    assert await queue.succeed(session, job.id, {"n": 1})

    assert not await queue.succeed(session, job.id, {"n": 2})
    assert await queue.fail(session, job.id, "late") is None
    assert not await queue.mark_cancelled(session, job.id, "late")
    assert not await queue.record_progress(session, job.id, 1, 2, "late")

    done = await _job(session, job.id)
    assert (done.state, done.result, done.error, done.progress) == (
        "succeeded",
        {"n": 1},
        None,
        None,
    )
    assert await _events(session, job.id) == ["queued", "started", "succeeded"]


async def test_progress_is_stored_and_logged(session: AsyncSession) -> None:
    job = await _running(session)

    assert await queue.record_progress(session, job.id, 3, 9, "ocr start")

    assert (await _job(session, job.id)).progress == {
        "current": 3,
        "total": 9,
        "message": "ocr start",
    }
    assert await _events(session, job.id) == ["queued", "started", "progress"]


# ── Cancel and retry ──────────────────────────────────────────────────────


async def test_cancelling_a_queued_job_cancels_it_now(session: AsyncSession) -> None:
    job, _ = await queue.enqueue(session, NET)

    cancelled = await queue.request_cancel(session, job.id)

    assert cancelled.state == "cancelled"
    assert cancelled.finished_at is not None
    assert await _claim(session) is None
    assert await _events(session, job.id) == ["queued", "cancelled"]


async def test_cancelling_a_running_job_asks_it_to_stop(session: AsyncSession) -> None:
    job = await _running(session)

    asked = await queue.request_cancel(session, job.id)
    again = await queue.request_cancel(session, job.id)

    assert (asked.state, asked.cancel_requested) == ("running", True)
    assert again.state == "running"
    assert await queue.mark_cancelled(session, job.id, "stopped as asked")
    assert (await _job(session, job.id)).state == "cancelled"
    assert await _events(session, job.id) == [
        "queued",
        "started",
        "cancel_requested",
        "cancelled",
    ]


async def test_a_failure_after_a_cancel_request_is_not_retried(session: AsyncSession) -> None:
    job = await _running(session)
    await queue.request_cancel(session, job.id)

    assert await queue.fail(session, job.id, "boom") == "failed"


async def test_a_finished_job_cannot_be_cancelled(session: AsyncSession) -> None:
    job = await _running(session)
    await queue.succeed(session, job.id, None)

    with pytest.raises(queue.JobStateError, match="is succeeded"):
        await queue.request_cancel(session, job.id)
    with pytest.raises(queue.JobNotFoundError):
        await queue.request_cancel(session, 999_999)


async def test_retry_puts_a_failed_job_back_as_new(session: AsyncSession) -> None:
    job = await _running(session, ONCE)
    await queue.fail(session, job.id, "boom")

    retried = await queue.retry(session, job.id)

    assert (retried.state, retried.attempt, retried.error, retried.finished_at) == (
        "queued",
        0,
        None,
        None,
    )
    assert await _events(session, job.id) == ["queued", "started", "failed", "retried"]
    claimed = await _claim(session)
    assert claimed is not None and (claimed.id, claimed.attempt) == (job.id, 1)


async def test_retry_of_a_cancelled_job(session: AsyncSession) -> None:
    job, _ = await queue.enqueue(session, NET)
    await queue.request_cancel(session, job.id)

    assert (await queue.retry(session, job.id)).cancel_requested is False


async def test_retry_refuses_an_active_job(session: AsyncSession) -> None:
    job = await _running(session)

    with pytest.raises(queue.JobStateError, match="only a failed or cancelled job"):
        await queue.retry(session, job.id)
    with pytest.raises(queue.JobNotFoundError):
        await queue.retry(session, 999_999)


async def test_retry_refuses_when_another_job_holds_the_key(session: AsyncSession) -> None:
    first, _ = await queue.enqueue(session, SINGLE)
    await queue.request_cancel(session, first.id)
    await queue.enqueue(session, SINGLE)

    with pytest.raises(queue.JobStateError, match="another job with its key"):
        await queue.retry(session, first.id)


# ── Restarts ──────────────────────────────────────────────────────────────


async def test_orphans_with_attempts_left_are_requeued(session: AsyncSession) -> None:
    job = await _running(session)

    (recovered,) = await queue.recover_orphans(session)

    assert (recovered.id, recovered.state, recovered.attempt) == (job.id, "queued", 1)
    assert await _events(session, job.id) == ["queued", "started", "requeued"]
    claimed = await _claim(session)
    assert claimed is not None and claimed.attempt == 2


async def test_orphans_without_attempts_left_fail(session: AsyncSession) -> None:
    job = await _running(session, ONCE)

    (recovered,) = await queue.recover_orphans(session)

    assert (recovered.state, recovered.error) == ("failed", queue.ORPHANED)
    assert recovered.finished_at is not None
    assert await _events(session, job.id) == ["queued", "started", "failed"]


async def test_an_orphan_whose_cancel_was_asked_is_cancelled(session: AsyncSession) -> None:
    job = await _running(session)
    await queue.request_cancel(session, job.id)

    (recovered,) = await queue.recover_orphans(session)

    assert recovered.state == "cancelled"


async def test_requeue_does_not_count_the_attempt(session: AsyncSession) -> None:
    job = await _running(session, ONCE)
    other = await _running(session)
    await queue.request_cancel(session, other.id)

    states = {j.id: (j.state, j.attempt) for j in await queue.requeue(session, [job.id, other.id])}

    assert states == {job.id: ("queued", 0), other.id: ("cancelled", 1)}
    assert await _events(session, job.id) == ["queued", "started", "requeued"]
    assert (await _job(session, job.id)).started_at is None


async def test_a_gpu_job_keeps_its_class(session: AsyncSession) -> None:
    job, _ = await queue.enqueue(session, GPU)

    assert await _claim(session, ["network"]) is None
    claimed = await _claim(session, ["gpu"])
    assert claimed is not None and claimed.id == job.id
