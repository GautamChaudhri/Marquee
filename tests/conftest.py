"""Shared pytest fixtures for Marquee tests."""

import pytest
import pytest_asyncio
from sqlalchemy import delete

from marquee.config import settings
from marquee.database import (
    Base,
    _get_engine,
    _get_session_factory,
    close_db,
)
from marquee.models import (
    ArtworkEvent,
    Episode,
    EpisodeMediaFile,
    LetterboxEvent,
    LetterboxState,
    ManagedSubtitleAsset,
    ManagedSubtitleBinding,
    MediaBackup,
    MediaBatch,
    MediaFile,
    MediaJob,
    MediaJobEvent,
    Movie,
    PipelineRun,
    Season,
    Series,
    SubtitleInventory,
    SubtitlePolicy,
    SubtitlePolicyBinding,
    SubtitleTrack,
)


@pytest_asyncio.fixture(scope="session", autouse=True)
async def isolated_database(tmp_path_factory):
    """Force the entire test session onto a temporary SQLite database."""
    original_data_dir = settings.DATA_DIR
    await close_db()
    settings.DATA_DIR = str(tmp_path_factory.mktemp("marquee-test-data"))
    try:
        yield
    finally:
        await close_db()
        settings.DATA_DIR = original_data_dir


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
        # Clean all rows from previous tests (children before parents).
        for model in (
            ArtworkEvent,
            LetterboxEvent,
            LetterboxState,
            PipelineRun,
            MediaJobEvent,
            MediaBackup,
            MediaJob,
            MediaBatch,
            SubtitleTrack,
            SubtitleInventory,
            ManagedSubtitleBinding,
            ManagedSubtitleAsset,
            SubtitlePolicyBinding,
            SubtitlePolicy,
            EpisodeMediaFile,
            MediaFile,
            Episode,
            Season,
            Series,
            Movie,
        ):
            await session.execute(delete(model))
        await session.commit()

        yield session
        await session.rollback()
        await session.close()
