"""child_tracking: record/clear child PIDs on the current job attempt."""

from __future__ import annotations

from marquee.core.jobs.child_tracking import clear_child_pid, current_attempt_id, record_child_pid
from marquee.core.jobs.manager import job_manager


async def test_record_child_pid_outside_job_context_is_noop():
    # No attempt id set in the contextvar — must not raise or touch the DB.
    await record_child_pid(12345)
    await clear_child_pid(12345)


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
