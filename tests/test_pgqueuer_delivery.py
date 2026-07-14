from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import anyio
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from pgqueuer import PgQueuer, Queries, RetryRequested
from pgqueuer.models import Context
from pgqueuer.models import Job as PgQueuerJob
from pgqueuer.types import QueueExecutionMode
from sqlalchemy import select

from marquee.core.jobs.commands import create_system_noop
from marquee.core.jobs.delivery import (
    DeliveryRejectedError,
    deliver_control_job,
    parse_transport_payload,
)
from marquee.core.jobs.pgqueuer_gateway import pgqueuer_gateway
from marquee.core.jobs.pgqueuer_scheduler import create_scheduler
from marquee.core.jobs.pgqueuer_worker import create_worker
from marquee.database import _get_engine
from marquee.main import app
from marquee.models.job import Job, JobAttempt, JobDispatch


@pytest_asyncio.fixture(autouse=True)
async def installed_pgqueuer(db) -> Queries:
    async with _get_engine().connect() as connection:
        raw = await connection.get_raw_connection()
        queries = Queries.from_asyncpg_connection(raw.driver_connection)
        await queries.install()
        try:
            yield queries
        finally:
            await db.rollback()
            await queries.uninstall()


async def _canonical_ticket(db, *, payload: dict | None = None) -> tuple[str, int]:
    await db.rollback()
    job = await create_system_noop(
        db,
        payload=payload or {"echo": "jmc1"},
        idempotency_key=f"system_noop:{uuid4().hex}",
    )
    dispatch = await db.scalar(
        select(JobDispatch).where(
            JobDispatch.job_id == job.id,
            JobDispatch.generation == 1,
        )
    )
    assert dispatch is not None
    values = (job.id, dispatch.pgq_job_id)
    await db.rollback()
    assert values[1] is not None
    return values[0], values[1]


def _transport_job(job_id: str, pgq_job_id: int, *, attempts: int = 0) -> PgQueuerJob:
    now = datetime.now(UTC)
    return PgQueuerJob(
        id=pgq_job_id,
        priority=50,
        created=now,
        updated=now,
        heartbeat=now,
        execute_after=now,
        status="picked",
        entrypoint="control",
        payload=(
            f'{{"dispatch_generation":1,"job_id":"{job_id}","payload_version":1}}'.encode()
        ),
        attempts=attempts,
        queue_manager_id=uuid4(),
        headers=None,
    )


def _context() -> Context:
    return Context(cancellation=anyio.CancelScope())


@pytest.mark.parametrize(
    "payload",
    [
        None,
        b"not-json",
        b'{"job_id":"x","payload_version":1}',
        b'{"dispatch_generation":1,"job_id":"x","payload_version":1,"extra":true}',
        b'{"dispatch_generation":1,"job_id":"x","job_id":"y","payload_version":1}',
        b'{"dispatch_generation":1,"job_id":"x","payload_version":2}',
        b"\xff",
    ],
)
def test_transport_payload_is_strict(payload):
    with pytest.raises(DeliveryRejectedError):
        parse_transport_payload(payload)


async def test_delivery_commits_canonical_success_before_return(db):
    job_id, ticket_id = await _canonical_ticket(db)
    await deliver_control_job(_transport_job(job_id, ticket_id), _context())

    await db.rollback()
    job = await db.get(Job, job_id)
    attempts = list(
        await db.scalars(select(JobAttempt).where(JobAttempt.job_id == job_id))
    )
    assert job is not None
    assert (job.phase, job.outcome, job.result) == (
        "terminal",
        "succeeded",
        {"echo": {"echo": "jmc1"}},
    )
    assert len(attempts) == 1
    assert attempts[0].status == "succeeded"


async def test_duplicate_delivery_performs_effect_once(db):
    job_id, ticket_id = await _canonical_ticket(db)
    calls = 0
    admitted = asyncio.Event()
    release = asyncio.Event()

    async def executor(payload, context):
        nonlocal calls
        calls += 1
        admitted.set()
        await release.wait()
        return payload

    transport_job = _transport_job(job_id, ticket_id)
    first = asyncio.create_task(
        deliver_control_job(transport_job, _context(), executor=executor)
    )
    await admitted.wait()
    await deliver_control_job(
        transport_job,
        _context(),
        executor=executor,
    )
    release.set()
    await first
    assert calls == 1


