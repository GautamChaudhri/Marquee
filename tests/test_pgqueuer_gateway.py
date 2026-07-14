"""Integration coverage for JMC1's canonical transactional enqueue gateway."""

from __future__ import annotations

import asyncio
import contextlib
import json
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
import pytest_asyncio
from pgqueuer import Queries
from sqlalchemy import func, select, text

from marquee.core.jobs.commands import (
    JobCommandError,
    create_system_noop,
    validate_system_noop_idempotency_key,
    validate_system_noop_payload,
)
from marquee.core.jobs.pgqueuer_gateway import (
    ENTRYPOINT_CONTROL,
    PgQueuerGatewayError,
    PgQueuerInvariantError,
    pgqueuer_gateway,
)
from marquee.database import _get_engine, _get_session_factory
from marquee.models.job import Job, JobDispatch, JobEvent


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
async def _add_canonical_rows(
    session,
    *,
    job_id: str,
    priority: int = 50,
    delay: timedelta | None = None,
) -> tuple[Job, JobDispatch]:
    now = datetime.now(UTC)
    generation = 1
    eligible_at = now + (delay or timedelta(0))
    job = Job(
        id=job_id,
        type="system_noop",
        payload_version=1,
        request={"echo": "manual"},
        phase="queued",
        desired_state="run",
        dispatch_generation=generation,
        priority=priority,
        eligible_at=eligible_at,
        idempotency_key=f"system_noop:{job_id}",
        root_id=job_id,
        trigger_kind="system",
        feature_area="system",
        subject_kind="system",
        subject_reference="system_noop",
        subject_snapshot={"version": 1, "kind": "system", "label": "System no-op"},
        queued_at=now,
    )
    dispatch = JobDispatch(
        job_id=job_id,
        generation=generation,
        entrypoint=ENTRYPOINT_CONTROL,
        dedupe_key=f"marquee:{job_id}:{generation}",
        priority=priority,
        eligible_at=eligible_at,
    )
    event = JobEvent(
        job_id=job_id,
        event_key="job.queued",
        state="queued",
        message="queued",
    )
    session.add_all([job, dispatch, event])
    return job, dispatch


async def _transport_row(session, ticket_id: int):
    return (
        await session.execute(
            text(
                "SELECT priority, entrypoint, payload, execute_after, dedupe_key "
                "FROM pgqueuer WHERE id = :ticket_id"
            ),
            {"ticket_id": ticket_id},
        )
    ).one_or_none()


@pytest.mark.asyncio
async def test_system_noop_commits_canonical_and_transport_rows_together(db) -> None:
    job = await create_system_noop(
        db,
        payload={"echo": "ready"},
        idempotency_key="system_noop:atomic-commit",
        priority=73,
    )

    assert job.pgq_job_id is not None
    dispatch = await db.scalar(select(JobDispatch).where(JobDispatch.job_id == job.id))
    events = (
        (await db.execute(select(JobEvent).where(JobEvent.job_id == job.id))).scalars().all()
    )
    row = await _transport_row(db, job.pgq_job_id)

    assert dispatch is not None and dispatch.pgq_job_id == job.pgq_job_id
    assert dispatch.generation == job.dispatch_generation == 1
    assert job.configuration_version == 1
    assert job.configuration_snapshot == {}
    assert len(events) == 1 and events[0].state == "queued"
    assert row is not None
    assert row.entrypoint == "control"
    assert row.priority == 73
    assert bytes(row.payload) == (
        f'{{"dispatch_generation":1,"job_id":"{job.id}","payload_version":1}}'.encode()
    )
    assert json.loads(bytes(row.payload)) == {
        "dispatch_generation": 1,
        "job_id": job.id,
        "payload_version": 1,
    }
    assert set(json.loads(bytes(row.payload))) == {
        "dispatch_generation",
        "job_id",
        "payload_version",
    }


@pytest.mark.asyncio
async def test_gateway_leaves_commit_and_rollback_to_caller(db) -> None:
    job_id = uuid.uuid4().hex
    observer = await _get_engine().connect()
    try:
        transaction = await db.begin()
        job, dispatch = await _add_canonical_rows(db, job_id=job_id)
        ticket = await pgqueuer_gateway.enqueue(
            db,
            job_id=job_id,
            entrypoint="control",
            payload_version=1,
            dispatch_generation=1,
            priority=50,
            execute_after=None,
            dedupe_key=f"marquee:{job_id}:1",
        )
        assert db.in_transaction()
        assert job.pgq_job_id == dispatch.pgq_job_id == ticket
        assert await observer.scalar(text("SELECT count(*) FROM jobs WHERE id = :id"), {"id": job_id}) == 0
        assert await observer.scalar(
            text("SELECT count(*) FROM pgqueuer WHERE id = :id"), {"id": ticket}
        ) == 0

        await transaction.commit()
        assert await observer.scalar(text("SELECT count(*) FROM jobs WHERE id = :id"), {"id": job_id}) == 1
        assert await observer.scalar(
            text("SELECT count(*) FROM pgqueuer WHERE id = :id"), {"id": ticket}
        ) == 1
        assert await db.scalar(text("SELECT 1")) == 1
    finally:
        await observer.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_point", ["before", "after"])
