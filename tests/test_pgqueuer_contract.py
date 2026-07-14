"""Contract tests for PgQueuer's caller-owned asyncpg connection bridge."""

from __future__ import annotations

import asyncio
import importlib.metadata
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import timedelta

import asyncpg
import pytest
import pytest_asyncio
from pgqueuer import PgQueuer, Queries
from pgqueuer.models import ScheduleContext
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from marquee.config import settings


@dataclass(frozen=True)
class _PgQueuerContractDatabase:
    engine: AsyncEngine
    dsn: str
    schema: str

    async def connect(self) -> asyncpg.Connection:
        return await asyncpg.connect(
            self.dsn,
            server_settings={"search_path": self.schema},
        )


@pytest_asyncio.fixture
async def pgqueuer_contract_database() -> AsyncIterator[_PgQueuerContractDatabase]:
    schema = f"pgq_contract_{uuid.uuid4().hex}"
    url = make_url(settings.db_url_resolved)
    dsn = url.set(drivername="postgresql").render_as_string(hide_password=False)
    admin_engine = create_async_engine(settings.db_url_resolved, poolclass=NullPool)

    async with admin_engine.begin() as connection:
        await connection.execute(text(f'CREATE SCHEMA "{schema}"'))

    install_connection = await asyncpg.connect(
        dsn,
        server_settings={"search_path": schema},
    )
    try:
        queries = Queries.from_asyncpg_connection(install_connection)
        await queries.install()
        await queries.upgrade()
    finally:
        await install_connection.close()

    engine = create_async_engine(
        settings.db_url_resolved,
        poolclass=NullPool,
        connect_args={"server_settings": {"search_path": schema}},
    )
    try:
        yield _PgQueuerContractDatabase(engine=engine, dsn=dsn, schema=schema)
    finally:
        await engine.dispose()
        async with admin_engine.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await admin_engine.dispose()


def test_pgqueuer_public_queries_contract_is_pinned() -> None:
    assert importlib.metadata.version("pgqueuer") == "1.1.1"
    assert callable(Queries.from_asyncpg_connection)
    assert callable(Queries.enqueue)


@pytest.mark.asyncio
async def test_pgqueuer_enqueue_uses_caller_transaction_and_connection(
    pgqueuer_contract_database: _PgQueuerContractDatabase,
) -> None:
    observer = await pgqueuer_contract_database.connect()
    observer_queries = Queries.from_asyncpg_connection(observer)
    try:
        async with pgqueuer_contract_database.engine.connect() as connection:
            transaction = await connection.begin()
            await connection.execute(text("SELECT 1"))
            raw_connection = await connection.get_raw_connection()
            driver_connection = raw_connection.driver_connection
            queries = Queries.from_asyncpg_connection(driver_connection)

            ids = await queries.enqueue(
                "control",
                b'{"dispatch_generation":1,"job_id":"contract","payload_version":1}',
                dedupe_key="marquee:contract:1",
            )

            assert len(ids) == 1
            assert isinstance(ids[0], int)
            assert connection.in_transaction()
            assert not driver_connection.is_closed()
            assert await queries.job_status(ids) == [(ids[0], "queued")]
            assert [row.job_id for row in await queries.queue_log()] == ids
            assert await observer_queries.job_status(ids) == []
            assert await observer_queries.queue_log() == []
            assert await connection.scalar(text("SELECT 1")) == 1

            await transaction.commit()
            assert not driver_connection.is_closed()
            assert await observer_queries.job_status(ids) == [(ids[0], "queued")]
            assert [row.job_id for row in await observer_queries.queue_log()] == ids
    finally:
        await observer.close()


@pytest.mark.asyncio
async def test_pgqueuer_enqueue_rolls_back_queue_and_log_writes(
    pgqueuer_contract_database: _PgQueuerContractDatabase,
) -> None:
    observer = await pgqueuer_contract_database.connect()
    observer_queries = Queries.from_asyncpg_connection(observer)
    try:
        async with pgqueuer_contract_database.engine.connect() as connection:
            transaction = await connection.begin()
            await connection.execute(text("SELECT 1"))
            raw_connection = await connection.get_raw_connection()
            driver_connection = raw_connection.driver_connection
            queries = Queries.from_asyncpg_connection(driver_connection)
            ids = await queries.enqueue(
                "control",
                b'{"dispatch_generation":1,"job_id":"rollback","payload_version":1}',
                dedupe_key="marquee:rollback:1",
            )

            await transaction.rollback()

            assert not driver_connection.is_closed()
            assert await connection.scalar(text("SELECT 1")) == 1
            assert await observer_queries.job_status(ids) == []
            assert all(row.job_id not in ids for row in await observer_queries.queue_log())
    finally:
        await observer.close()