async def test_new_queue_manager_recovers_abandoned_running_attempt(db):
    job_id, ticket_id = await _canonical_ticket(db)
    admitted = asyncio.Event()
    release = asyncio.Event()

    async def abandoned(payload, context):
        admitted.set()
        await release.wait()
        return payload

    first = asyncio.create_task(
        deliver_control_job(
            _transport_job(job_id, ticket_id),
            _context(),
            executor=abandoned,
        )
    )
    await admitted.wait()
    await deliver_control_job(
        _transport_job(job_id, ticket_id),
        _context(),
    )
    release.set()
    await first

    await db.rollback()
    job = await db.get(Job, job_id)
    attempts = list(
        await db.scalars(
            select(JobAttempt)
            .where(JobAttempt.job_id == job_id)
            .order_by(JobAttempt.number)
        )
    )
    assert job is not None
    assert (job.phase, job.outcome) == ("terminal", "succeeded")
    assert [attempt.status for attempt in attempts] == ["interrupted", "succeeded"]


async def test_retry_is_persisted_before_pgqueuer_signal_is_rethrown(db):
    job_id, ticket_id = await _canonical_ticket(db)

    async def retry(payload, context):
        raise RetryRequested(timedelta(seconds=3), "transient")

    with pytest.raises(RetryRequested):
        await deliver_control_job(
            _transport_job(job_id, ticket_id),
            _context(),
            executor=retry,
        )
    await db.rollback()
    job = await db.get(Job, job_id)
    attempt = await db.scalar(select(JobAttempt).where(JobAttempt.job_id == job_id))
    assert job is not None and attempt is not None
    assert (job.phase, job.status, attempt.status) == (
        "queued",
        "retry_scheduled",
        "retrying",
    )


async def test_real_queue_manager_owns_retry_delay_and_second_attempt(db, installed_pgqueuer):
    job_id, ticket_id = await _canonical_ticket(db)
    calls = 0
    completed = asyncio.Event()
    async with _get_engine().connect() as connection:
        raw = await connection.get_raw_connection()
        app = PgQueuer.from_asyncpg_connection(raw.driver_connection)

        @app.entrypoint("control", accepts_context=True, on_failure="hold")
        async def control(transport_job, context):
            async def retry_once(payload, delivery_context):
                nonlocal calls
                calls += 1
                if calls == 1:
                    raise RetryRequested(timedelta(milliseconds=50), "transient")
                completed.set()
                return {"echo": payload}

            await deliver_control_job(transport_job, context, executor=retry_once)

        manager = asyncio.create_task(
            app.qm.run(
                dequeue_timeout=timedelta(milliseconds=20),
                batch_size=1,
                max_concurrent_tasks=2,
                heartbeat_timeout=timedelta(seconds=1),
            )
        )
        await asyncio.wait_for(completed.wait(), timeout=5)
        app.shutdown.set()
        await asyncio.wait_for(manager, timeout=5)

    await db.rollback()
    job = await db.get(Job, job_id)
    attempts = list(
        await db.scalars(
            select(JobAttempt)
            .where(JobAttempt.job_id == job_id)
            .order_by(JobAttempt.number)
        )
    )
    assert calls == 2
    assert job is not None and (job.phase, job.outcome) == ("terminal", "succeeded")
    assert [attempt.status for attempt in attempts] == ["retrying", "succeeded"]
    assert attempts[0].error["delay_seconds"] == 0.05
    assert await installed_pgqueuer.job_status([ticket_id]) == [(ticket_id, "successful")]


async def test_failure_is_terminalized_before_exception_is_rethrown(db):
    job_id, ticket_id = await _canonical_ticket(db)

    async def fail(payload, context):
        raise RuntimeError("bounded failure")

    with pytest.raises(RuntimeError, match="bounded failure"):
        await deliver_control_job(
            _transport_job(job_id, ticket_id),
            _context(),
            executor=fail,
        )
    await db.rollback()
    job = await db.get(Job, job_id)
    assert job is not None
    assert (job.phase, job.outcome, job.error["type"]) == (
        "terminal",
        "failed",
        "RuntimeError",
    )