async def test_command_failure_rolls_back_every_row(db, monkeypatch, failure_point) -> None:
    original = pgqueuer_gateway.enqueue

    async def fail(*args, **kwargs):
        if failure_point == "after":
            await original(*args, **kwargs)
        raise RuntimeError(f"{failure_point} enqueue failure")

    monkeypatch.setattr(pgqueuer_gateway, "enqueue", fail)
    with pytest.raises(RuntimeError, match="enqueue failure"):
        await create_system_noop(
            db,
            payload={"echo": failure_point},
            idempotency_key=f"system_noop:rollback-{failure_point}",
        )

    assert await db.scalar(select(func.count(Job.id))) == 0
    assert await db.scalar(select(func.count(JobDispatch.id))) == 0
    assert await db.scalar(select(func.count(JobEvent.id))) == 0
    assert await db.scalar(text("SELECT count(*) FROM pgqueuer")) == 0
    assert await db.scalar(text("SELECT count(*) FROM pgqueuer_log")) == 0


@pytest.mark.asyncio
async def test_command_idempotency_race_dispatches_one_ticket() -> None:
    factory = _get_session_factory()

    async def submit() -> str:
        async with factory() as session:
            job = await create_system_noop(
                session,
                payload={"echo": "race"},
                idempotency_key="system_noop:race",
            )
            return job.id

    first, second = await asyncio.gather(submit(), submit())
    assert first == second

    async with factory() as session:
        assert await session.scalar(select(func.count(Job.id))) == 1
        assert await session.scalar(select(func.count(JobDispatch.id))) == 1
        assert await session.scalar(text("SELECT count(*) FROM pgqueuer")) == 1


@pytest.mark.asyncio
async def test_command_rejects_idempotency_payload_conflict(db) -> None:
    await create_system_noop(
        db,
        payload={"echo": "first"},
        idempotency_key="system_noop:conflict",
    )
    with pytest.raises(JobCommandError, match="different command"):
        await create_system_noop(
            db,
            payload={"echo": "second"},
            idempotency_key="system_noop:conflict",
        )


@pytest.mark.asyncio
async def test_priority_and_deferred_eligibility_round_trip(db) -> None:
    delay = timedelta(minutes=20)
    before = datetime.now(UTC)
    job = await create_system_noop(
        db,
        payload={"echo": "later"},
        idempotency_key="system_noop:deferred",
        priority=91,
        execute_after=delay,
    )
    row = await _transport_row(db, job.pgq_job_id)

    assert row is not None and row.priority == 91
    assert before + timedelta(minutes=19, seconds=55) <= row.execute_after
    assert job.eligible_at >= before + timedelta(minutes=19, seconds=55)


def test_noop_validation_rejects_secrets_paths_and_nonfinite_values() -> None:
    with pytest.raises(JobCommandError, match="secrets or paths"):
        validate_system_noop_payload({"api_token": "secret"})
    with pytest.raises(JobCommandError, match="finite JSON"):
        validate_system_noop_payload({"value": float("nan")})
    with pytest.raises(JobCommandError, match="stable-key"):
        validate_system_noop_idempotency_key("not-canonical")


@pytest.mark.asyncio
async def test_gateway_preconditions_and_nested_connection_use_fail_clearly(db) -> None:
    job_id = uuid.uuid4().hex
    with pytest.raises(PgQueuerGatewayError, match="active SQLAlchemy transaction"):
        await pgqueuer_gateway.enqueue(
            db,
            job_id=job_id,
            entrypoint="control",
            payload_version=1,
            dispatch_generation=1,
            priority=50,
            execute_after=None,
            dedupe_key=f"marquee:{job_id}:1",
        )

    async with db.begin():
        await db.execute(text("SELECT 1"))
        async with pgqueuer_gateway._queries(db):
            with pytest.raises(PgQueuerGatewayError, match="concurrent PgQueuer use"):
                async with pgqueuer_gateway._queries(db):
                    pass


@pytest.mark.asyncio
async def test_gateway_rejects_multiple_returned_ticket_ids(db, monkeypatch) -> None:
    class FakeQueries:
        async def enqueue(self, *args, **kwargs):
            return [10, 11]

    monkeypatch.setattr(
        Queries,
        "from_asyncpg_connection",
        classmethod(lambda cls, connection: FakeQueries()),
    )
    job_id = uuid.uuid4().hex
    async with db.begin():
        await _add_canonical_rows(db, job_id=job_id)
        with pytest.raises(PgQueuerInvariantError, match="exactly one numeric ID"):
            await pgqueuer_gateway.enqueue(
                db,
                job_id=job_id,
                entrypoint="control",
                payload_version=1,
                dispatch_generation=1,
                priority=50,
                execute_after=None,
                dedupe_key=f"marquee:{job_id}:1",
            )


