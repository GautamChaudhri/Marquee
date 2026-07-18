from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from marquee.core.jobs import handlers_maintenance
from marquee.core.jobs.delivery import EXECUTION_HANDLERS
from marquee.core.jobs.manifest import JOB_DEFINITION_REGISTRY
from marquee.database import _get_session_factory
from marquee.models import Job, SystemMetricsSample

MAINTENANCE_TYPES = {
    "backup_create",
    "poster_maintenance",
    "pipeline_cache_clear",
    "job_retention_purge",
    "system_metrics_purge",
}


def _job(job_id: str, *, phase: str, when: datetime, parent_id: str | None = None) -> Job:
    return Job(
        id=job_id,
        type="system_noop",
        request={"echo": job_id},
        phase=phase,
        outcome="succeeded" if phase == "terminal" else None,
        desired_state="run",
        priority=50,
        eligible_at=when,
        dispatch_generation=1,
        parent_id=parent_id,
        root_id=parent_id or job_id,
        correlation_id=parent_id or job_id,
        trigger_kind="system",
        feature_area="system",
        presentation_family="system",
        subject_kind="system_work",
        subject_reference="system_noop",
        subject_snapshot={
            "version": 1,
            "kind": "system_work",
            "display_id": f"system:{job_id}",
            "display_name": job_id,
            "snapshot_at": when.isoformat(),
            "work": "system_noop",
        },
        progress_sequence=0,
        queued_at=when,
        terminal_at=when if phase == "terminal" else None,
    )


def _context(request: dict[str, object], *, current_job_id: str = "maintenance-current"):
    async def owns_current_attempt(_session):
        return True

    async def progress_stage(*_args, **_kwargs):
        return None

    return SimpleNamespace(
        request=request,
        delivery=SimpleNamespace(canonical_job_id=current_job_id),
        attempt=SimpleNamespace(attempt_id=1, fence_token=1),
        cancellation=SimpleNamespace(cancel_called=False),
        progress=SimpleNamespace(stage=progress_stage),
        writer=SimpleNamespace(owns_current_attempt=owns_current_attempt),
        session_factory=_get_session_factory(),
    )


def test_canonical_maintenance_definitions_are_exclusive_single_attempt_handlers() -> None:
    assert EXECUTION_HANDLERS.keys() >= MAINTENANCE_TYPES
    for job_type in MAINTENANCE_TYPES:
        definition = JOB_DEFINITION_REGISTRY.get(job_type)
        assert definition.enabled is True
        assert definition.entrypoint == "maintenance"
        assert definition.safety_policy.exclusive_maintenance is True
        assert definition.retry_policy.max_attempts == 1
        assert definition.request.models[1].__name__ != "BuiltInIntentV1"


@pytest.mark.asyncio
async def test_metrics_purge_is_bounded_confirmed_and_cancellable(
    db: AsyncSession,
) -> None:
    now = datetime.now(UTC)
    old = SystemMetricsSample(
        cpu={}, ram={}, disk={}, net={}, active_jobs=[], created_at=now - timedelta(days=60)
    )
    recent = SystemMetricsSample(
        cpu={}, ram={}, disk={}, net={}, active_jobs=[], created_at=now
    )
    db.add_all([old, recent])
    await db.commit()
    await db.refresh(old)
    await db.refresh(recent)

    context = _context(
        {"dry_run": True, "retention_days": 30, "max_records": 10, "batch_size": 1}
    )
    plan = await handlers_maintenance.execute_system_metrics_purge(context)
    assert plan["planned_count"] == 1
    assert plan["counts"] == {"samples": 1}

    context.request = {
        "dry_run": False,
        "retention_days": 30,
        "max_records": 10,
        "batch_size": 1,
        "confirmed_plan_checksum": plan["plan_checksum"],
    }
    context.cancellation.cancel_called = True
    cancelled = await handlers_maintenance.execute_system_metrics_purge(context)
    assert cancelled["cancelled"] is True
    assert cancelled["deleted_count"] == 0

    context.cancellation.cancel_called = False
    result = await handlers_maintenance.execute_system_metrics_purge(context)
    assert result["deleted_count"] == 1
    async with _get_session_factory()() as session:
        assert await session.get(SystemMetricsSample, old.id) is None
        assert await session.get(SystemMetricsSample, recent.id) is not None


@pytest.mark.asyncio
async def test_job_retention_preserves_active_dependencies(
    db: AsyncSession,
) -> None:
    old = datetime.now(UTC) - timedelta(days=60)
    eligible = _job("retention-eligible", phase="terminal", when=old)
    protected = _job("retention-protected", phase="terminal", when=old)
    child = _job(
        "retention-active-child", phase="running", when=datetime.now(UTC), parent_id=protected.id
    )
    db.add_all([eligible, protected, child])
    await db.commit()

    context = _context(
        {"dry_run": True, "retention_days": 30, "max_records": 10, "batch_size": 1}
    )
    plan = await handlers_maintenance.execute_job_retention_purge(context)
    assert plan["planned_count"] == 1
    assert plan["counts"] == {"jobs": 1}

    context.request = {
        "dry_run": False,
        "retention_days": 30,
        "max_records": 10,
        "batch_size": 1,
        "confirmed_plan_checksum": plan["plan_checksum"],
    }
    result = await handlers_maintenance.execute_job_retention_purge(context)
    assert result["deleted_count"] == 1
    async with _get_session_factory()() as session:
        remaining = set(
            (
                await session.scalars(
                    select(Job.id).where(
                        Job.id.in_([eligible.id, protected.id, child.id])
                    )
                )
            ).all()
        )
    assert remaining == {protected.id, child.id}


@pytest.mark.asyncio
async def test_evidence_only_retention_is_bounded_and_does_not_require_delete_confirmation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    async def artifacts(**_kwargs):
        calls.append("artifacts")
        return {"claimed": 2, "expired": 1, "deleted": 1, "missing": 0, "failed": 1}

    async def logs(**_kwargs):
        calls.append("logs")
        return {"claimed": 1, "expired": 1, "deleted": 0, "missing": 1, "failed": 0}

    monkeypatch.setattr(handlers_maintenance, "expire_artifacts", artifacts)
    monkeypatch.setattr(handlers_maintenance, "expire_logs", logs)
    result = await handlers_maintenance.execute_job_retention_purge(
        _context({"retention_days": 30, "evidence_only": True})
    )
    assert calls == ["artifacts", "logs"]
    assert result["planned_count"] == 3
    assert result["processed_count"] == 2
    assert result["counts"] == {
        "artifacts": 1,
        "logs": 1,
        "missing": 1,
        "failed": 1,
    }
