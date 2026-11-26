from __future__ import annotations

import asyncio
import importlib
import threading
from typing import Any

import pytest
from sqlalchemy import func, update
from sqlalchemy.ext.asyncio import AsyncEngine

from marquee.config import settings
from marquee.db.engine import create_session_factory
from marquee.db.models import Job
from marquee.jobs import queue
from marquee.jobs.handlers import KINDS
from marquee.jobs.handlers.library_sync import library_sync
from marquee.jobs.registry import JobContext, JobError, PermanentJobError
from marquee.jobs.worker import Worker
from marquee.library import sync

# The package re-exports the handler function under the module's name, so reach the module.
handler = importlib.import_module("marquee.jobs.handlers.library_sync")


def _context() -> JobContext:
    return JobContext(job_id=1, payload={}, cancel=threading.Event(), progress=lambda *_: None)


def _sync_does(monkeypatch: pytest.MonkeyPatch, outcome: Any) -> dict[str, Any]:
    seen: dict[str, Any] = {}

    async def fake(*, dry_run: bool = False, cancel: Any = None) -> sync.SyncReport:
        seen.update(dry_run=dry_run, cancel=cancel)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    monkeypatch.setattr(sync, "sync_from_settings", fake)
    return seen


async def test_the_result_is_the_report(monkeypatch: pytest.MonkeyPatch) -> None:
    report = sync.SyncReport(dry_run=False, movies_listed=489, with_file=155, posters_found=147)
    seen = _sync_does(monkeypatch, report)
    context = _context()

    result = await library_sync(context)

    assert result == {"skipped": False, "ok": True, **report.to_dict(), "new_movie_runs": None}
    assert seen == {"dry_run": False, "cancel": context.cancel}


async def test_another_sync_running_is_a_skip(monkeypatch: pytest.MonkeyPatch) -> None:
    _sync_does(monkeypatch, sync.SyncAlreadyRunningError("another sync is running"))

    assert await library_sync(_context()) == {
        "skipped": True,
        "reason": "another sync is running",
    }


async def test_a_path_mapping_failure_is_permanent(monkeypatch: pytest.MonkeyPatch) -> None:
    _sync_does(monkeypatch, sync.SyncPathError("Radarr's root folder does not map"))

    with pytest.raises(PermanentJobError, match="does not map"):
        await library_sync(_context())


async def test_a_share_that_did_not_answer_is_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    _sync_does(
        monkeypatch,
        sync.SyncUnavailableError("Radarr's root folders did not answer within 30 s"),
    )

    with pytest.raises(JobError, match="did not answer") as raised:
        await library_sync(_context())
    assert not isinstance(raised.value, PermanentJobError)


@pytest.mark.db
async def test_a_sync_that_timed_out_fails_the_first_attempt_and_passes_the_second(
    monkeypatch: pytest.MonkeyPatch, database_url: str, db_engine: AsyncEngine
) -> None:
    """Through the queue and a real worker: the share does not answer, then it does."""
    calls: list[int] = []

    async def share_wakes_up(*, dry_run: bool = False, cancel: Any = None) -> sync.SyncReport:
        calls.append(1)
        if len(calls) == 1:
            raise sync.SyncUnavailableError(
                "Radarr's root folders did not answer within 30 s (is the share mounted?)"
            )
        return sync.SyncReport(dry_run=False, movies_listed=489)

    monkeypatch.setattr(sync, "sync_from_settings", share_wakes_up)
    monkeypatch.setattr(settings, "RUN_NEW_MOVIES", False)
    registry = {kind.name: kind for kind in KINDS}
    sessions = create_session_factory(db_engine)
    async with sessions() as session:
        job, _ = await queue.enqueue(session, registry["library_sync"], {})

    async def drain() -> None:
        worker = Worker(
            registry=registry,
            database_url=database_url,
            schedules=(),
            poll_s=0.2,
            stop_grace_s=2.0,
            gpu_idle_s=60.0,
            drain=True,
        )
        await asyncio.wait_for(worker.run(), 30)

    await drain()
    async with sessions() as session:
        waiting = await session.get(Job, job.id)
        assert waiting is not None
        assert (waiting.state, waiting.attempt) == ("queued", 1)
        assert waiting.error is not None and "did not answer within 30 s" in waiting.error
        assert waiting.run_after > waiting.created_at
        # The backoff has passed.
        await session.execute(update(Job).where(Job.id == job.id).values(run_after=func.now()))
        await session.commit()

    await drain()
    async with sessions() as session:
        done = await session.get(Job, job.id)
        assert done is not None
        assert (done.state, done.attempt) == ("succeeded", 2)
        assert done.result is not None and done.result["movies_listed"] == 489
    assert len(calls) == 2


async def test_other_errors_are_left_to_the_retry_rule(monkeypatch: pytest.MonkeyPatch) -> None:
    _sync_does(monkeypatch, sync.SyncError("Radarr's movie list is not a list"))

    with pytest.raises(sync.SyncError) as raised:
        await library_sync(_context())
    assert not isinstance(raised.value, PermanentJobError)


async def test_new_movies_are_queued_only_when_asked(monkeypatch: pytest.MonkeyPatch) -> None:
    report = sync.SyncReport(dry_run=False, movies_listed=1)
    _sync_does(monkeypatch, report)
    queued: list[str] = []

    async def fake_queue() -> dict[str, Any] | None:
        queued.append("called")
        return None if not settings.RUN_NEW_MOVIES else {"batch_id": 9, "movies": 2, "queued": 2}

    monkeypatch.setattr(handler, "queue_new_movie_runs", fake_queue)
    assert (await library_sync(_context()))["new_movie_runs"] is None

    monkeypatch.setattr(settings, "RUN_NEW_MOVIES", True)
    assert (await library_sync(_context()))["new_movie_runs"] == {
        "batch_id": 9,
        "movies": 2,
        "queued": 2,
    }
    assert queued == ["called", "called"]


async def test_a_skipped_sync_queues_no_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    _sync_does(monkeypatch, sync.SyncAlreadyRunningError("another sync is running"))
    monkeypatch.setattr(settings, "RUN_NEW_MOVIES", True)

    async def must_not_run() -> None:
        raise AssertionError("a skipped sync must not queue runs")

    monkeypatch.setattr(handler, "queue_new_movie_runs", must_not_run)
    assert (await library_sync(_context()))["skipped"] is True


async def test_with_the_setting_off_nothing_reaches_the_database(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "RUN_NEW_MOVIES", False)
    assert await handler.queue_new_movie_runs() is None