@pytest.mark.asyncio
async def test_transport_dedupe_conflict_is_an_invariant_and_rolls_back_canonical(
    db,
    installed_pgqueuer: Queries,
) -> None:
    job_id = uuid.uuid4().hex
    dedupe_key = f"marquee:{job_id}:1"
    existing = await installed_pgqueuer.enqueue(
        "control",
        b'{"dispatch_generation":1,"job_id":"preexisting","payload_version":1}',
        dedupe_key=dedupe_key,
    )

    with pytest.raises(PgQueuerInvariantError, match="dedupe conflict"):
        async with db.begin():
            await _add_canonical_rows(db, job_id=job_id)
            await pgqueuer_gateway.enqueue(
                db,
                job_id=job_id,
                entrypoint="control",
                payload_version=1,
                dispatch_generation=1,
                priority=50,
                execute_after=None,
                dedupe_key=dedupe_key,
            )

    assert await db.scalar(select(func.count(Job.id))) == 0
    assert await db.scalar(text("SELECT count(*) FROM pgqueuer")) == 1
    assert await installed_pgqueuer.job_status(existing) == [(existing[0], "queued")]


@pytest.mark.asyncio
async def test_gateway_rejects_driver_mismatch_and_closed_connection(db) -> None:
    class WrongDriverSession:
        def in_transaction(self):
            return True

        async def connection(self):
            return SimpleNamespace(
                dialect=SimpleNamespace(name="sqlite", driver="aiosqlite")
            )

    with pytest.raises(PgQueuerGatewayError, match="PostgreSQL with asyncpg"):
        async with pgqueuer_gateway._queries(WrongDriverSession()):
            pass

    connection = await _get_engine().connect()
    raw = await connection.get_raw_connection()
    await raw.driver_connection.close()

    class ClosedDriverSession:
        def in_transaction(self):
            return True

        async def connection(self):
            return connection

    try:
        with pytest.raises(PgQueuerGatewayError, match="connection is closed"):
            async with pgqueuer_gateway._queries(ClosedDriverSession()):
                pass
    finally:
        with contextlib.suppress(Exception):
            await connection.invalidate()
        with contextlib.suppress(Exception):
            await connection.close()


@pytest.mark.asyncio
async def test_gateway_companion_operations_are_bounded_to_known_tickets(
    db,
    installed_pgqueuer: Queries,
) -> None:
    job = await create_system_noop(
        db,
        payload={"echo": "companions"},
        idempotency_key="system_noop:companions",
    )
    ticket = job.pgq_job_id
    assert ticket is not None

    async with db.begin():
        statuses = await pgqueuer_gateway.known_ticket_statuses(db, job_ids=[job.id])
        statistics = await pgqueuer_gateway.queue_statistics(db)
        await pgqueuer_gateway.cancel_known_ticket(db, job_id=job.id)

    assert statuses == {job.id: "queued"}
    assert statistics and all("entrypoint" in row for row in statistics)
    assert await installed_pgqueuer.job_status([ticket]) == [(ticket, "canceled")]
    assert (job.desired_state, job.phase, job.outcome) == (
        "cancel",
        "terminal",
        "cancelled",
    )

    async with db.begin():
        with pytest.raises(PgQueuerGatewayError, match="1..100"):
            await pgqueuer_gateway.known_ticket_statuses(db, job_ids=[])


@pytest.mark.asyncio
async def test_reprioritize_replaces_only_the_known_queued_ticket(
    db,
    installed_pgqueuer: Queries,
) -> None:
    job = await create_system_noop(
        db,
        payload={"echo": "reprioritize"},
        idempotency_key="system_noop:reprioritize",
        priority=25,
    )
    original_ticket = job.pgq_job_id
    assert original_ticket is not None

    async with db.begin():
        await pgqueuer_gateway.reprioritize_known_ticket(
            db,
            job_id=job.id,
            priority=75,
        )

    replacement_ticket = job.pgq_job_id
    assert replacement_ticket is not None
    assert replacement_ticket != original_ticket
    assert await installed_pgqueuer.job_status([original_ticket]) == [
        (original_ticket, "canceled")
    ]
    assert await installed_pgqueuer.job_status([replacement_ticket]) == [
        (replacement_ticket, "queued")
    ]
    assert job.priority == 75
    assert job.dispatch_generation == 2

    dispatches = list(
        (
            await db.scalars(
                select(JobDispatch)
                .where(JobDispatch.job_id == job.id)
                .order_by(JobDispatch.generation)
            )
        ).all()
    )
    assert [(row.generation, row.priority, row.disposition) for row in dispatches] == [
        (1, 25, "superseded"),
        (2, 75, "active"),
    ]
