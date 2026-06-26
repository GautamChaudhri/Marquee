"""system_metrics_purge: deletes old SystemMetricsSample rows.

Mirrors test_job_retention.py's structure — the samples this deletes are
written by the lightweight asyncio sampler, not by a Job, but the daily
cleanup itself is a real, infrequent Job and tested the same way.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from marquee.config import settings
from marquee.core.jobs.builtin_handlers import system_metrics_purge
from marquee.core.jobs.manager import job_manager
from marquee.core.jobs.scheduler import reconcile_schedules
from marquee.models import JobSchedule, SystemMetricsSample


async def _make_sample(db, *, age_days: int) -> SystemMetricsSample:
    sample = SystemMetricsSample(
        cpu={"avg": 1.0}, gpu=None, ram={"pct": 1.0}, disk={"pct": 1.0}, net={}, active_jobs=[]
    )
    db.add(sample)
    await db.commit()
    sample.created_at = datetime.now(UTC) - timedelta(days=age_days)
    await db.commit()
    return sample


async def test_purge_deletes_old_samples(db, monkeypatch):
    monkeypatch.setattr(settings, "METRICS_RETENTION_DAYS", 14)
    sample = await _make_sample(db, age_days=20)
    sample_id = sample.id
    job = await job_manager.create(db, job_type="system_noop")

    result = await system_metrics_purge(job)
    db.expire_all()

    assert result["samples_deleted"] == 1
    assert (await db.get(SystemMetricsSample, sample_id)) is None


async def test_purge_keeps_samples_within_retention_window(db, monkeypatch):
    monkeypatch.setattr(settings, "METRICS_RETENTION_DAYS", 14)
    sample = await _make_sample(db, age_days=2)
    sample_id = sample.id
    job = await job_manager.create(db, job_type="system_noop")

    result = await system_metrics_purge(job)

    assert result["samples_deleted"] == 0
    assert (await db.get(SystemMetricsSample, sample_id)) is not None


async def test_reconcile_schedules_enables_metrics_purge_unconditionally(db):
    await reconcile_schedules()

    schedule = await db.get(JobSchedule, "system-metrics-purge")
    assert schedule is not None
    assert schedule.enabled is True
    assert schedule.job_type == "system_metrics_purge"
    assert schedule.interval_seconds == 86400