@pytest.mark.asyncio
async def test_pgqueuer_bulk_enqueue_returns_ordered_one_for_one_numeric_ids(
    pgqueuer_contract_database: _PgQueuerContractDatabase,
) -> None:
    observer = await pgqueuer_contract_database.connect()
    try:
        async with pgqueuer_contract_database.engine.connect() as connection:
            transaction = await connection.begin()
            await connection.execute(text("SELECT 1"))
            raw_connection = await connection.get_raw_connection()
            driver_connection = raw_connection.driver_connection
            queries = Queries.from_asyncpg_connection(driver_connection)
            entrypoints = ["control", "network", "cpu"]
            payloads = [b"first", b"second", b"third"]
            priorities = [3, 2, 1]
            dedupe_keys = [f"jmc4a-bulk-{uuid.uuid4().hex}-{index}" for index in range(3)]

            ids = await queries.enqueue(
                entrypoints,
                payloads,
                priority=priorities,
                execute_after=[timedelta(0), timedelta(seconds=1), timedelta(seconds=2)],
                dedupe_key=dedupe_keys,
            )

            assert len(ids) == len(entrypoints)
            assert len(set(ids)) == len(ids)
            assert all(isinstance(ticket_id, int) for ticket_id in ids)
            rows = await driver_connection.fetch(
                "SELECT id, entrypoint, payload, priority, dedupe_key "
                "FROM pgqueuer WHERE id = ANY($1::bigint[])",
                ids,
            )
            rows_by_id = {row["id"]: row for row in rows}
            assert [rows_by_id[ticket_id]["entrypoint"] for ticket_id in ids] == entrypoints
            assert [bytes(rows_by_id[ticket_id]["payload"]) for ticket_id in ids] == payloads
            assert [rows_by_id[ticket_id]["priority"] for ticket_id in ids] == priorities
            assert [rows_by_id[ticket_id]["dedupe_key"] for ticket_id in ids] == dedupe_keys
            assert await Queries.from_asyncpg_connection(observer).job_status(ids) == []

            await transaction.commit()

        statuses = await Queries.from_asyncpg_connection(observer).job_status(ids)
        assert [ticket_id for ticket_id, status in statuses if status == "queued"] == ids
    finally:
        await observer.close()


@pytest.mark.asyncio
async def test_pgqueuer_bulk_enqueue_rollback_removes_every_queue_and_log_row(
    pgqueuer_contract_database: _PgQueuerContractDatabase,
) -> None:
    observer = await pgqueuer_contract_database.connect()
    observer_queries = Queries.from_asyncpg_connection(observer)
    try:
        async with pgqueuer_contract_database.engine.connect() as connection:
            transaction = await connection.begin()
            await connection.execute(text("SELECT 1"))
            raw_connection = await connection.get_raw_connection()
            queries = Queries.from_asyncpg_connection(raw_connection.driver_connection)
            suffix = uuid.uuid4().hex
            ids = await queries.enqueue(
                ["control", "control"],
                [b"rollback-first", b"rollback-second"],
                priority=[0, 0],
                dedupe_key=[f"jmc4a-rollback-{suffix}-1", f"jmc4a-rollback-{suffix}-2"],
            )

            await transaction.rollback()

            assert await connection.scalar(text("SELECT 1")) == 1
            assert await observer_queries.job_status(ids) == []
            assert all(row.job_id not in ids for row in await observer_queries.queue_log())
    finally:
        await observer.close()


