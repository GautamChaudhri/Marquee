"""Schedules: work the worker queues by itself, at an interval.

The schedules are declared here, in code, with their intervals in settings; the
``job_schedules`` table holds only when each is next due. A schedule seen for the first time
is due at once, unless it says to wait one whole interval first (retention does, so that
starting an updated installation never deletes anything before its owner has looked). When
one is due, its job is queued under a dedupe key (so a schedule never
has two jobs queued or running) and its due time moves on by the interval, in one
transaction. An interval of 0 turns a schedule off, and a shortened interval takes effect
within one new interval.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, Literal

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from marquee.config import settings
from marquee.db.models import JobSchedule
from marquee.jobs import queue
from marquee.jobs.registry import Registry

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Schedule:
    name: str
    kind: str
    interval: Callable[[], timedelta]
    """Read on every check, so it follows the settings."""
    payload: dict[str, Any] = field(default_factory=dict)
    first_due: Literal["now", "after_one_interval"] = "now"
    """When a schedule seen for the first time is due: at once, or one interval later."""


def _sync_interval() -> timedelta:
    return timedelta(minutes=settings.SYNC_INTERVAL_MINUTES)


SCHEDULES: tuple[Schedule, ...] = (
    Schedule("library_sync", kind="library_sync", interval=_sync_interval),
)


async def run_due(
    session: AsyncSession, schedules: Sequence[Schedule], registry: Registry
) -> list[int]:
    """Queue the job of every schedule that is due; returns the ids of the jobs it queued."""
    queued: list[int] = []
    for schedule in schedules:
        interval = schedule.interval()
        kind = registry.get(schedule.kind)
        if interval <= timedelta(0) or kind is None:
            continue
        first = func.now() if schedule.first_due == "now" else func.now() + interval
        await session.execute(
            insert(JobSchedule)
            .values(name=schedule.name, next_due_at=first)
            .on_conflict_do_nothing()
        )
        await session.execute(
            update(JobSchedule)
            .where(
                JobSchedule.name == schedule.name,
                JobSchedule.next_due_at > func.now() + interval,
            )
            .values(next_due_at=func.now() + interval)
        )
        due = (
            await session.execute(
                select(JobSchedule.next_due_at, func.now())
                .where(JobSchedule.name == schedule.name, JobSchedule.next_due_at <= func.now())
                .with_for_update()
            )
        ).first()
        if due is None:
            await session.commit()
            continue
        due_at, now = due
        job, created = await queue.add_job(
            session,
            kind,
            schedule.payload,
            dedupe_key=kind.dedupe_key_for(schedule.payload) or f"schedule:{schedule.name}",
        )
        # Keep the cadence, but never queue a burst to catch up after a long stop.
        next_due = due_at + interval if due_at + interval > now else now + interval
        await session.execute(
            update(JobSchedule)
            .where(JobSchedule.name == schedule.name)
            .values(next_due_at=next_due, last_job_id=job.id)
        )
        await session.commit()
        logger.info(
            "SCHEDULE | %s: %s job %d; next due %s",
            schedule.name,
            "queued" if created else "already active:",
            job.id,
            next_due.isoformat(),
        )
        if created:
            queued.append(job.id)
    return queued
