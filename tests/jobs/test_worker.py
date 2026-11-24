"""The worker loop, run in-process with fake job kinds (see tests/conftest.py for the database)."""

from __future__ import annotations

import asyncio
import os
import signal
import time
from collections import Counter
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr
from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from marquee.cancellation import JobCancelledError
from marquee.config import settings
from marquee.db.engine import create_session_factory
from marquee.db.models import Job, JobEvent
from marquee.jobs import batch, queue
from marquee.jobs.registry import JobContext, JobKind, PermanentJobError
from marquee.jobs.schedules import Schedule
from marquee.jobs.worker import Worker, WorkerAlreadyRunningError, WorkerLostLockError, serve

pytestmark = pytest.mark.db

SECRET = "s3cret-db-password"
state: dict[str, Any] = {}


@pytest.fixture(autouse=True)
def _reset_state() -> None:
    state.clear()
    state.update(calls=Counter(), active=0, peak=0)


async def ok(ctx: JobContext) -> dict:
    return {"payload": ctx.payload, "job": ctx.job_id}


async def flaky(ctx: JobContext) -> dict:
    state["calls"][ctx.job_id] += 1
    if state["calls"][ctx.job_id] == 1:
        raise RuntimeError("the first try fails")
    return {"tries": state["calls"][ctx.job_id]}


async def slow(ctx: JobContext) -> None:
    await asyncio.sleep(10)


async def until_cancelled(ctx: JobContext) -> None:
    for _ in range(400):
        if ctx.cancel.is_set():
            raise JobCancelledError("stopped as asked")
        await asyncio.sleep(0.05)
    raise AssertionError("no cancel arrived")


async def ignores_cancel(ctx: JobContext) -> None:
    await asyncio.sleep(30)


async def leaks_secret(ctx: JobContext) -> None:
    raise RuntimeError(f"cannot connect: password {SECRET} refused\nsecond line")


async def permanent(ctx: JobContext) -> None:
    raise PermanentJobError("no point retrying")


async def not_json(ctx: JobContext) -> dict:
    return {"score": float("inf")}


async def chatty(ctx: JobContext) -> None:
    for step in range(50):
        ctx.progress(step, 50, f"step {step}")


async def chatty_thread(ctx: JobContext) -> None:
    def work() -> None:
        for step in range(10):
            ctx.progress(step, 10, "from a thread")

    await asyncio.to_thread(work)


async def counted(ctx: JobContext) -> None:
    state["active"] += 1
    state["peak"] = max(state["peak"], state["active"])
    await asyncio.sleep(0.3)
    state["active"] -= 1


OK = JobKind("ok", "network", handler=ok)
FLAKY = JobKind("flaky", "network", handler=flaky, max_attempts=2)
SLOW = JobKind("slow", "network", handler=slow, timeout_s=0.5)
UNTIL_CANCELLED = JobKind("until_cancelled", "network", handler=until_cancelled, max_attempts=2)
IGNORES_CANCEL = JobKind("ignores_cancel", "network", handler=ignores_cancel)
LEAKS = JobKind("leaks", "network", handler=leaks_secret)
PERMANENT = JobKind("permanent", "network", handler=permanent, max_attempts=3)
NOT_JSON = JobKind("not_json", "network", handler=not_json)
CHATTY = JobKind("chatty", "network", handler=chatty)
CHATTY_THREAD = JobKind("chatty_thread", "network", handler=chatty_thread)
COUNTED = JobKind("counted", "network", handler=counted)
GPU_ECHO = JobKind("gpu_echo", "gpu", gpu_function="gpu_fakes:echo")


async def gpu_handler(ctx: JobContext) -> dict:
    first = await ctx.gpu("gpu_fakes:echo", {"step": 1, **ctx.payload})
    second = await ctx.gpu("gpu_fakes:echo", {"step": 2})
    return {"first": first, "second": second}


async def gpu_handler_cancelled(ctx: JobContext) -> None:
    await ctx.gpu("gpu_fakes:wait_for_cancel", {})


async def network_calls_gpu(ctx: JobContext) -> None:
    await ctx.gpu("gpu_fakes:echo", {})


