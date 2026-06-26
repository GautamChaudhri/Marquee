"""job_retention_purge: deletes old terminal Job rows and their bridged MediaJob."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from marquee.config import settings
from marquee.core.jobs.builtin_handlers import job_retention_purge
from marquee.core.jobs.manager import job_manager
from marquee.core.jobs.scheduler import reconcile_schedules
from marquee.models import (
    Job,
    JobEvent,
    JobResource,
    JobResourceReservation,
    JobSchedule,
    JobWorker,
    MediaJob,
)


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


async def test_purge_deletes_idle_media_file_resource_rows(db):
    job = await job_manager.create(db, job_type="system_noop")
    db.add(JobResource(key="media-file:77", capacity=1))
    await db.commit()

    result = await job_retention_purge(job)

    assert result["resources_deleted"] == 1
    assert (await db.get(JobResource, "media-file:77")) is None


async def test_purge_keeps_media_file_resources_with_active_reservations(db):
    job = await job_manager.create(db, job_type="system_noop")
    db.add(JobResource(key="media-file:88", capacity=1))
    db.add(JobResourceReservation(job_id=job.id, resource_key="media-file:88", units=1))
    await db.commit()

    result = await job_retention_purge(job)

    assert result["resources_deleted"] == 0
    assert (await db.get(JobResource, "media-file:88")) is not None


async def test_purge_deletes_old_stopped_or_dead_workers(db):
    job = await job_manager.create(db, job_type="system_noop")
    old = datetime.now(UTC) - timedelta(days=8)
    recent = datetime.now(UTC) - timedelta(days=1)
    db.add_all(
        [
            JobWorker(id="worker-old-dead", status="dead", heartbeat_at=old),
            JobWorker(id="worker-old-stopped", status="stopped", heartbeat_at=old),
            JobWorker(id="worker-recent-dead", status="dead", heartbeat_at=recent),
        ]
    )
    await db.commit()

    result = await job_retention_purge(job)

    assert result["workers_deleted"] == 2
    assert (await db.get(JobWorker, "worker-old-dead")) is None
    assert (await db.get(JobWorker, "worker-old-stopped")) is None
    assert (await db.get(JobWorker, "worker-recent-dead")) is not None


async def test_purge_deletes_stale_live_workers_after_24_hours(db):
    job = await job_manager.create(db, job_type="system_noop")
    old = datetime.now(UTC) - timedelta(hours=25)
    recent = datetime.now(UTC) - timedelta(hours=1)
    db.add_all(
        [
            JobWorker(id="worker-old-running", status="running", heartbeat_at=old),
            JobWorker(id="worker-old-draining", status="draining", heartbeat_at=old),
            JobWorker(id="worker-recent-running", status="running", heartbeat_at=recent),
        ]
    )
    await db.commit()

    result = await job_retention_purge(job)

    assert result["workers_deleted"] == 2
    assert (await db.get(JobWorker, "worker-old-running")) is None
    assert (await db.get(JobWorker, "worker-old-draining")) is None
    assert (await db.get(JobWorker, "worker-recent-running")) is not None
