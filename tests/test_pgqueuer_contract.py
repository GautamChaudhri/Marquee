"""Contract tests for PgQueuer's caller-owned asyncpg connection bridge."""

from __future__ import annotations

import importlib.metadata
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass

import asyncpg
import pytest
import pytest_asyncio
from pgqueuer import Queries
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
        await Queries.from_asyncpg_connection(install_connection).install()
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
