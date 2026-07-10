"""Durable worker cancellation, timeout, and child-cleanup behavior."""

from __future__ import annotations

import asyncio

import marquee.core.jobs.worker as worker_module
from marquee.config import settings
from marquee.core.jobs import cancel_registry
from marquee.core.jobs.cancel_registry import JobCancelledError
from marquee.core.jobs.manager import job_manager
from marquee.models import Job, JobAttempt


async def test_worker_cancel_watcher_sets_event_kills_children_and_marks_cancelled(db, monkeypatch):
    await job_manager.bootstrap_resources(db)
    job = await job_manager.create(db, job_type="system_noop")
    claim = await job_manager.claim_next(db, "worker-a")
    assert claim is not None
    claimed, attempt = claim

    handler_started = asyncio.Event()
    terminate_seen = asyncio.Event()
    terminated: list[tuple[str, list[int]]] = []

    async def fake_handler(running_job: Job) -> dict:
        handler_started.set()
        while True:
            event = cancel_registry.get(running_job.id)
            if event is not None and event.is_set():
                await terminate_seen.wait()
                raise JobCancelledError("cooperative cancel")
            await asyncio.sleep(0.005)

    async def fake_terminate(_db, running_attempt: JobAttempt, *, grace_seconds: float = 2.0):
        terminated.append((running_attempt.id, list(running_attempt.child_pids or [])))
        running_attempt.child_pids = []
        await _db.flush()
        terminate_seen.set()
        return [333]

    monkeypatch.setattr(worker_module, "resolve", lambda _job_type: fake_handler)
    monkeypatch.setattr(worker_module, "terminate_child_pids", fake_terminate)
    monkeypatch.setattr(worker_module, "CANCEL_POLL_SECONDS", 0.01)

    worker = worker_module.DurableWorker()
    task = asyncio.create_task(worker._run_claim(claimed, attempt))
    await asyncio.wait_for(handler_started.wait(), timeout=1)

    attempt_row = await db.get(JobAttempt, attempt.id)
    assert attempt_row is not None
    attempt_row.child_pids = [333]
    await db.commit()
    await db.refresh(job)
    await job_manager.request_cancel(db, job)

    await asyncio.wait_for(task, timeout=2)

    job_id = job.id
    attempt_id = attempt.id
    db.expire_all()
    row = await db.get(Job, job_id)
    attempt_row = await db.get(JobAttempt, attempt_id)
    assert row is not None and row.status == "cancelled"
    assert attempt_row is not None and attempt_row.status == "cancelled"
    assert attempt_row.child_pids == []
    assert terminated == [(attempt.id, [333])]
    assert cancel_registry.get(job.id) is None


async def test_worker_timeout_sets_event_terminates_children_and_fails_job(db, monkeypatch):
    await job_manager.bootstrap_resources(db)
    job = await job_manager.create(db, job_type="system_noop")
    claim = await job_manager.claim_next(db, "worker-a")
    assert claim is not None
    claimed, attempt = claim
    attempt.child_pids = [444]
    await db.commit()

    handler_started = asyncio.Event()
    terminated: list[tuple[str, list[int], float]] = []

    async def slow_handler(_job: Job) -> dict:
        handler_started.set()
        await asyncio.sleep(10)
        return {"ok": True}

    async def fake_terminate(_db, running_attempt: JobAttempt, *, grace_seconds: float = 2.0):
        terminated.append(
            (running_attempt.id, list(running_attempt.child_pids or []), grace_seconds)
        )
        running_attempt.child_pids = []
        await _db.flush()
        return [444]

    monkeypatch.setattr(worker_module, "resolve", lambda _job_type: slow_handler)
    monkeypatch.setattr(worker_module, "max_runtime_seconds", lambda _job_type: 0.03)
    monkeypatch.setattr(worker_module, "terminate_child_pids", fake_terminate)
    monkeypatch.setattr(settings, "JOB_SHUTDOWN_GRACE_SECONDS", 0.01)

    worker = worker_module.DurableWorker()
    task = asyncio.create_task(worker._run_claim(claimed, attempt))
    await asyncio.wait_for(handler_started.wait(), timeout=1)
    await asyncio.wait_for(task, timeout=2)

    job_id = job.id
    attempt_id = attempt.id
    db.expire_all()
    row = await db.get(Job, job_id)
    attempt_row = await db.get(JobAttempt, attempt_id)
    assert row is not None and row.status == "failed"
    assert attempt_row is not None
    assert attempt_row.status == "failed"
    assert attempt_row.error["type"] == "TimeoutError"
    assert attempt_row.child_pids == []
    assert terminated == [(attempt.id, [444], 2.0)]


async def test_legacy_media_jobs_use_media_runtime_override():
    assert (
        worker_module.max_runtime_seconds("subtitle_remove")
        == settings.JOB_MEDIA_MAX_RUNTIME_SECONDS
    )
    assert worker_module.max_runtime_seconds("system_noop") is None
