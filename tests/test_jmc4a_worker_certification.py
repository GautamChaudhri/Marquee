"""A5 worker entrypoint, load, readiness, and production-boundary certification."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import anyio
import pytest
import pytest_asyncio
from pgqueuer import Queries
from pgqueuer.models import Context
from pgqueuer.models import Job as PgQueuerJob
from pgqueuer.types import QueueExecutionMode
from pydantic import ValidationError
from sqlalchemy import func, select

from marquee.config import Settings, settings
from marquee.core.jobs import pgqueuer_worker, readiness
from marquee.core.jobs.contracts import ExecutionClass
from marquee.core.jobs.delivery import DeliveryRejectedError, deliver_job
from marquee.core.jobs.manifest import JOB_DEFINITION_REGISTRY
from marquee.core.jobs.pgqueuer_worker import (
    create_worker,
    entrypoint_concurrency_limits,
)
from marquee.database import _get_engine
from marquee.models import JobAttempt


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


def _transport_job(*, entrypoint: str, payload: bytes = b"not-json") -> PgQueuerJob:
    now = datetime.now(UTC)
    return PgQueuerJob(
        id=1,
        priority=0,
        entrypoint=entrypoint,
        payload=payload,
        heartbeat=now,
        created=now,
        updated=now,
        execute_after=now,
        status="picked",
        attempts=0,
        queue_manager_id=uuid4(),
        headers=None,
    )


@pytest.mark.asyncio
async def test_expected_entrypoint_mismatch_fails_before_payload_or_attempt_admission(db) -> None:
    with pytest.raises(DeliveryRejectedError, match="does not match worker registration"):
        await deliver_job(
            _transport_job(entrypoint="control"),
            Context(cancellation=anyio.CancelScope()),
            expected_entrypoint="network",
        )
    assert await db.scalar(select(func.count()).select_from(JobAttempt)) == 0


@pytest.mark.asyncio
async def test_worker_registers_every_execution_class_with_explicit_limits(db) -> None:
    async with _get_engine().connect() as connection:
        raw = await connection.get_raw_connection()
        app = create_worker(raw.driver_connection)

    limits = entrypoint_concurrency_limits()
    assert set(limits) == {
        execution_class.value
        for execution_class in ExecutionClass
        if execution_class != ExecutionClass.MEDIA_WRITE
    }
    assert {
        entrypoint: executor.parameters.concurrency_limit
        for entrypoint, executor in app.qm.entrypoint_registry.items()
    } == limits
    assert settings.JOB_PGQUEUER_BATCH_SIZE <= settings.JOB_WORKER_CONCURRENCY
    assert settings.JOB_SAFETY_GATE_CONNECTIONS >= settings.JOB_WORKER_CONCURRENCY


@pytest.mark.asyncio
async def test_control_network_cpu_media_read_gpu_and_maintenance_canaries_obey_limits(
    db, installed_pgqueuer, monkeypatch
) -> None:
    canaries = (
        "control",
        "network",
        "cpu",
        "media_read",
        "gpu",
        "maintenance",
    )
    active_total = 0
    max_total = 0
    active_by_entrypoint: defaultdict[str, int] = defaultdict(int)
    max_by_entrypoint: defaultdict[str, int] = defaultdict(int)
    seen: set[str] = set()

    async def canary(_job, _context, *, expected_entrypoint: str) -> None:
        nonlocal active_total, max_total
        active_total += 1
        active_by_entrypoint[expected_entrypoint] += 1
        max_total = max(max_total, active_total)
        max_by_entrypoint[expected_entrypoint] = max(
            max_by_entrypoint[expected_entrypoint],
            active_by_entrypoint[expected_entrypoint],
        )
        seen.add(expected_entrypoint)
        try:
            await asyncio.sleep(0.02)
        finally:
            active_by_entrypoint[expected_entrypoint] -= 1
            active_total -= 1

    monkeypatch.setattr(pgqueuer_worker, "deliver_job", canary)
    entrypoints = [entrypoint for entrypoint in canaries for _ in range(4)]
    suffix = uuid4().hex
    await installed_pgqueuer.enqueue(
        entrypoints,
        [b"canary"] * len(entrypoints),
        priority=[0] * len(entrypoints),
        dedupe_key=[f"jmc4a-load-{suffix}-{index}" for index in range(len(entrypoints))],
    )

    async with _get_engine().connect() as connection:
        raw = await connection.get_raw_connection()
        app = create_worker(raw.driver_connection)
        await asyncio.wait_for(
            app.qm.run(
                dequeue_timeout=timedelta(milliseconds=10),
                batch_size=settings.JOB_PGQUEUER_BATCH_SIZE,
                mode=QueueExecutionMode.drain,
                max_concurrent_tasks=settings.JOB_WORKER_CONCURRENCY,
                heartbeat_timeout=timedelta(seconds=1),
            ),
            timeout=10,
        )

    limits = entrypoint_concurrency_limits()
    assert seen == set(canaries)
    assert max_total <= settings.JOB_WORKER_CONCURRENCY
    assert all(
        max_by_entrypoint[entrypoint] <= limits[entrypoint]
        for entrypoint in canaries
    )
    assert max_by_entrypoint["gpu"] == 1
    assert max_by_entrypoint["maintenance"] == 1


def test_worker_batch_and_safety_connection_coherence_is_validated() -> None:
    with pytest.raises(ValidationError, match="BATCH_SIZE cannot exceed"):
        Settings(
            _env_file=None,
            DEBUG=True,
            JOB_WORKER_CONCURRENCY=2,
            JOB_PGQUEUER_BATCH_SIZE=3,
            JOB_SAFETY_GATE_CONNECTIONS=4,
        )
    with pytest.raises(ValidationError, match="SAFETY_GATE_CONNECTIONS cannot be below"):
        Settings(
            _env_file=None,
            DEBUG=True,
            JOB_WORKER_CONCURRENCY=4,
            JOB_PGQUEUER_BATCH_SIZE=2,
            JOB_SAFETY_GATE_CONNECTIONS=3,
        )


def test_readiness_is_sanitized_and_reports_locked_jmc4a_boundaries() -> None:
    worker = readiness.worker_entrypoint_report()
    schedule = readiness.schedule_catalog_report()
    batch = readiness.batch_projection_report()
    budget = readiness.connection_budget_report()

    assert worker["status"] == "ok"
    assert worker["registered"] == sorted(
        item.value for item in ExecutionClass if item != ExecutionClass.MEDIA_WRITE
    )
    assert worker["enabled"] == ["control", "cpu", "media_read", "network"]
    assert worker["later_media_write_limit"] == 1
    assert worker["media_write_product_available"] is False
    assert schedule == {
        "status": "ok",
        "definition_count": 2,
        "keys": ["audio-subs-deep-scan", "library-sync"],
        "entrypoints": ["schedule_audio_subs_deep_scan", "schedule_library_sync"],
        "occurrence_policies": ["hourly_window", "interval_bucket"],
        "production_occurrences_enabled": False,
        "activated_keys": ["audio-subs-deep-scan", "library-sync"],
        "diagnostic_limit": 100,
    }
    assert batch["status"] == "ok"
    assert (batch["fixed_child_cap"], batch["dynamic_child_cap"]) == (500, 500)
    assert (budget["configured"], budget["maximum"], budget["within_budget"]) == (
        28,
        32,
        True,
    )
    serialized = str((worker, schedule, batch, budget)).lower()
    for forbidden in ("pgq_job_id", "schedule_id", "payload", "dedupe", "secret", "advisory"):
        assert forbidden not in serialized


def test_final_manifest_keeps_only_system_noop_enabled() -> None:
    assert len(JOB_DEFINITION_REGISTRY) == 49
    assert JOB_DEFINITION_REGISTRY.enabled_types == {
        "system_noop",
        "library_sync",
        "letterbox_detect",
        "letterbox_detect_episode",
        "letterbox_detect_tv_scope",
        "subtitle_scan",
        "subtitle_policy_audit",
        "dovi_analyze",
    }
    enabled = [definition for definition in JOB_DEFINITION_REGISTRY if definition.enabled]
    assert sorted(
        (definition.job_type, definition.entrypoint) for definition in enabled
    ) == [
        ("dovi_analyze", "media_read"),
        ("letterbox_detect", "media_read"),
        ("letterbox_detect_episode", "media_read"),
        ("letterbox_detect_tv_scope", "media_read"),
        ("library_sync", "network"),
        ("subtitle_policy_audit", "cpu"),
        ("subtitle_scan", "media_read"),
        ("system_noop", "control"),
    ]
    assert all(
        not definition.enabled
        for definition in JOB_DEFINITION_REGISTRY
        if definition.entrypoint == "media_write"
    )
