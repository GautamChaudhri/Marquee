"""The library_sync job: canonical submission and handler behaviour.

Library sync is a read-only network job. Covers its definition, submission through the
canonical path, rejection of unknown request fields, the 202 the sync route returns while
reusing an active run, and the no-change outcome when no source is configured."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from pgqueuer import Queries

from marquee.config import settings
from marquee.core.jobs.contracts import TriggerKind
from marquee.core.jobs.handlers_library import execute_library_sync
from marquee.core.jobs.manifest import JOB_DEFINITION_REGISTRY
from marquee.core.jobs.submission import (
    Initiator,
    SubjectLocator,
    submit_job,
)
from marquee.database import _get_engine, _get_session_factory
from marquee.main import app
from marquee.models import Job


@pytest_asyncio.fixture
async def client() -> AsyncClient:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest_asyncio.fixture(autouse=True)
async def installed_pgqueuer(db) -> Queries:
    async with _get_engine().connect() as connection:
        raw = await connection.get_raw_connection()
        queries = Queries.from_asyncpg_connection(raw.driver_connection)
        await queries.install()
        try:
            yield queries
        finally:
            await db.rollback()
            await queries.uninstall()


def test_library_sync_definition_is_enabled_read_only_network() -> None:
    definition = JOB_DEFINITION_REGISTRY.get("library_sync")
    assert definition.enabled is True
    assert definition.execution_class.value == "network"
    assert definition.effect_safety.value == "read_only"
    # honest indeterminate stages, no fabricated percentage
    assert definition.progress_policy.strategy.value == "indeterminate"
    assert definition.progress_policy.stage_keys == {
        "connecting",
        "sync_movies",
        "sync_series",
        "finalizing",
    }
    # dispatch is enabled only on the network entrypoint
    assert JOB_DEFINITION_REGISTRY.for_dispatch("library_sync", entrypoint="network").enabled


@pytest.mark.asyncio
async def test_library_sync_submits_as_canonical_network_job(db) -> None:
    async with db.begin():
        result = await submit_job(
            db,
            job_type="library_sync",
            request={"source": "manual"},
            subject=SubjectLocator(kind="maintenance_scope", reference="library-sync"),
            trigger=TriggerKind.MANUAL,
            initiator=Initiator(kind="user", display_name="Operator"),
            idempotency_key="library_sync:manual-smoke",
        )
    assert result.disposition == "created"
    assert result.phase == "queued"

    job = await db.get(Job, result.job_id)
    assert job is not None
    assert job.type == "library_sync"
    assert job.pgq_job_id is not None
    assert job.plan["entrypoint"] == "network"
    assert job.plan["effect_safety"] == "read_only"
    assert job.request == {"source": "manual"}
    assert job.subject_kind == "maintenance_scope"
    assert job.subject_snapshot["kind"] == "maintenance_scope"

    # end the implicit read transaction before opening the next caller transaction
    await db.rollback()
    # idempotent re-submit returns the same job
    async with db.begin():
        again = await submit_job(
            db,
            job_type="library_sync",
            request={"source": "manual"},
            subject=SubjectLocator(kind="maintenance_scope", reference="library-sync"),
            trigger=TriggerKind.MANUAL,
            initiator=Initiator(kind="user", display_name="Operator"),
            idempotency_key="library_sync:manual-smoke",
        )
    assert again.disposition == "reused"
    assert again.job_id == result.job_id


@pytest.mark.asyncio
async def test_library_sync_rejects_unknown_request_field(db) -> None:
    from marquee.core.jobs.submission import SubmissionValidationError

    with pytest.raises(SubmissionValidationError):
        async with db.begin():
            await submit_job(
                db,
                job_type="library_sync",
                request={"source": "manual", "not_a_field": True},
                subject=SubjectLocator(kind="maintenance_scope", reference="library-sync"),
                trigger=TriggerKind.MANUAL,
                initiator=None,
                idempotency_key="library_sync:bad-field",
            )


@pytest.mark.asyncio
async def test_sync_all_route_submits_202_and_reuses_active(client: AsyncClient, db) -> None:
    response = await client.post("/api/sync/all")
    assert response.status_code == 202
    body = response.json()
    assert body["disposition"] == "created"
    assert body["phase"] == "queued"
    assert body["snapshot_url"] == f"/api/jobs/{body['job_id']}/snapshot"
    assert not any(key in body for key in ("pgq_job_id", "status", "duration_seconds"))

    job = await db.get(Job, body["job_id"])
    assert job is not None and job.type == "library_sync"
    assert job.trigger_kind == "manual"

    # an in-flight sync is reused rather than starting a concurrent full-library sync
    again = await client.post("/api/sync/all")
    assert again.status_code == 202
    assert again.json()["disposition"] == "reused"
    assert again.json()["job_id"] == body["job_id"]


@pytest.mark.asyncio
async def test_handler_reports_no_change_when_no_source_configured(
    db,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del db
    monkeypatch.setattr(settings, "RADARR_URL", "")
    monkeypatch.setattr(settings, "RADARR_API_KEY", None)
    monkeypatch.setattr(settings, "SONARR_URL", "")
    monkeypatch.setattr(settings, "SONARR_API_KEY", None)
    monkeypatch.setattr(settings, "TMDB_READ_ACCESS_TOKEN", None)
    context = SimpleNamespace(
        cancellation=SimpleNamespace(cancel_called=False),
        session_factory=_get_session_factory(),
    )
    result = await execute_library_sync(context)  # type: ignore[arg-type]
    assert result["outcome"] == "no_change"
    assert result["summary"]["reason"] == "no_source_configured"
    assert result["summary"]["sources"] == {
        "radarr": "skipped",
        "sonarr": "skipped",
        "tmdb": "skipped",
    }
