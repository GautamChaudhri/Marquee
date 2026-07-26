"""JMC6D D1 durable runtime-incarnation lifecycle contracts."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import anyio
import pytest
import pytest_asyncio
from pgqueuer import Queries
from pgqueuer.models import Context
from pgqueuer.models import Job as PgQueuerJob
from sqlalchemy import select

from marquee.core.jobs import delivery
from marquee.core.jobs.commands import create_system_noop
from marquee.core.jobs.delivery import deliver_job
from marquee.core.jobs.pgqueuer_worker import entrypoint_concurrency_limits
from marquee.core.jobs.runtime_instances import RuntimeInstanceHandle, capability_snapshot
from marquee.database import _get_engine
from marquee.models import JobAttempt, JobDispatch, RuntimeInstance


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


def _transport_job(job_id: str, pgq_job_id: int) -> PgQueuerJob:
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
        attempts=0,
        queue_manager_id=uuid4(),
        headers=None,
    )


def _context() -> Context:
    return Context(cancellation=anyio.CancelScope())


def test_runtime_instance_schema_is_topology_evidence_not_queue_authority() -> None:
    columns = set(RuntimeInstance.__table__.columns.keys())
    assert {
        "id",
        "role",
        "node_label",
        "build",
        "host_boot_id",
        "process_id",
        "process_start_ticks",
        "process_group_id",
        "advertised_entrypoints",
        "capabilities",
        "readiness",
        "started_at",
        "stopped_at",
        "last_heartbeat_at",
        "heartbeat_expires_at",
    } <= columns
    assert not columns & {
        "job_id",
        "pgq_job_id",
        "claim",
        "lease",
        "retry_at",
        "schedule_cursor",
    }
    assert "runtime_instance_id" in JobAttempt.__table__.columns


@pytest.mark.asyncio
async def test_worker_and_scheduler_incarnations_heartbeat_and_stop(db) -> None:
    worker = RuntimeInstanceHandle(
        role="worker",
        node_label="logical-node",
        advertised_entrypoints=("control",),
        capabilities={"entrypoints": ["control"]},
    )
    scheduler = RuntimeInstanceHandle(
        role="scheduler",
        node_label="logical-node",
        advertised_entrypoints=("schedule_library_sync",),
        capabilities={"schedule_entrypoints": ["schedule_library_sync"]},
    )
    # Production worker and scheduler roles are distinct processes. Give the unit-test scheduler
    # a distinct durable start identity while both handles necessarily run in this pytest process.
    scheduler.process_start_ticks += 1
    await worker.start()
    await scheduler.start()
    await worker.ready()
    await scheduler.ready()
    assert await worker.heartbeat_once()
    assert await scheduler.heartbeat_once()
    await worker.stop()
    await scheduler.stop()

    await db.rollback()
    rows = list(
        await db.scalars(select(RuntimeInstance).order_by(RuntimeInstance.role.desc()))
    )
    assert [row.role for row in rows] == ["worker", "scheduler"]
    assert all(UUID(row.id).version == 4 for row in rows)
    assert all(row.node_label == "logical-node" for row in rows)
    assert all(row.readiness == "stopped" and row.stopped_at is not None for row in rows)
    assert all(row.heartbeat_expires_at > row.last_heartbeat_at for row in rows)


@pytest.mark.asyncio
async def test_heartbeat_failure_is_bounded_and_operationally_visible(monkeypatch) -> None:
    handle = RuntimeInstanceHandle(
        role="worker",
        node_label="degraded-node",
        advertised_entrypoints=("control",),
        capabilities={"entrypoints": ["control"]},
    )

    async def unavailable() -> None:
        raise ConnectionError("database unavailable")

    monkeypatch.setattr(handle, "_write_heartbeat", unavailable)
    assert await handle.heartbeat_once() is False
    assert handle.telemetry_health["status"] == "degraded"
    assert handle.telemetry_health["heartbeat_failures"] == 1
    assert handle.telemetry_health["last_error_at"] is not None


def test_capability_advertisement_is_bounded_and_sanitized() -> None:
    configured = entrypoint_concurrency_limits()
    snapshot = capability_snapshot(configured)

    assert snapshot["entrypoints"] == sorted(configured)
    assert set(snapshot) == {"entrypoints", "containment", "gpu"}
    serialized = str(snapshot).lower()
    for forbidden in ("password", "token", "api_key", "payload", "environment", "/dev/"):
        assert forbidden not in serialized


@pytest.mark.asyncio
async def test_admitted_attempt_records_exact_runtime_incarnation(db, monkeypatch) -> None:
    runtime = RuntimeInstanceHandle(
        role="worker",
        node_label="attempt-node",
        advertised_entrypoints=("control",),
        capabilities={"entrypoints": ["control"]},
    )
    await runtime.start()
    await runtime.ready()
    job = await create_system_noop(
        db,
        payload={"echo": "runtime-instance"},
        idempotency_key=f"system_noop:{uuid4().hex}",
    )
    dispatch = await db.scalar(
        select(JobDispatch).where(
            JobDispatch.job_id == job.id,
            JobDispatch.generation == job.dispatch_generation,
        )
    )
    assert dispatch is not None and dispatch.pgq_job_id is not None
    job_id = job.id
    ticket_id = dispatch.pgq_job_id
    await db.rollback()

    async def handler(execution):
        return {"outcome": "succeeded", "summary": {}}

    monkeypatch.setitem(delivery._EXECUTION_HANDLERS, "system_noop", handler)
    await deliver_job(
        _transport_job(job_id, ticket_id),
        _context(),
        expected_entrypoint="control",
        runtime_instance_id=runtime.instance_id,
    )
    await runtime.stop()

    await db.rollback()
    attempt = await db.scalar(select(JobAttempt).where(JobAttempt.job_id == job_id))
    assert attempt is not None
    assert attempt.runtime_instance_id == runtime.instance_id
    assert attempt.worker_node_id is not None
    assert attempt.started_at is not None and attempt.started_at <= datetime.now(UTC)
