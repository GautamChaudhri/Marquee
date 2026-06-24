"""job_retention_purge: deletes old terminal Job rows and their bridged MediaJob."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from marquee.config import settings
from marquee.core.jobs.builtin_handlers import job_retention_purge
from marquee.core.jobs.manager import job_manager
from marquee.core.jobs.scheduler import reconcile_schedules
from marquee.models import Job, JobEvent, JobSchedule, MediaJob


async def _make_terminal_job(db, *, age_days: int, status: str = "succeeded", payload=None):
    job = await job_manager.create(db, job_type="system_noop", payload=payload or {})
    job.status = status
    job.finished_at = datetime.now(UTC) - timedelta(days=age_days)
    await db.commit()
    return job


async def test_purge_deletes_old_terminal_job_and_cascades_children(db, monkeypatch):
    monkeypatch.setattr(settings, "JOB_RETENTION_DAYS", 30)
    job = await _make_terminal_job(db, age_days=40)
    job_id = job.id

    result = await job_retention_purge(job)
    db.expire_all()

    assert result["jobs_deleted"] == 1
    assert (await db.get(Job, job_id)) is None
    remaining_events = (
        (await db.execute(select(JobEvent).where(JobEvent.job_id == job_id))).scalars().all()
    )
    assert remaining_events == []


async def test_purge_keeps_jobs_within_retention_window(db, monkeypatch):
    monkeypatch.setattr(settings, "JOB_RETENTION_DAYS", 30)
    job = await _make_terminal_job(db, age_days=5)
    job_id = job.id

    result = await job_retention_purge(job)

    assert result["jobs_deleted"] == 0
    assert (await db.get(Job, job_id)) is not None


async def test_purge_keeps_non_terminal_jobs_regardless_of_age(db, monkeypatch):
    monkeypatch.setattr(settings, "JOB_RETENTION_DAYS", 30)
    job = await job_manager.create(db, job_type="system_noop")
    job.status = "running"
    job.finished_at = datetime.now(UTC) - timedelta(days=400)
    await db.commit()
    job_id = job.id

    result = await job_retention_purge(job)

    assert result["jobs_deleted"] == 0
    assert (await db.get(Job, job_id)) is not None


async def test_purge_deletes_bridged_media_job_alongside_its_generic_job(db, monkeypatch):
    monkeypatch.setattr(settings, "JOB_RETENTION_DAYS", 30)
    media_job = MediaJob(job_id="mj-purge-1", operation="subtitle_scan", status="succeeded")
    db.add(media_job)
    await db.commit()
    job = await _make_terminal_job(db, age_days=40, payload={"media_job_id": "mj-purge-1"})

    result = await job_retention_purge(job)
    db.expire_all()

    assert result["jobs_deleted"] == 1
    assert result["media_jobs_deleted"] == 1
    assert (await db.get(MediaJob, "mj-purge-1")) is None


async def test_purge_orphans_child_parent_id_but_does_not_delete_child(db, monkeypatch):
    monkeypatch.setattr(settings, "JOB_RETENTION_DAYS", 30)
    parent = await _make_terminal_job(db, age_days=40)
    child = await job_manager.create(db, job_type="system_noop", parent_id=parent.id)
    child_id = child.id

    await job_retention_purge(parent)
    db.expire_all()

    refreshed_child = await db.get(Job, child_id)
    assert refreshed_child is not None
    assert refreshed_child.parent_id is None


async def test_reconcile_schedules_enables_retention_purge_unconditionally(db):
    await reconcile_schedules()

    schedule = await db.get(JobSchedule, "job-retention-purge")
    assert schedule is not None
    assert schedule.enabled is True
    assert schedule.job_type == "job_retention_purge"
    assert schedule.interval_seconds == 86400
