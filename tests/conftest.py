"""Shared pytest fixtures for Marquee tests."""

import uuid

import pytest
import pytest_asyncio
from pgqueuer import Queries
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

import marquee.database as database_module
from marquee.config import settings
from marquee.database import (
    Base,
    _get_engine,
    _get_session_factory,
    close_db,
    reset_database,
)

pytest_plugins = ["tests.support.jmc5b_harness"]


def pytest_configure(config):
    """Collect executed-node evidence for JMC6I §7 executable certification."""
    from tests.support.jmc6i_certification import ExecutionEvidence

    config.jmc6i_execution_evidence = ExecutionEvidence()


def pytest_collection_modifyitems(config, items):
    """Record the selected node set and run the certification report last.

    The certification report consumes evidence produced by every other selected
    test, so its module is deterministically moved to the end of the session.
    """
    evidence = getattr(config, "jmc6i_execution_evidence", None)
    if evidence is None:
        return
    for item in items:
        evidence.mark_selected(item.nodeid)
    certification = [
        item for item in items if "test_jmc6i_executable_certification" in item.nodeid
    ]
    if certification:
        others = [
            item for item in items if "test_jmc6i_executable_certification" not in item.nodeid
        ]
        items[:] = others + certification


_JMC6I_EVIDENCE: dict = {}


def pytest_sessionstart(session):
    _JMC6I_EVIDENCE["evidence"] = getattr(session.config, "jmc6i_execution_evidence", None)


def pytest_runtest_logreport(report):
    """Record each node's true outcome (a skip or xfail is never 'executed')."""
    evidence = _JMC6I_EVIDENCE.get("evidence")
    if evidence is None:
        return
    if report.when == "call":
        if report.passed and not hasattr(report, "wasxfail"):
            evidence.record(report.nodeid, "passed")
        elif report.skipped:
            evidence.record(report.nodeid, "skipped")
        else:
            evidence.record(report.nodeid, "failed")
    elif report.when == "setup" and (report.skipped or report.failed):
        evidence.record(report.nodeid, "skipped" if report.skipped else "failed")


@pytest_asyncio.fixture(scope="session", autouse=True)
async def isolated_database(tmp_path_factory):
    """Provision an isolated PostgreSQL schema for the test session."""
    original_data_dir = settings.DATA_DIR
    schema = f"test_{uuid.uuid4().hex}"
    admin_engine = create_async_engine(
        settings.db_url_resolved,
        echo=False,
        poolclass=NullPool,
    )
    await close_db()
    settings.DATA_DIR = str(tmp_path_factory.mktemp("marquee-test-data"))
    try:
        async with admin_engine.begin() as conn:
            await conn.execute(text(f'CREATE SCHEMA "{schema}"'))

        test_engine = create_async_engine(
            settings.db_url_resolved,
            echo=False,
            poolclass=NullPool,
            connect_args={"server_settings": {"search_path": schema}},
        )
        database_module._engine = test_engine
        database_module._session_factory = async_sessionmaker(
            bind=test_engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )

        async with test_engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        yield
    finally:
        await close_db()
        async with admin_engine.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await admin_engine.dispose()
        settings.DATA_DIR = original_data_dir


@pytest.fixture(scope="session", autouse=True)
def _auth_disabled_in_tests():
    """Run the suite with DEBUG=True so the global API-key gate is bypassed.
    Auth enforcement itself is covered explicitly in test_auth.py."""
    original = settings.DEBUG
    settings.DEBUG = True
    yield
    settings.DEBUG = original


@pytest.fixture
async def db():
    """Yield a clean async database session.

    All tables are truncated before each test so tests never see
    data from previous tests.  The ``SyncService`` (and other code
    under test) calls ``commit()`` itself — this fixture does NOT
    wrap in a transaction.
    """
    engine = _get_engine()

    # Ensure tables exist
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = _get_session_factory()
    async with factory() as session:
        await reset_database(session)
        from marquee.core.configuration_cache import configuration_provider

        await configuration_provider.refresh_from_session(session, initial=True)
        await session.rollback()
        yield session
        await session.rollback()
        await session.close()


@pytest_asyncio.fixture
async def installed_pgqueuer(db: AsyncSession) -> Queries:
    """Install PgQueuer tables for route tests that submit real canonical tickets."""
    async with _get_engine().connect() as connection:
        raw = await connection.get_raw_connection()
        queries = Queries.from_asyncpg_connection(raw.driver_connection)
        await queries.install()
        try:
            yield queries
        finally:
            await db.rollback()
            await queries.uninstall()