@pytest.mark.asyncio
async def test_pgqueuer_schedule_callback_receives_picked_utc_value_and_shared_context(
    pgqueuer_contract_database: _PgQueuerContractDatabase,
) -> None:
    connection = await pgqueuer_contract_database.connect()
    marker = object()
    app = PgQueuer.from_asyncpg_connection(connection, resources={"marker": marker})
    received = []
    expression = "*/1 * * * * *"
    entrypoint = f"jmc4a_schedule_{uuid.uuid4().hex}"

    @app.schedule(entrypoint, expression, accepts_context=True)
    async def scheduled(schedule, context: ScheduleContext) -> None:
        received.append((schedule, context))

    with pytest.raises(RuntimeError, match="must be unique"):
        app.schedule(entrypoint, expression)(scheduled)

    key, executor = next(iter(app.sm.registry.items()))
    try:
        await app.queries.insert_schedule({key: timedelta(0)})
        schedules = await app.queries.fetch_schedule({key: timedelta(seconds=1)})
        assert len(schedules) == 1
        picked = schedules[0]
        assert picked.status == "picked"
        assert picked.entrypoint == entrypoint
        assert picked.expression == expression
        assert picked.heartbeat.tzinfo is not None
        assert picked.updated.tzinfo is not None
        assert picked.next_run.tzinfo is not None
        assert picked.next_run >= picked.heartbeat

        await app.sm.dispatch(executor, picked)

        assert len(received) == 1
        assert received[0][0] == picked
        assert received[0][1].resources["marker"] is marker
        stored = [row for row in await app.queries.peek_schedule() if row.id == picked.id]
        assert len(stored) == 1
        assert stored[0].status == "queued"
        assert stored[0].last_run is not None
        assert stored[0].last_run.tzinfo is not None
    finally:
        await connection.close()


@pytest.mark.asyncio
async def test_pgqueuer_schedule_cancellation_requeues_the_picked_schedule(
    pgqueuer_contract_database: _PgQueuerContractDatabase,
) -> None:
    connection = await pgqueuer_contract_database.connect()
    app = PgQueuer.from_asyncpg_connection(connection)
    started = asyncio.Event()
    entrypoint = f"jmc4a_cancel_schedule_{uuid.uuid4().hex}"

    @app.schedule(entrypoint, "*/1 * * * * *")
    async def scheduled(_schedule) -> None:
        started.set()
        await asyncio.Event().wait()

    key, executor = next(iter(app.sm.registry.items()))
    try:
        await app.queries.insert_schedule({key: timedelta(0)})
        picked = (await app.queries.fetch_schedule({key: timedelta(seconds=1)}))[0]
        task = asyncio.create_task(app.sm.dispatch(executor, picked))
        await asyncio.wait_for(started.wait(), timeout=1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        stored = [row for row in await app.queries.peek_schedule() if row.id == picked.id]
        assert len(stored) == 1
        assert stored[0].status == "queued"
        assert stored[0].last_run is not None
    finally:
        await connection.close()


@pytest.mark.asyncio
async def test_pgqueuer_entrypoint_concurrency_is_database_global_across_workers(
    pgqueuer_contract_database: _PgQueuerContractDatabase,
) -> None:
    producer = await pgqueuer_contract_database.connect()
    first = await pgqueuer_contract_database.connect()
    second = await pgqueuer_contract_database.connect()
    try:
        suffix = uuid.uuid4().hex
        ids = await Queries.from_asyncpg_connection(producer).enqueue(
            ["cpu", "cpu"],
            [b"first", b"second"],
            priority=[0, 0],
            dedupe_key=[f"jmc4a-concurrency-{suffix}-1", f"jmc4a-concurrency-{suffix}-2"],
        )
        first_app = PgQueuer.from_asyncpg_connection(first)
        second_app = PgQueuer.from_asyncpg_connection(second)
        for app in (first_app, second_app):

            @app.entrypoint("cpu", concurrency_limit=1)
            async def cpu_entrypoint(_job) -> None:
                return None

        first_pick = [
            job
            async for job in first_app.qm.fetch_jobs(
                batch_size=2,
                global_concurrency_limit=None,
                heartbeat_timeout=timedelta(seconds=60),
            )
        ]
        second_pick = [
            job
            async for job in second_app.qm.fetch_jobs(
                batch_size=2,
                global_concurrency_limit=None,
                heartbeat_timeout=timedelta(seconds=60),
            )
        ]

        assert [job.id for job in first_pick] == [ids[0]]
        assert second_pick == []
    finally:
        await producer.close()
        await first.close()
        await second.close()