GPU_HANDLER = JobKind("gpu_handler", "gpu", handler=gpu_handler)
GPU_HANDLER_CANCELLED = JobKind("gpu_handler_cancelled", "gpu", handler=gpu_handler_cancelled)
NETWORK_CALLS_GPU = JobKind("network_calls_gpu", "network", handler=network_calls_gpu)
KINDS = {
    k.name: k
    for k in (
        OK,
        FLAKY,
        SLOW,
        UNTIL_CANCELLED,
        IGNORES_CANCEL,
        LEAKS,
        PERMANENT,
        NOT_JSON,
        CHATTY,
        CHATTY_THREAD,
        COUNTED,
        GPU_ECHO,
        GPU_HANDLER,
        GPU_HANDLER_CANCELLED,
        NETWORK_CALLS_GPU,
    )
}


@pytest.fixture
def sessions(db_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return create_session_factory(db_engine)


def _worker(database_url: str, **options: Any) -> Worker:
    defaults: dict[str, Any] = {
        "registry": KINDS,
        "database_url": database_url,
        "schedules": (),
        "poll_s": 0.2,
        "stop_grace_s": 2.0,
        "gpu_idle_s": 60.0,
    }
    return Worker(**{**defaults, **options})


async def _drain(database_url: str, **options: Any) -> None:
    await asyncio.wait_for(_worker(database_url, drain=True, **options).run(), 30)


async def _enqueue(
    sessions: async_sessionmaker[AsyncSession], kind: JobKind, payload: dict | None = None
) -> int:
    async with sessions() as session:
        job, _ = await queue.enqueue(session, kind, payload)
    return job.id


async def _job(sessions: async_sessionmaker[AsyncSession], job_id: int) -> Job:
    async with sessions() as session:
        job = await session.get(Job, job_id)
    assert job is not None
    return job


async def _events(sessions: async_sessionmaker[AsyncSession], job_id: int) -> list[str]:
    async with sessions() as session:
        rows = await session.scalars(
            select(JobEvent.type).where(JobEvent.job_id == job_id).order_by(JobEvent.id)
        )
        return list(rows)


async def _wait_for(
    sessions: async_sessionmaker[AsyncSession], job_id: int, *states: str, within: float = 15
) -> Job:
    deadline = time.monotonic() + within
    while time.monotonic() < deadline:
        job = await _job(sessions, job_id)
        if job.state in states:
            return job
        await asyncio.sleep(0.05)
    raise AssertionError(f"job {job_id} never reached {states}")


async def _worker_backend(sessions: async_sessionmaker[AsyncSession], within: float = 10) -> int:
    """The pid of the running worker's lock connection on the test database."""
    deadline = time.monotonic() + within
    while time.monotonic() < deadline:
        async with sessions() as session:
            pid = await session.scalar(
                text(
                    "select pid from pg_stat_activity where application_name = 'marquee-worker' "
                    "and datname = current_database()"
                )
            )
        if pid is not None:
            return int(pid)
        await asyncio.sleep(0.05)
    raise AssertionError("no worker connection appeared")


# ── Outcomes ──────────────────────────────────────────────────────────────


async def test_a_job_succeeds_with_its_result(
    database_url: str, sessions: async_sessionmaker[AsyncSession]
) -> None:
    job_id = await _enqueue(sessions, OK, {"title": "Die Hard"})

    await _drain(database_url)

    job = await _job(sessions, job_id)
    assert job.state == "succeeded"
    assert job.result == {"payload": {"title": "Die Hard"}, "job": job_id}
    assert await _events(sessions, job_id) == ["queued", "started", "succeeded"]


async def test_a_failure_is_retried_later_then_succeeds(
    database_url: str, sessions: async_sessionmaker[AsyncSession]
) -> None:
    job_id = await _enqueue(sessions, FLAKY)

    await _drain(database_url)

    waiting = await _job(sessions, job_id)
    assert (waiting.state, waiting.attempt) == ("queued", 1)
    assert waiting.error == "RuntimeError: the first try fails"
    async with sessions() as session:
        await session.execute(update(Job).where(Job.id == job_id).values(run_after=func.now()))
        await session.commit()

    await _drain(database_url)

    done = await _job(sessions, job_id)
    assert (done.state, done.attempt, done.result, done.error) == (
        "succeeded",
        2,
        {"tries": 2},
        None,
    )
    assert await _events(sessions, job_id) == [
        "queued",
        "started",
        "retry_scheduled",
        "started",
        "succeeded",
    ]


async def test_a_job_that_runs_too_long_times_out(
    database_url: str, sessions: async_sessionmaker[AsyncSession]
) -> None:
    job_id = await _enqueue(sessions, SLOW)

    await _drain(database_url)

    job = await _job(sessions, job_id)
    assert (job.state, job.error) == ("failed", "timed out after 0.5 s")


async def test_a_permanent_error_fails_at_once(
    database_url: str, sessions: async_sessionmaker[AsyncSession]
) -> None:
    job_id = await _enqueue(sessions, PERMANENT)

    await _drain(database_url)

    job = await _job(sessions, job_id)
    assert (job.state, job.attempt, job.error) == ("failed", 1, "no point retrying")


async def test_the_stored_error_is_one_line_without_secrets(
    database_url: str,
    sessions: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "POSTGRES_PASSWORD", SecretStr(SECRET))
    job_id = await _enqueue(sessions, LEAKS)

    await _drain(database_url)

    job = await _job(sessions, job_id)
    assert job.error == "RuntimeError: cannot connect: password *** refused"


async def test_an_unknown_kind_fails_for_good(
    database_url: str, sessions: async_sessionmaker[AsyncSession]
) -> None:
    job_id = await _enqueue(sessions, OK)

    await _drain(database_url, registry={})

    job = await _job(sessions, job_id)
    assert (job.state, job.error) == ("failed", "unknown job kind 'ok'")


async def test_a_result_that_is_not_json_fails(
    database_url: str, sessions: async_sessionmaker[AsyncSession]
) -> None:
    job_id = await _enqueue(sessions, NOT_JSON)

    await _drain(database_url)

    job = await _job(sessions, job_id)
    assert job.state == "failed"
    assert job.error is not None and "not plain JSON" in job.error


async def test_progress_is_throttled_and_the_last_value_kept(
    database_url: str, sessions: async_sessionmaker[AsyncSession]
) -> None:
    job_id = await _enqueue(sessions, CHATTY)

    await _drain(database_url)

    job = await _job(sessions, job_id)
    assert job.progress == {"current": 49, "total": 50, "message": "step 49"}
    assert (await _events(sessions, job_id)).count("progress") <= 2


async def test_progress_from_a_handler_thread(
    database_url: str, sessions: async_sessionmaker[AsyncSession]
) -> None:
    job_id = await _enqueue(sessions, CHATTY_THREAD)

    await _drain(database_url)

    job = await _job(sessions, job_id)
    assert (job.state, job.progress) == (
        "succeeded",
        {"current": 9, "total": 10, "message": "from a thread"},
    )


async def test_progress_writes_land_in_order_even_when_the_first_is_slow() -> None:
    from marquee.jobs.worker import _Progress

    landed: list[tuple] = []

    async def write(value: tuple) -> None:
        if value[0] == 0:
            await asyncio.sleep(0.2)  # the first write is slow to commit
        landed.append(value)

    progress = _Progress(write)
    progress(0, 10, "step")
    await asyncio.sleep(0.05)  # the first value is written at once, and its write is slow
    for step in range(1, 10):
        progress(step, 10, "step")
    await progress.close()  # the last value is written now, while the first is still going

    assert landed == [(0, 10, "step"), (9, 10, "step")]


async def test_a_class_never_runs_past_its_limit(
    database_url: str, sessions: async_sessionmaker[AsyncSession]
) -> None:
    ids = [await _enqueue(sessions, COUNTED) for _ in range(7)]

    await _drain(database_url)

    assert state["peak"] == 4
    assert {(await _job(sessions, job_id)).state for job_id in ids} == {"succeeded"}


async def test_a_batch_runs_to_completion(
    database_url: str, sessions: async_sessionmaker[AsyncSession]
) -> None:
    async with sessions() as session:
        parent, _ = await batch.enqueue_batch(session, [(OK, {"n": n}) for n in range(3)])

    await _drain(database_url)

    done = await _job(sessions, parent.id)
    assert done.state == "succeeded"
    assert done.result == {"total": 3, "succeeded": 3, "failed": 0, "cancelled": 0}


# ── Cancel and stop ───────────────────────────────────────────────────────


async def test_cancel_mid_run(
    database_url: str, sessions: async_sessionmaker[AsyncSession]
) -> None:
    job_id = await _enqueue(sessions, UNTIL_CANCELLED)
    worker = asyncio.create_task(_drain(database_url, poll_s=5.0))
    try:
        await _wait_for(sessions, job_id, "running")
        async with sessions() as session:
            await queue.request_cancel(session, job_id)

        # The notification, not the 5 s poll, brings the cancel to the job.
        job = await _wait_for(sessions, job_id, "cancelled", within=4)
        assert job.attempt == 1
    finally:
        await worker
    assert await _events(sessions, job_id) == [
        "queued",
        "started",
        "cancel_requested",
        "cancelled",
    ]


async def test_a_graceful_stop_requeues_without_counting_the_attempt(
    database_url: str, sessions: async_sessionmaker[AsyncSession]
) -> None:
    job_id = await _enqueue(sessions, UNTIL_CANCELLED)
    worker = _worker(database_url)
    running = asyncio.create_task(worker.run())
    await _wait_for(sessions, job_id, "running")

    worker.request_stop()
    await asyncio.wait_for(running, 10)

    job = await _job(sessions, job_id)
    assert (job.state, job.attempt, job.started_at) == ("queued", 0, None)
    assert await _events(sessions, job_id) == ["queued", "started", "requeued"]


async def test_a_graceful_stop_gives_up_on_a_job_that_ignores_cancel(
    database_url: str, sessions: async_sessionmaker[AsyncSession]
) -> None:
    job_id = await _enqueue(sessions, IGNORES_CANCEL)
    worker = _worker(database_url, stop_grace_s=0.3)
    running = asyncio.create_task(worker.run())
    await _wait_for(sessions, job_id, "running")

    worker.request_stop()
    await asyncio.wait_for(running, 10)

    job = await _job(sessions, job_id)
    assert (job.state, job.attempt) == ("queued", 0)


async def test_sigterm_stops_the_worker_gracefully(
    database_url: str, sessions: async_sessionmaker[AsyncSession]
) -> None:
    job_id = await _enqueue(sessions, UNTIL_CANCELLED)
    running = asyncio.create_task(serve(_worker(database_url)))
    await _wait_for(sessions, job_id, "running")

    os.kill(os.getpid(), signal.SIGTERM)
    await asyncio.wait_for(running, 10)

    assert (await _job(sessions, job_id)).state == "queued"


# ── One worker ────────────────────────────────────────────────────────────


async def test_orphans_are_recovered_at_start(
    database_url: str, sessions: async_sessionmaker[AsyncSession]
) -> None:
    job_id = await _enqueue(sessions, FLAKY)
    async with sessions() as session:
        await queue.claim(session, ["network"])  # a worker that then died
    state["calls"][job_id] = 1  # so the run after recovery succeeds

    await _drain(database_url)

    job = await _job(sessions, job_id)
    assert (job.state, job.attempt) == ("succeeded", 2)
    assert await _events(sessions, job_id) == [
        "queued",
        "started",
        "requeued",
        "started",
        "succeeded",
    ]


async def test_a_second_worker_is_refused(
    database_url: str, sessions: async_sessionmaker[AsyncSession]
) -> None:
    first = _worker(database_url)
    running = asyncio.create_task(first.run())
    try:
        await _worker_backend(sessions)
        with pytest.raises(WorkerAlreadyRunningError, match="a worker is already running"):
            await _worker(database_url).run()
    finally:
        first.request_stop()
        await asyncio.wait_for(running, 10)


async def test_the_worker_stops_when_its_lock_connection_is_lost(
    database_url: str, sessions: async_sessionmaker[AsyncSession]
) -> None:
    running = asyncio.create_task(_worker(database_url).run())
    pid = await _worker_backend(sessions)
    async with sessions() as session:
        await session.execute(text("select pg_terminate_backend(:pid)"), {"pid": pid})

    with pytest.raises(WorkerLostLockError):
        await asyncio.wait_for(running, 10)


async def test_schedules_queue_work(
    database_url: str, sessions: async_sessionmaker[AsyncSession]
) -> None:
    every_hour = Schedule("hourly", kind="ok", interval=lambda: timedelta(hours=1))
    worker = _worker(database_url, schedules=[every_hour])
    running = asyncio.create_task(worker.run())
    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            async with sessions() as session:
                done = await session.scalar(select(Job).where(Job.state == "succeeded"))
            if done is not None:
                break
            await asyncio.sleep(0.05)
        assert done is not None and done.kind == "ok"
    finally:
        worker.request_stop()
        await asyncio.wait_for(running, 10)


async def test_a_drain_does_not_queue_scheduled_work(
    database_url: str, sessions: async_sessionmaker[AsyncSession]
) -> None:
    every_hour = Schedule("hourly", kind="ok", interval=lambda: timedelta(hours=1))

    await _drain(database_url, schedules=[every_hour])

    async with sessions() as session:
        assert await session.scalar(select(func.count()).select_from(Job)) == 0


# ── The GPU child ─────────────────────────────────────────────────────────


async def test_a_gpu_job_runs_in_the_child_which_stops_when_idle(
    database_url: str,
    sessions: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parent))
    job_id = await _enqueue(sessions, GPU_ECHO, {"n": 1})
    worker = _worker(database_url, gpu_idle_s=0.5)
    running = asyncio.create_task(worker.run())
    try:
        job = await _wait_for(sessions, job_id, "succeeded", "failed")
        assert job.state == "succeeded", job.error
        assert job.result["echo"] == {"n": 1} and job.result["pid"] != os.getpid()
        assert job.progress == {"current": 1, "total": 2, "message": "half way"}
        deadline = time.monotonic() + 10
        while worker.gpu.alive and time.monotonic() < deadline:
            await asyncio.sleep(0.1)
        assert not worker.gpu.alive
    finally:
        worker.request_stop()
        await asyncio.wait_for(running, 20)
        worker.gpu.kill()


