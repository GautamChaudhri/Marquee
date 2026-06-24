"""Dedicated scheduler that creates durable recurring jobs, never work itself."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from marquee.config import settings
from marquee.core.jobs.manager import job_manager
from marquee.database import _get_session_factory, close_db, init_db
from marquee.models import JobSchedule

# Every recurring schedule Marquee knows about.  Whether each is actually
# enabled is decided per-reconcile from the feature flags below, so toggling a
# flag off durably disables the schedule instead of leaving it firing.
ALL_SCHEDULE_IDS = ("poster-heal", "letterbox-heal", "backup", "job-retention-purge")


def _enabled_schedules() -> dict[str, tuple[str, int]]:
    """Schedules whose feature flags are currently on → (job_type, interval_s)."""
    schedules: dict[str, tuple[str, int]] = {}
    if settings.HEAL_ENABLED:
        schedules["poster-heal"] = ("poster_heal", settings.HEAL_INTERVAL_MINUTES * 60)
    if settings.LETTERBOX_ENABLED and settings.LETTERBOX_HEAL_ENABLED:
        schedules["letterbox-heal"] = (
            "letterbox_heal",
            settings.LETTERBOX_HEAL_INTERVAL_MINUTES * 60,
        )
    # Scheduled backups are skipped in DEBUG (matches the prior lifespan gate).
    if settings.BACKUP_INTERVAL_HOURS > 0 and not settings.DEBUG:
        schedules["backup"] = ("backup_create", settings.BACKUP_INTERVAL_HOURS * 3600)
    # Pure hygiene — no feature flag, always on (gated only by JOB_RETENTION_DAYS).
    schedules["job-retention-purge"] = ("job_retention_purge", 86400)
    return schedules


async def reconcile_schedules() -> None:
    desired = _enabled_schedules()
    factory = _get_session_factory()
    async with factory() as db:
        for schedule_id, (job_type, interval) in desired.items():
            interval = max(60, interval)
            row = await db.get(JobSchedule, schedule_id)
            if row is None:
                db.add(
                    JobSchedule(
                        id=schedule_id,
                        job_type=job_type,
                        interval_seconds=interval,
                        next_run_at=datetime.now(UTC) + timedelta(seconds=interval),
                        enabled=True,
                    )
                )
            else:
                row.job_type = job_type
                row.interval_seconds = interval
                row.enabled = True
        # Durably disable any known schedule whose flag was turned off.
        for schedule_id in set(ALL_SCHEDULE_IDS) - set(desired):
            row = await db.get(JobSchedule, schedule_id)
            if row is not None and row.enabled:
                row.enabled = False
        await db.commit()


async def run() -> None:
    from marquee.logging import setup_logging

    setup_logging(level=settings.LOG_LEVEL, fmt=settings.LOG_FORMAT)

    await init_db()
    await reconcile_schedules()
    factory = _get_session_factory()
    try:
        while True:
            now = datetime.now(UTC)
            async with factory() as db:
                schedules = (
                    (
                        await db.execute(
                            select(JobSchedule)
                            .where(JobSchedule.enabled.is_(True), JobSchedule.next_run_at <= now)
                            .with_for_update(skip_locked=True)
                        )
                    )
                    .scalars()
                    .all()
                )
                for schedule in schedules:
                    job = await job_manager.create(
                        db,
                        job_type=schedule.job_type,
                        payload={"scheduled": True},
                        priority=schedule.priority,
                        idempotency_key=f"{schedule.id}:{schedule.next_run_at.isoformat()}",
                    )
                    schedule.last_job_id = job.id
                    schedule.last_run_at = now
                    schedule.next_run_at = now + timedelta(seconds=schedule.interval_seconds)
                await db.commit()
            await asyncio.sleep(settings.JOB_POLL_SECONDS)
    finally:
        await close_db()


if __name__ == "__main__":
    asyncio.run(run())
