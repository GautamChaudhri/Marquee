"""Schedules against a real PostgreSQL server (see tests/conftest.py)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from marquee.config import settings
from marquee.db.engine import create_session_factory
from marquee.db.models import Job, JobSchedule
from marquee.jobs import queue, schedules
from marquee.jobs.registry import JobContext, JobKind

pytestmark = pytest.mark.db


async def _noop(ctx: JobContext) -> None:
    return None


SYNC = JobKind("fake_sync", "network", handler=_noop, dedupe_key="fake_sync")
PLAIN = JobKind("fake_plain", "network", handler=_noop)
REGISTRY = {kind.name: kind for kind in (SYNC, PLAIN)}


def _every(minutes: float, kind: str = "fake_sync") -> schedules.Schedule:
    return schedules.Schedule("every", kind=kind, interval=lambda: timedelta(minutes=minutes))


@pytest.fixture
async def session(db_engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    async with create_session_factory(db_engine)() as session:
        yield session


async def _row(session: AsyncSession) -> JobSchedule | None:
    row = await session.scalar(
        select(JobSchedule).where(JobSchedule.name == "every"),
        execution_options={"populate_existing": True},
    )
    await session.commit()
    return row


async def _jobs(session: AsyncSession) -> list[Job]:
    rows = list(await session.scalars(select(Job).order_by(Job.id)))
    await session.commit()
    return rows


async def test_a_new_schedule_fires_at_once_and_then_waits(session: AsyncSession) -> None:
    before = datetime.now(UTC)
    first = await schedules.run_due(session, [_every(60)], REGISTRY)
    second = await schedules.run_due(session, [_every(60)], REGISTRY)

    (job,) = await _jobs(session)
    assert first == [job.id] and second == []
    assert (job.kind, job.dedupe_key) == ("fake_sync", "fake_sync")
    row = await _row(session)
    assert row is not None and row.last_job_id == job.id
    wait = (row.next_due_at - before).total_seconds()
    assert 3590 <= wait <= 3610


async def test_a_due_schedule_never_queues_a_second_active_job(session: AsyncSession) -> None:
    await schedules.run_due(session, [_every(60)], REGISTRY)
    await session.execute(update(JobSchedule).values(next_due_at=func.now()))
    await session.commit()

    again = await schedules.run_due(session, [_every(60)], REGISTRY)

    assert again == []
    assert len(await _jobs(session)) == 1
    row = await _row(session)
    assert row is not None and row.next_due_at > datetime.now(UTC)


async def test_it_fires_again_once_the_last_job_is_done(session: AsyncSession) -> None:
    (first,) = await schedules.run_due(session, [_every(60)], REGISTRY)
    await queue.claim(session, ["network"])
    await queue.succeed(session, first, None)
    await session.execute(update(JobSchedule).values(next_due_at=func.now()))
    await session.commit()

    (second,) = await schedules.run_due(session, [_every(60)], REGISTRY)

    assert second != first


async def test_a_kind_without_a_key_is_keyed_by_the_schedule(session: AsyncSession) -> None:
    await schedules.run_due(session, [_every(60, kind="fake_plain")], REGISTRY)

    (job,) = await _jobs(session)
    assert job.dedupe_key == "schedule:every"


async def test_an_interval_of_zero_turns_it_off(session: AsyncSession) -> None:
    assert await schedules.run_due(session, [_every(0)], REGISTRY) == []
    assert await _jobs(session) == []
    assert await _row(session) is None


async def test_an_unknown_kind_is_skipped(session: AsyncSession) -> None:
    assert await schedules.run_due(session, [_every(1, kind="nope")], REGISTRY) == []


async def test_a_shortened_interval_takes_effect(session: AsyncSession) -> None:
    await schedules.run_due(session, [_every(360)], REGISTRY)

    before = datetime.now(UTC)
    await schedules.run_due(session, [_every(1)], REGISTRY)

    row = await _row(session)
    assert row is not None
    assert (row.next_due_at - before).total_seconds() <= 61


async def test_a_long_stop_does_not_queue_a_burst(session: AsyncSession) -> None:
    await schedules.run_due(session, [_every(1)], REGISTRY)
    await session.execute(update(JobSchedule).values(next_due_at=func.now() - timedelta(hours=5)))
    await session.commit()

    before = datetime.now(UTC)
    await schedules.run_due(session, [_every(1)], REGISTRY)

    row = await _row(session)
    assert row is not None
    assert 50 <= (row.next_due_at - before).total_seconds() <= 61


def test_the_sync_schedule_follows_the_setting(monkeypatch: pytest.MonkeyPatch) -> None:
    (sync,) = schedules.SCHEDULES
    monkeypatch.setattr(settings, "SYNC_INTERVAL_MINUTES", 7)

    assert (sync.name, sync.kind, sync.interval()) == (
        "library_sync",
        "library_sync",
        timedelta(minutes=7),
    )
