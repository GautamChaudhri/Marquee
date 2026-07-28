"""Tests for POST /api/system/reset-db."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select, text

from marquee.database import _get_session_factory
from marquee.db_migration import ALEMBIC_HEAD
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
                expected_version=ALEMBIC_HEAD,
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


@pytest.mark.asyncio
async def test_reset_requests_cancellation_before_wiping_live_jobs(db, client: AsyncClient):
    """A handler must be told to stop, not merely have its rows deleted underneath it.

    Cancellation is the only signal the delivery loop watches, so without it a
    running job keeps its runner child alive and writes progress into rows that no
    longer exist until it finishes on its own.
    """
    observed: dict[str, str] = {}

    for phase in ("planned", "queued", "running", "stopping"):
        db.add(
            Job(
                id=f"live-{phase}",
                type="maintenance",
                payload_version=1,
                request={},
                phase=phase,
                desired_state="run",
                root_id=f"live-{phase}",
                subject_snapshot={"version": 1, "kind": "system", "label": phase},
            )
        )
    db.add(
        Job(
            id="already-done",
            type="maintenance",
            payload_version=1,
            request={},
            phase="terminal",
            outcome="succeeded",
            terminal_at=datetime.now(UTC),
            desired_state="run",
            root_id="already-done",
            subject_snapshot={"version": 1, "kind": "system", "label": "done"},
        )
    )
    await db.commit()

    # The worker's part of the handshake: observe the intent, then go terminal.
    async def settle() -> None:
        async with _get_session_factory()() as session:
            for job in (await session.scalars(select(Job).where(Job.phase != "terminal"))).all():
                observed[job.id] = job.desired_state
                job.phase = "terminal"
                job.outcome = "cancelled"
                job.terminal_at = datetime.now(UTC)
            await session.commit()

    from marquee import database as database_module

    original = database_module._await_quiescence

    async def quiesce_with_worker(session, *, timeout_seconds):
        await settle()
        return await original(session, timeout_seconds=timeout_seconds)

    database_module._await_quiescence = quiesce_with_worker
    try:
        response = await client.post("/api/system/reset-db")
    finally:
        database_module._await_quiescence = original

    assert response.status_code == 200, response.text
    body = response.json()
    # Every live job was signalled; the terminal one was left alone.
    assert body["jobs_stop_requested"] == 4
    assert observed == {
        f"live-{phase}": "cancel" for phase in ("planned", "queued", "running", "stopping")
    }
    assert body["jobs_still_running"] == []
    async with _get_session_factory()() as session:
        assert await session.scalar(select(func.count()).select_from(Job)) == 0


@pytest.mark.asyncio
async def test_reset_reports_jobs_that_refused_to_stop(db, client: AsyncClient, monkeypatch):
    """The wipe still happens, but a job that never released is named, not hidden."""
    from marquee import database as database_module

    db.add(
        Job(
            id="wedged",
            type="maintenance",
            payload_version=1,
            request={},
            phase="running",
            desired_state="run",
            root_id="wedged",
            subject_snapshot={"version": 1, "kind": "system", "label": "wedged"},
        )
    )
    await db.commit()
    monkeypatch.setattr(database_module, "_QUIESCE_POLL_SECONDS", 0.01)
    monkeypatch.setattr(database_module, "_QUIESCE_TIMEOUT_SECONDS", 0.05)

    response = await client.post("/api/system/reset-db")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["jobs_stop_requested"] == 1
    assert body["jobs_still_running"] == ["wedged"]
    async with _get_session_factory()() as session:
        assert await session.scalar(select(func.count()).select_from(Job)) == 0


@pytest.mark.asyncio
async def test_reset_removes_the_job_evidence_the_wiped_rows_owned(db, client: AsyncClient):
    """Truncating the job tables strands every workspace and artifact on disk."""
    from pathlib import Path

    from marquee.config import settings

    root = Path(settings.DATA_DIR)
    workspace = root / "jobs" / "workspaces" / "some-job" / "1-1"
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "candidate-000.jpg").write_bytes(b"poster")
    artifact = root / "jobs" / "evidence" / "artifacts" / "some-job" / "7"
    artifact.mkdir(parents=True, exist_ok=True)
    (artifact / "artifact-7.json").write_text("{}", encoding="utf-8")
    unrelated = root / "cache" / "keep-me.txt"
    unrelated.parent.mkdir(parents=True, exist_ok=True)
    unrelated.write_text("cached", encoding="utf-8")

    response = await client.post("/api/system/reset-db")

    assert response.status_code == 200, response.text
    assert response.json()["evidence_entries_removed"] >= 2
    assert not (root / "jobs" / "workspaces" / "some-job").exists()
    assert not (root / "jobs" / "evidence" / "artifacts" / "some-job").exists()
    # The roots themselves survive so the platform keeps writing without a restart.
    assert (root / "jobs" / "workspaces").is_dir()
    assert (root / "jobs" / "evidence").is_dir()
    # Caches and backups are not owned by any wiped row.
    assert unrelated.read_text(encoding="utf-8") == "cached"


@pytest.mark.asyncio
async def test_reset_does_not_wait_on_work_that_owns_no_process(db, client: AsyncClient):
    """Queued work has no handler to unwind, so it must not cost a timeout.

    Its transport ticket is truncated moments later; blocking on it would make the
    ordinary reset take the full quiescence budget for no benefit.
    """
    from marquee import database as database_module

    db.add(
        Job(
            id="never-picked",
            type="maintenance",
            payload_version=1,
            request={},
            phase="queued",
            desired_state="run",
            root_id="never-picked",
            subject_snapshot={"version": 1, "kind": "system", "label": "queued"},
        )
    )
    await db.commit()

    started = datetime.now(UTC)
    response = await client.post("/api/system/reset-db")
    elapsed = (datetime.now(UTC) - started).total_seconds()

    assert response.status_code == 200, response.text
    assert response.json()["jobs_stop_requested"] == 1
    assert response.json()["jobs_still_running"] == []
    assert elapsed < database_module._QUIESCE_TIMEOUT_SECONDS / 2
