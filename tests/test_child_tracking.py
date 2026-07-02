"""child_tracking: record/clear child PIDs on the current job attempt."""

from __future__ import annotations

import signal

from marquee.core.jobs import cancel_registry
from marquee.core.jobs.child_tracking import (
    clear_child_pid,
    current_attempt_id,
    record_child_pid,
    terminate_child_pids,
)
from marquee.core.jobs.manager import job_manager


async def test_record_child_pid_outside_job_context_is_noop():
    # No attempt id set in the contextvar — must not raise or touch the DB.
    await record_child_pid(12345)
    await clear_child_pid(12345)


def test_cancel_registry_lifecycle():
    event = cancel_registry.register("job-registry-test")
    try:
        assert cancel_registry.get("job-registry-test") is event
        assert cancel_registry.set_cancelled("job-registry-test") is True
        assert event.is_set()
    finally:
        cancel_registry.discard("job-registry-test")

    assert cancel_registry.get("job-registry-test") is None
    assert cancel_registry.set_cancelled("job-registry-test") is False


async def test_record_and_clear_child_pid_roundtrip(db):
    await job_manager.bootstrap_resources(db)
    await job_manager.create(db, job_type="system_noop")
    claim = await job_manager.claim_next(db, "worker-test")
    assert claim is not None
    _, attempt = claim

    token = current_attempt_id.set(attempt.id)
    try:
        await record_child_pid(999)
        await db.refresh(attempt)
        assert attempt.child_pids == [999]

        await record_child_pid(1000)
        await db.refresh(attempt)
        assert sorted(attempt.child_pids) == [999, 1000]

        await clear_child_pid(999)
        await db.refresh(attempt)
        assert attempt.child_pids == [1000]
    finally:
        current_attempt_id.reset(token)


async def test_terminate_child_pids_signals_and_clears_attempt(db, monkeypatch):
    await job_manager.bootstrap_resources(db)
    await job_manager.create(db, job_type="system_noop")
    claim = await job_manager.claim_next(db, "worker-test")
    assert claim is not None
    _, attempt = claim
    attempt.child_pids = [111, 222]
    await db.flush()

    calls: list[tuple[int, int]] = []

    def fake_kill(pid: int, sig: int) -> None:
        calls.append((pid, sig))

    monkeypatch.setattr("marquee.core.jobs.child_tracking.os.kill", fake_kill)

    pids = await terminate_child_pids(db, attempt, grace_seconds=0)

    assert pids == [111, 222]
    assert calls == [
        (111, signal.SIGTERM),
        (222, signal.SIGTERM),
        (111, 0),
        (111, signal.SIGKILL),
        (222, 0),
        (222, signal.SIGKILL),
    ]
    await db.refresh(attempt)
    assert attempt.child_pids == []