async def test_a_gpu_handler_calls_the_child_through_its_context(
    database_url: str,
    sessions: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parent))
    job_id = await _enqueue(sessions, GPU_HANDLER, {"n": 7})
    worker = _worker(database_url)
    running = asyncio.create_task(worker.run())
    try:
        job = await _wait_for(sessions, job_id, "succeeded", "failed")
        assert job.state == "succeeded", job.error
        first, second = job.result["first"], job.result["second"]
        assert first["echo"] == {"step": 1, "n": 7}
        assert second["echo"] == {"step": 2}
        # One child serves both calls, and it is not the worker.
        assert first["pid"] == second["pid"] != os.getpid()
        assert second["calls"] == first["calls"] + 1
        assert job.progress == {"current": 1, "total": 2, "message": "half way"}
    finally:
        worker.request_stop()
        await asyncio.wait_for(running, 20)
        worker.gpu.kill()


async def test_a_gpu_handler_passes_its_cancel_to_the_child(
    database_url: str,
    sessions: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parent))
    job_id = await _enqueue(sessions, GPU_HANDLER_CANCELLED)
    worker = _worker(database_url)
    running = asyncio.create_task(worker.run())
    try:
        await _wait_for(sessions, job_id, "running")
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            job = await _job(sessions, job_id)
            if job.progress is not None:
                break
            await asyncio.sleep(0.05)
        async with sessions() as session:
            await queue.request_cancel(session, job_id)
        job = await _wait_for(sessions, job_id, "cancelled", "failed", "succeeded")
        assert job.state == "cancelled", job.error
        assert worker.gpu.alive
    finally:
        worker.request_stop()
        await asyncio.wait_for(running, 20)
        worker.gpu.kill()


async def test_a_handler_of_another_class_may_not_call_the_child(
    database_url: str, sessions: async_sessionmaker[AsyncSession]
) -> None:
    job_id = await _enqueue(sessions, NETWORK_CALLS_GPU)

    await _drain(database_url)

    job = await _job(sessions, job_id)
    assert job.state == "failed"
    assert "not of class gpu" in (job.error or "")
