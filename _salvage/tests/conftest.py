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

# Onboarding ships disabled (see Settings.ONBOARDING_ENABLED), and main.py registers
# its router at import time. Enable it here — before any test module imports
# marquee.main — so the flow stays covered while staying off for real deployments.
settings.ONBOARDING_ENABLED = True

pytest_plugins = ["tests.support.process_harness"]


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
        # quiesce=False: no worker is running against this schema, so there is
        # nothing to wait for and nothing to signal.
        await reset_database(session, include_runtime_registration=True, quiesce=False)
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
