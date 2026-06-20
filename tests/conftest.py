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
    Job,
    JobAttempt,
    JobEvent,
    JobResource,
    JobResourceReservation,
    JobSchedule,
    JobWorker,
    LetterboxEvent,
    LetterboxReencodeArtifact,
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
    """Use SQLite only for isolated unit tests; production is PostgreSQL."""
    original_data_dir = settings.DATA_DIR
    original_db_url = settings.DB_URL
    await close_db()
    settings.DATA_DIR = str(tmp_path_factory.mktemp("marquee-test-data"))
    settings.DB_URL = "sqlite+aiosqlite:///./data/marquee.db"
    try:
        async with _get_engine().begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        yield
    finally:
        await close_db()
        settings.DATA_DIR = original_data_dir
        settings.DB_URL = original_db_url


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
        # Clean all rows from previous tests (children before parents).
        for model in (
            JobResourceReservation,
            JobEvent,
            JobAttempt,
            JobSchedule,
            JobWorker,
            Job,
            JobResource,
            ArtworkEvent,
            LetterboxEvent,
            LetterboxReencodeArtifact,
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