async def test_stale_ticket_and_cancelled_job_do_not_execute(db):
    job_id, ticket_id = await _canonical_ticket(db)
    job = await db.get(Job, job_id)
    assert job is not None
    job.desired_state = "cancel"
    await db.commit()

    called = False

    async def executor(payload, context):
        nonlocal called
        called = True
        return payload

    await deliver_control_job(
        _transport_job(job_id, ticket_id),
        _context(),
        executor=executor,
    )
    await db.rollback()
    db.expire_all()
    job = await db.get(Job, job_id)
    assert job is not None
    assert not called
    assert (job.phase, job.outcome) == ("terminal", "cancelled")


async def test_picked_cancellation_reaches_test_blocker(db, installed_pgqueuer):
    job_id, ticket_id = await _canonical_ticket(db)
    started = asyncio.Event()
    async with _get_engine().connect() as connection:
        raw = await connection.get_raw_connection()
        app = PgQueuer.from_asyncpg_connection(raw.driver_connection)

        @app.entrypoint("control", accepts_context=True, on_failure="hold")
        async def control(transport_job, context):
            async def blocker(payload, delivery_context):
                started.set()
                while not delivery_context.cancellation.cancel_called:
                    await asyncio.sleep(0.01)
                raise asyncio.CancelledError

            await deliver_control_job(transport_job, context, executor=blocker)

        manager = asyncio.create_task(
            app.qm.run(
                dequeue_timeout=timedelta(milliseconds=50),
                batch_size=1,
                max_concurrent_tasks=2,
                heartbeat_timeout=timedelta(seconds=1),
            )
        )
        await asyncio.wait_for(started.wait(), timeout=5)
        async with db.begin():
            await pgqueuer_gateway.cancel_known_ticket(db, job_id=job_id)

        for _ in range(100):
            await db.rollback()
            db.expire_all()
            job = await db.get(Job, job_id)
            if job is not None and job.phase == "terminal":
                break
            await asyncio.sleep(0.02)
        app.shutdown.set()
        await asyncio.wait_for(manager, timeout=5)

    assert job is not None
    assert (job.desired_state, job.phase, job.outcome) == (
        "cancel",
        "terminal",
        "cancelled",
    )
    assert await installed_pgqueuer.job_status([ticket_id]) == [(ticket_id, "canceled")]


async def test_unknown_canonical_job_is_held_without_legacy_resolution():
    with pytest.raises(DeliveryRejectedError, match="canonical job does not exist"):
        await deliver_control_job(_transport_job("0" * 32, 999999), _context())


async def test_real_queue_manager_delivers_control_and_acknowledges(db):
    job_id, _ = await _canonical_ticket(db)
    async with _get_engine().connect() as connection:
        raw = await connection.get_raw_connection()
        app: PgQueuer = create_worker(raw.driver_connection)
        await asyncio.wait_for(
            app.qm.run(
                dequeue_timeout=timedelta(milliseconds=50),
                batch_size=1,
                mode=QueueExecutionMode.drain,
                max_concurrent_tasks=2,
                heartbeat_timeout=timedelta(seconds=1),
            ),
            timeout=5,
        )

    await db.rollback()
    job = await db.get(Job, job_id)
    assert job is not None
    assert (job.phase, job.outcome) == ("terminal", "succeeded")


async def test_polling_fallback_delivers_without_matching_notification_channel(db):
    delivered = asyncio.Event()
    async with _get_engine().connect() as connection:
        raw = await connection.get_raw_connection()
        app = PgQueuer.from_asyncpg_connection(
            raw.driver_connection,
            channel="ch_jmc1_notifications_intentionally_missing",
        )

        @app.entrypoint("control", accepts_context=True, on_failure="hold")
        async def control(transport_job, context):
            await deliver_control_job(transport_job, context)
            delivered.set()

        manager = asyncio.create_task(
            app.qm.run(
                dequeue_timeout=timedelta(milliseconds=100),
                batch_size=1,
                max_concurrent_tasks=2,
                heartbeat_timeout=timedelta(seconds=1),
            )
        )
        await asyncio.sleep(0.05)
        job_id, _ = await _canonical_ticket(db)
        await asyncio.wait_for(delivered.wait(), timeout=5)
        app.shutdown.set()
        await asyncio.wait_for(manager, timeout=5)

    await db.rollback()
    job = await db.get(Job, job_id)
    assert job is not None and (job.phase, job.outcome) == ("terminal", "succeeded")


