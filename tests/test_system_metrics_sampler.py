"""SystemMetricsSampler: writes host-telemetry samples without touching the Job system."""

from __future__ import annotations

from sqlalchemy import func, select

from marquee.core.jobs.manager import job_manager
from marquee.core.system_metrics_sampler import SystemMetricsSampler
from marquee.models import Job, JobEvent, SystemMetricsSample


async def _job_and_event_counts(db) -> tuple[int, int]:
    jobs = (await db.execute(select(func.count()).select_from(Job))).scalar()
    events = (await db.execute(select(func.count()).select_from(JobEvent))).scalar()
    return jobs, events


async def test_tick_writes_exactly_one_sample_row(db):
    sampler = SystemMetricsSampler()

    await sampler._tick()

    rows = (await db.execute(select(SystemMetricsSample))).scalars().all()
    assert len(rows) == 1
    sample = rows[0]
    assert isinstance(sample.cpu, dict) and "avg" in sample.cpu
    assert isinstance(sample.ram, dict)
    assert isinstance(sample.disk, dict)
    assert isinstance(sample.net, dict)
    assert sample.active_jobs == []


async def test_tick_creates_no_job_or_event_rows(db):
    """The core invariant: a 10-15s sampler heartbeat must never flood the
    job history Projection Room lets the user inspect."""
    sampler = SystemMetricsSampler()
    before_jobs, before_events = await _job_and_event_counts(db)

    await sampler._tick()

    after_jobs, after_events = await _job_and_event_counts(db)
    assert after_jobs == before_jobs
    assert after_events == before_events


async def test_tick_snapshots_active_job_ids_and_types(db):
    job = await job_manager.create(db, job_type="system_noop")
    job.status = "running"
    await db.commit()

    sampler = SystemMetricsSampler()
    await sampler._tick()

    sample = (await db.execute(select(SystemMetricsSample))).scalar_one()
    assert sample.active_jobs == [{"id": job.id, "type": "system_noop"}]


async def test_tick_ignores_queued_and_terminal_jobs(db):
    queued = await job_manager.create(db, job_type="system_noop")
    succeeded = await job_manager.create(db, job_type="system_noop")
    succeeded.status = "succeeded"
    await db.commit()
    assert queued.status == "queued"

    sampler = SystemMetricsSampler()
    await sampler._tick()

    sample = (await db.execute(select(SystemMetricsSample))).scalar_one()
    assert sample.active_jobs == []
