"""Tests for POST /api/system/reset-db."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select, text

from marquee.database import _get_session_factory
from marquee.main import app
from marquee.models import Job, Movie, RuntimeInstance, SchemaContract, WorkerNode


@pytest_asyncio.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.mark.asyncio
async def test_reset_db_endpoint_clears_application_tables(db, client: AsyncClient):
    movie = Movie(title="Reset Me", year=2024, folder_path="/movies/reset", tmdb_id=424242)
    job = Job(
        id="reset-job",
        type="maintenance",
        payload_version=1,
        request={},
        phase="queued",
        root_id="reset-job",
        subject_snapshot={"version": 1, "kind": "system", "label": "Reset test"},
    )
    db.add_all([movie, job])
    await db.commit()

    before_movies = await db.scalar(select(func.count()).select_from(Movie))
    before_jobs = await db.scalar(select(func.count()).select_from(Job))
    assert before_movies == 1
    assert before_jobs == 1

    await db.close()
    response = await client.post("/api/system/reset-db")

    assert response.status_code == 200
    assert response.json()["status"] == "reset"
    assert response.json()["tables_cleared"] > 0

    factory = _get_session_factory()
    async with factory() as verify_db:
        after_movies = await verify_db.scalar(select(func.count()).select_from(Movie))
        after_jobs = await verify_db.scalar(select(func.count()).select_from(Job))
        assert after_movies == 0
        assert after_jobs == 0


@pytest.mark.asyncio
async def test_reset_db_preserves_runtime_registration(db, client: AsyncClient):
    """A reset must not delete the rows live processes and the migrator registered.

    A running worker holds its ``runtime_instances`` id for the life of the
    process and every ``job_attempts`` insert references it, so deleting the row
    fails each following attempt on the foreign key. Worker and scheduler
    startup verifies against ``schema_contracts`` and fails closed, so deleting
    that blocks the next boot until the migration command is run again.
    """
    now = datetime.now(UTC)
    db.add_all(
        [
            RuntimeInstance(
                id="11111111-2222-3333-4444-555555555555",
                role="worker",
                node_label="test-node",
                build="test",
                host_boot_id="boot",
                process_id=4242,
                process_start_ticks=1,
                process_group_id=4242,
                advertised_entrypoints=["gpu"],
                capabilities={},
                readiness="ready",
                started_at=now,
                last_heartbeat_at=now,
                heartbeat_expires_at=now + timedelta(seconds=60),
            ),
            WorkerNode(id="test-node", capabilities={}, readiness="ready"),
            SchemaContract(
                component="marquee",
                expected_version="0016_poster_scope",
                catalog_fingerprint="f" * 64,
                verified_at=now,
                verifier_build="test",
            ),
            Movie(title="Reset Me", year=2024, folder_path="/movies/reset", tmdb_id=424243),
        ]
    )
    await db.commit()
    await db.close()

    response = await client.post("/api/system/reset-db")
    assert response.status_code == 200

    factory = _get_session_factory()
    async with factory() as verify_db:
        assert await verify_db.scalar(select(func.count()).select_from(Movie)) == 0
        assert await verify_db.scalar(select(func.count()).select_from(RuntimeInstance)) == 1
        assert await verify_db.scalar(select(func.count()).select_from(WorkerNode)) == 1
        assert await verify_db.scalar(select(func.count()).select_from(SchemaContract)) == 1


@pytest.mark.asyncio
async def test_reset_db_clears_the_transport_queue(db, installed_pgqueuer, client: AsyncClient):
    """Job rows and their PgQueuer entries have to clear together.

    ``pgqueuer`` is not a SQLAlchemy model, so a metadata-driven truncate leaves
    transport rows naming jobs that no longer exist.
    """
    await installed_pgqueuer.enqueue(["network"], [None], [0])
    factory = _get_session_factory()
    async with factory() as seeded:
        assert await seeded.scalar(text("SELECT count(*) FROM pgqueuer")) == 1

    await db.close()
    response = await client.post("/api/system/reset-db")
    assert response.status_code == 200

    async with factory() as verify_db:
        assert await verify_db.scalar(text("SELECT count(*) FROM pgqueuer")) == 0
