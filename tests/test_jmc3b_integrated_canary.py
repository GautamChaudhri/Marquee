from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import anyio
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from pgqueuer import Queries
from pgqueuer.models import Context
from pgqueuer.models import Job as PgQueuerJob
from sqlalchemy import select

from marquee.core.jobs.commands import create_system_noop
from marquee.core.jobs.delivery import deliver_control_job
from marquee.database import _get_engine
from marquee.main import app
from marquee.models import Job, JobArtifact, JobAttempt, JobEvent, JobLog
from marquee.models.job import JobDispatch


@pytest_asyncio.fixture(autouse=True)
async def installed_pgqueuer(db):
    async with _get_engine().connect() as connection:
        raw = await connection.get_raw_connection()
        queries = Queries.from_asyncpg_connection(raw.driver_connection)
        await queries.install()
        try:
            yield
        finally:
            await db.rollback()
            await queries.uninstall()


async def test_fixed_canary_integrates_progress_logs_artifacts_events_and_apis(db) -> None:
    await db.rollback()
    job = await create_system_noop(
        db,
        payload={"echo": "jmc3b-integrated"},
        idempotency_key=f"system_noop:{uuid4().hex}",
    )
    job_id = job.id
    dispatch = await db.scalar(
        select(JobDispatch).where(
            JobDispatch.job_id == job_id,
            JobDispatch.generation == 1,
        )
    )
    assert dispatch is not None and dispatch.pgq_job_id is not None
    pgq_job_id = dispatch.pgq_job_id
    await db.rollback()

    now = datetime.now(UTC)
    transport = PgQueuerJob(
        id=pgq_job_id,
        priority=50,
        created=now,
        updated=now,
        heartbeat=now,
        execute_after=now,
        status="picked",
        entrypoint="control",
        payload=(
            f'{{"dispatch_generation":1,"job_id":"{job_id}","payload_version":1}}'.encode()
        ),
        attempts=0,
        queue_manager_id=uuid4(),
        headers=None,
    )
    await deliver_control_job(transport, Context(cancellation=anyio.CancelScope()))

    await db.rollback()
    db.expire_all()
    canonical = await db.get(Job, job_id)
    attempt = (
        await db.scalars(select(JobAttempt).where(JobAttempt.job_id == job_id))
    ).one()
    log = (await db.scalars(select(JobLog).where(JobLog.job_id == job_id))).one()
    artifact = (
        await db.scalars(select(JobArtifact).where(JobArtifact.job_id == job_id))
    ).one()
    events = list(
        await db.scalars(
            select(JobEvent).where(JobEvent.job_id == job_id).order_by(JobEvent.id)
        )
    )

    assert canonical is not None
    assert (canonical.phase, canonical.outcome) == ("terminal", "succeeded")
    assert canonical.progress_sequence == 2
    assert canonical.progress["freshness"] == "terminal"
    assert (attempt.phase, attempt.outcome) == ("finished", "succeeded")
    assert log.seal_status == "sealed" and log.compression == "gzip" and log.checksum
    assert artifact.status == "available"
    assert artifact.virtual_source == {"document": "result", "version": 1}
    assert [event.id for event in events] == sorted({event.id for event in events})
    keys = [event.event_key for event in events]
    assert keys.count("progress.updated") == 2
    assert {"attempt.started", "job.succeeded", "log.available", "artifact.available"} <= set(
        keys
    )

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        snapshot = await client.get(f"/api/jobs/{job_id}/snapshot")
        presentation = await client.get(f"/api/jobs/{job_id}/presentation")
        log_page = await client.get(f"/api/jobs/{job_id}/attempts/{attempt.id}/logs")
        log_download = await client.get(
            f"/api/jobs/{job_id}/attempts/{attempt.id}/logs/download"
        )
        artifact_page = await client.get(f"/api/jobs/{job_id}/artifacts")
        artifact_download = await client.get(
            f"/api/jobs/{job_id}/artifacts/{artifact.id}/download"
        )
        event_page = await client.get(f"/api/jobs/{job_id}/events")

    assert snapshot.status_code == presentation.status_code == 200
    assert snapshot.json()["progress"]["freshness"] == "terminal"
    assert presentation.json()["evidence"] == {
        "logs_available": True,
        "artifacts_available": True,
    }
    assert log_page.status_code == log_download.status_code == 200
    assert log_download.headers["content-type"] == "application/gzip"
    item = artifact_page.json()["items"][0]
    assert item["available"] is True and "storage_key" not in item
    assert artifact_download.status_code == 200
    assert artifact_download.json()["value"]["summary"]["echo"] == "jmc3b-integrated"
    assert event_page.status_code == 200
    assert len({item["id"] for item in event_page.json()["items"]}) == len(
        event_page.json()["items"]
    )
