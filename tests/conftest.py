"""Shared pytest fixtures for Marquee tests."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from marquee.database import Base, _get_engine, _get_session_factory


@pytest.fixture
async def db():
    """Yield an async database session with all tables created.

    Each test gets a fresh transaction that is rolled back afterward,
    so tests are isolated from each other.
    """
    engine = _get_engine()

    # Ensure all tables exist
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = _get_session_factory()
    async with factory() as session:
        async with session.begin():
            yield session
            await session.rollback()
