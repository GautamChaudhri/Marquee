"""Durable generic job manager behavior."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from marquee.config import settings
from marquee.core.jobs.cancel_registry import JobCancelledError
from marquee.core.jobs.manager import job_manager
from marquee.models import Job, JobAttempt, JobEvent, JobResourceReservation


async def test_job_claim_reserves_resources_and_persists_attempt(db):
    await job_manager.bootstrap_resources(db)
    job = await job_manager.create(
        db,
        job_type="system_noop",
        payload={"value": 1},
        resources={"gpu": 1, "media-file:12": 1},
        subject_type="media_file",
        subject_id=12,
    )

    claim = await job_manager.claim_next(db, "worker-a")

    assert claim is not None
    claimed, attempt = claim
    assert claimed.id == job.id
    assert claimed.status == "claimed"
    assert attempt.number == 1
    reservations = (
        (
            await db.execute(
                select(JobResourceReservation).where(JobResourceReservation.job_id == job.id)
            )
        )
        .scalars()
        .all()
    )
    assert {item.resource_key for item in reservations} == {"gpu", "media-file:12"}


async def test_cancelling_queued_job_is_terminal(db):
    job = await job_manager.create(db, job_type="system_noop")

    cancelled = await job_manager.request_cancel(db, job)

    assert cancelled.status == "cancelled"
    assert cancelled.finished_at is not None


async def test_job_cancelled_error_marks_running_job_cancelled(db):
    await job_manager.bootstrap_resources(db)
    await job_manager.create(db, job_type="system_noop")
    claim = await job_manager.claim_next(db, "worker-a")
    assert claim is not None
    job, attempt = claim

    await job_manager.start(db, job, attempt)
    await job_manager.fail(db, job, attempt, JobCancelledError("cancelled cooperatively"))

    await db.refresh(job)
    await db.refresh(attempt)
    assert job.status == "cancelled"
    assert attempt.status == "cancelled"
    assert attempt.error["type"] == "Cancelled"


async def test_create_and_run_completes_inline_job_without_queue(db):
    job = await job_manager.create_and_run(
        db,
        job_type="system_noop",
        payload={"scope": "inline"},
        worker_id="inline-test",
    )

    attempt = (
        await db.execute(select(JobAttempt).where(JobAttempt.job_id == job.id))
    ).scalar_one()
    assert job.status == "succeeded"
    assert job.started_at is not None
    assert job.finished_at is not None
    assert job.result == {"echo": {"scope": "inline"}}
    assert attempt.worker_id == "inline-test"
    assert attempt.status == "succeeded"


async def test_create_and_run_never_retries_even_for_retryable_types(db, monkeypatch):
    async def broken(_job):
        raise RuntimeError("boom")

    monkeypatch.setattr(
        "marquee.core.jobs.manager.is_instant",
        lambda job_type: job_type == "letterbox_detect",
    )
    monkeypatch.setattr("marquee.core.jobs.manager.resolve", lambda _job_type: broken)

    with pytest.raises(RuntimeError, match="boom"):
        await job_manager.create_and_run(db, job_type="letterbox_detect", worker_id="inline-test")

    job = (
        await db.execute(select(Job).where(Job.type == "letterbox_detect").order_by(Job.created_at.desc()))
    ).scalars().first()
    assert job is not None
    assert job.status == "failed"
    assert job.attempt_count == 1
    assert job.scheduled_at is None


async def test_resource_conflict_waits_without_double_claim(db):
    await job_manager.bootstrap_resources(db)
    await job_manager.create(db, job_type="system_noop", resources={"gpu": 1})
    second = await job_manager.create(db, job_type="system_noop", resources={"gpu": 1})

    first_claim = await job_manager.claim_next(db, "worker-a")
    assert first_claim is not None
    assert (await job_manager.claim_next(db, "worker-b")) is None
    second_row = await db.get(Job, second.id)
    assert second_row is not None and second_row.status == "waiting_resource"

    await job_manager.start(db, *first_claim)
    await job_manager.finish(db, *first_claim, result={"ok": True})
    second_claim = await job_manager.claim_next(db, "worker-b")
    assert second_claim is not None and second_claim[0].id == second.id


async def test_claim_next_skips_blocked_media_write_and_claims_non_conflicting_job(db):
    await job_manager.bootstrap_resources(db)
    await job_manager.create(db, job_type="system_noop", resources={"media-file:1": 1})
    blocked = await job_manager.create(
        db, job_type="system_noop", resources={"media-file:1": 1}
    )
    claimable = await job_manager.create(
        db, job_type="system_noop", resources={"media-file:2": 1}
    )

    first_claim = await job_manager.claim_next(db, "worker-a")
    assert first_claim is not None
    await job_manager.start(db, *first_claim)

    second_claim = await job_manager.claim_next(db, "worker-b")
    assert second_claim is not None
    assert second_claim[0].id == claimable.id
    blocked_row = await db.get(Job, blocked.id)
    assert blocked_row is not None and blocked_row.status == "waiting_resource"

    await job_manager.finish(db, *first_claim, result={"ok": True})
    previously_blocked = await job_manager.claim_next(db, "worker-c")
    assert previously_blocked is not None
    assert previously_blocked[0].id == blocked.id


async def test_claim_next_limit_can_reach_claimable_job_beyond_default_window(db):
    await job_manager.bootstrap_resources(db)
    await job_manager.create(db, job_type="system_noop", resources={"media-file:blocked": 1})
    held = await job_manager.claim_next(db, "worker-a")
    assert held is not None
    await job_manager.start(db, *held)

    for _ in range(32):
        await job_manager.create(db, job_type="system_noop", resources={"media-file:blocked": 1})
    target = await job_manager.create(
        db, job_type="system_noop", resources={"media-file:claimable": 1}
    )

    assert await job_manager.claim_next(db, "worker-b", limit=32) is None
    widened = await job_manager.claim_next(db, "worker-b", limit=64)

    assert widened is not None
    assert widened[0].id == target.id


async def _drive_to_finish(db, worker_id: str, *, status: str) -> None:
    """Claim the next queued job, run it, and finish it with a result status."""
    claim = await job_manager.claim_next(db, worker_id)
    assert claim is not None
    await job_manager.start(db, *claim)
    await job_manager.finish(db, *claim, result={"status": status})


async def test_parent_emits_incremental_progress_then_summary(db):
    """A batch parent streams live per-child progress and a final summary —
    this is what drives the letterbox analyze bar and auto-moving cards."""
    parent = await job_manager.create(
        db, job_type="letterbox_detect_batch", status="waiting_external"
    )
    await job_manager.create(
        db, job_type="letterbox_detect", parent_id=parent.id, subject_type="movie", subject_id=1
    )
    await job_manager.create(
        db, job_type="letterbox_detect", parent_id=parent.id, subject_type="movie", subject_id=2
    )

    async def parent_events() -> list[JobEvent]:
        return (
            (
                await db.execute(
                    select(JobEvent).where(JobEvent.job_id == parent.id).order_by(JobEvent.id)
                )
            )
            .scalars()
            .all()
        )

    # First child finishes → an incremental progress event (parent still running).
    await _drive_to_finish(db, "worker-a", status="candidate")
    progress = [e for e in await parent_events() if e.state == "progress"]
    assert progress, "expected an incremental progress event after the first child"
    assert progress[-1].detail["children_completed"] == 1
    assert progress[-1].detail["children_total"] == 2
    assert progress[-1].detail["subject_id"] == "1"
    assert (await db.get(Job, parent.id)).status == "waiting_external"

    # Second (last) child finishes → parent terminal with an aggregated summary.
    await _drive_to_finish(db, "worker-a", status="not_letterboxed")
    parent_row = await db.get(Job, parent.id)
    assert parent_row.status == "succeeded"
    assert parent_row.result["summary"] == {"candidate": 1, "not_letterboxed": 1}
    terminal = [e for e in await parent_events() if e.detail and "summary" in e.detail]
    assert terminal and terminal[-1].detail["summary"] == {"candidate": 1, "not_letterboxed": 1}


async def test_create_batch_makes_every_child_visible_before_workers_can_claim(db):
    parent, children = await job_manager.create_batch(
        db,
        parent_type="letterbox_detect_batch",
        parent_payload={"movie_ids": [1, 2]},
        parent_priority=60,
        parent_subject_type="letterbox_batch",
        parent_subject_id="batch-1",
        children=[
            {
                "job_type": "letterbox_detect",
                "payload": {"movie_id": 1},
                "subject_type": "movie",
                "subject_id": 1,
            },
            {
                "job_type": "letterbox_detect",
                "payload": {"movie_id": 2},
                "subject_type": "movie",
                "subject_id": 2,
            },
        ],
    )

    assert parent.status == "waiting_external"
    assert len(children) == 2
    rows = (await db.execute(select(Job).where(Job.parent_id == parent.id))).scalars().all()
    assert {row.subject_id for row in rows} == {"1", "2"}


async def test_interrupt_rolls_parent_progress_and_terminalizes_batch(db):
    parent = await job_manager.create(
        db, job_type="letterbox_detect_batch", status="waiting_external"
    )
    await job_manager.create(
        db, job_type="letterbox_detect", parent_id=parent.id, subject_type="movie", subject_id=1
    )

    claim = await job_manager.claim_next(db, "worker-a")
    assert claim is not None
    job, attempt = claim
    await job_manager.start(db, job, attempt)
    await job_manager.interrupt(db, job, attempt, reason="worker shutdown")

    parent_row = await db.get(Job, parent.id)
    assert parent_row is not None
    assert parent_row.status == "interrupted"
    assert parent_row.finished_at is not None
    assert parent_row.progress == {
        "children_total": 1,
        "children_completed": 1,
        "children_failed": 1,
    }


async def test_recover_releases_stale_waiting_resource_attempt_and_requeues_retryable_job(db):
    await job_manager.bootstrap_resources(db)
    await job_manager.create(
        db,
        job_type="letterbox_detect",
        resources={"media_read": 1, "media-file:12": 1},
        subject_type="movie",
        subject_id=12,
    )

    claim = await job_manager.claim_next(db, "worker-a")
    assert claim is not None
    claimed, attempt = claim
    await job_manager.start(db, claimed, attempt)

    claimed.status = "waiting_resource"
    attempt.heartbeat_at = datetime.now(UTC) - timedelta(seconds=settings.JOB_LEASE_SECONDS + 5)
    await db.commit()

    recovered = await job_manager.recover(db)

    assert recovered == 1
    await db.refresh(claimed)
    await db.refresh(attempt)
    assert claimed.status == "retry_scheduled"
    assert claimed.finished_at is None
    assert attempt.status == "interrupted"
    reservations = (
        (
            await db.execute(
                select(JobResourceReservation).where(JobResourceReservation.job_id == claimed.id)
            )
        )
        .scalars()
        .all()
    )
    assert reservations
    assert all(item.released_at is not None for item in reservations)


async def test_cancelling_parent_batch_cascades_and_preserves_completed_children(db):
    parent = await job_manager.create(
        db, job_type="letterbox_detect_batch", status="waiting_external"
    )
    await job_manager.create(
        db, job_type="letterbox_detect", parent_id=parent.id, subject_type="movie", subject_id=1
    )
    queued = await job_manager.create(
        db, job_type="letterbox_detect", parent_id=parent.id, subject_type="movie", subject_id=2
    )
    await job_manager.create(
        db, job_type="letterbox_detect", parent_id=parent.id, subject_type="movie", subject_id=3
    )

    first_claim = await job_manager.claim_next(db, "worker-a")
    assert first_claim is not None
    await job_manager.start(db, *first_claim)
    await job_manager.finish(db, *first_claim, result={"status": "candidate"})

    second_claim = await job_manager.claim_next(db, "worker-a")
    assert second_claim is not None
    running_job, running_attempt = second_claim
    await job_manager.start(db, running_job, running_attempt)

    cancelled = await job_manager.request_cancel(db, parent)

    queued_row = await db.get(Job, queued.id)
    running_row = await db.get(Job, running_job.id)
    assert cancelled.status == "cancelling"
    assert queued_row is not None and queued_row.status == "cancelled"
    assert running_row is not None and running_row.cancel_requested is True
    assert running_row.status == "running"

    await job_manager.interrupt(db, running_job, running_attempt, reason="worker shutdown")
    parent_row = await db.get(Job, parent.id)
    assert parent_row is not None
    assert parent_row.status == "cancelled"
    assert parent_row.result == {
        "children_total": 3,
        "children_completed": 3,
        "children_failed": 2,
        "summary": {"candidate": 1},
    }


async def test_cancelled_before_execution_updates_parent_to_terminal(db):
    parent = await job_manager.create(
        db, job_type="letterbox_detect_batch", status="waiting_external"
    )
    child = await job_manager.create(
        db,
        job_type="letterbox_detect",
        parent_id=parent.id,
        subject_type="movie",
        subject_id=1,
        status="retry_scheduled",
    )
    child.cancel_requested = True
    await db.commit()

    claim = await job_manager.claim_next(db, "worker-a")

    assert claim is None
    child_row = await db.get(Job, child.id)
    parent_row = await db.get(Job, parent.id)
    assert child_row is not None and child_row.status == "cancelled"
    assert parent_row is not None
    assert parent_row.status == "cancelled"
    assert parent_row.finished_at is not None
