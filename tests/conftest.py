"""Shared pytest fixtures for Marquee tests."""

import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from marquee.database import Base, _get_engine, _get_session_factory
from marquee.models import Episode, Movie, Season, Series


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
        # Clean all rows from previous tests
        for model in (Episode, Season, Series, Movie):
            await session.execute(delete(model))
        await session.commit()

        yield session
        await session.rollback()
        await session.close()