async def test_real_queue_manager_holds_rejected_delivery(installed_pgqueuer):
    payload = (
        b'{"dispatch_generation":1,"job_id":"00000000000000000000000000000000",'
        b'"payload_version":1}'
    )
    ticket_ids = await installed_pgqueuer.enqueue(
        "control",
        payload,
        dedupe_key=f"jmc1-rejected-{uuid4().hex}",
    )
    async with _get_engine().connect() as connection:
        raw = await connection.get_raw_connection()
        app = create_worker(raw.driver_connection)
        await asyncio.wait_for(
            app.qm.run(
                dequeue_timeout=timedelta(milliseconds=50),
                batch_size=1,
                mode=QueueExecutionMode.drain,
                max_concurrent_tasks=2,
                heartbeat_timeout=timedelta(seconds=1),
            ),
            timeout=5,
        )

    statuses = dict(await installed_pgqueuer.job_status(ticket_ids))
    assert statuses[ticket_ids[0]] == "failed"


async def test_stale_pick_is_recovered_by_pgqueuer_heartbeat(db, installed_pgqueuer):
    job_id, _ = await _canonical_ticket(db)
    async with _get_engine().connect() as connection:
        raw = await connection.get_raw_connection()
        app = create_worker(raw.driver_connection)
        picked = [
            job
            async for job in app.qm.fetch_jobs(
                batch_size=1,
                global_concurrency_limit=None,
                heartbeat_timeout=timedelta(milliseconds=50),
            )
        ]
        assert len(picked) == 1
        await asyncio.sleep(0.1)
        await asyncio.wait_for(
            app.qm.run(
                dequeue_timeout=timedelta(milliseconds=50),
                batch_size=1,
                mode=QueueExecutionMode.drain,
                max_concurrent_tasks=2,
                heartbeat_timeout=timedelta(milliseconds=50),
            ),
            timeout=5,
        )

    await db.rollback()
    job = await db.get(Job, job_id)
    assert job is not None
    assert (job.phase, job.outcome) == ("terminal", "succeeded")


async def test_two_scheduler_managers_reconcile_one_test_schedule(installed_pgqueuer):
    effects = 0
    fired = asyncio.Event()
    async with _get_engine().connect() as first, _get_engine().connect() as second:
        first_raw = await first.get_raw_connection()
        second_raw = await second.get_raw_connection()
        apps = [
            create_scheduler(first_raw.driver_connection),
            create_scheduler(second_raw.driver_connection),
        ]
        for app in apps:

            @app.schedule("jmc1_test_schedule", "*/1 * * * * *")
            async def scheduled_noop(schedule):
                nonlocal effects
                effects += 1
                fired.set()

        tasks = [asyncio.create_task(app.sm.run()) for app in apps]
        await asyncio.wait_for(fired.wait(), timeout=5)
        await asyncio.sleep(0.2)
        for app in apps:
            app.shutdown.set()
        await asyncio.wait_for(asyncio.gather(*tasks), timeout=5)

    rows = await installed_pgqueuer.peek_schedule()
    assert [row.entrypoint for row in rows].count("jmc1_test_schedule") == 1
    assert effects == 1


async def test_transport_diagnostics_are_bounded_aggregates(db):
    job_id, _ = await _canonical_ticket(db, payload={"echo": "do-not-expose"})

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/system/job-transport")

    assert response.status_code == 200
    body = response.json()
    assert body["connections"]["budget"]["within_budget"] is True
    assert body["connections"]["observed"] <= body["connections"]["budget"]["maximum"]
    assert body["queue"]
    serialized = response.text
    assert job_id not in serialized
    assert "pgq_job_id" not in serialized
    assert "do-not-expose" not in serialized
    assert "payload" not in serialized.lower()
