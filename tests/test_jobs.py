"""Durable generic job manager behavior."""

from __future__ import annotations

from sqlalchemy import select

from marquee.core.jobs.manager import job_manager
from marquee.models import Job, JobEvent, JobResourceReservation


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
        await db.execute(select(JobResourceReservation).where(JobResourceReservation.job_id == job.id))
    ).scalars().all()
    assert {item.resource_key for item in reservations} == {"gpu", "media-file:12"}


async def test_cancelling_queued_job_is_terminal(db):
    job = await job_manager.create(db, job_type="system_noop")

    cancelled = await job_manager.request_cancel(db, job)

    assert cancelled.status == "cancelled"
    assert cancelled.finished_at is not None


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


async def _drive_to_finish(db, worker_id: str, *, status: str) -> None:
    """Claim the next queued job, run it, and finish it with a result status."""
    claim = await job_manager.claim_next(db, worker_id)
    assert claim is not None
    await job_manager.start(db, *claim)
    await job_manager.finish(db, *claim, result={"status": status})


async def test_parent_emits_incremental_progress_then_summary(db):
    """A batch parent streams live per-child progress and a final summary —
    this is what drives the letterbox analyze bar and auto-moving cards."""
    parent = await job_manager.create(db, job_type="letterbox_detect_batch", status="waiting_external")
    await job_manager.create(db, job_type="letterbox_detect", parent_id=parent.id, subject_type="movie", subject_id=1)
    await job_manager.create(db, job_type="letterbox_detect", parent_id=parent.id, subject_type="movie", subject_id=2)

    async def parent_events() -> list[JobEvent]:
        return (
            await db.execute(select(JobEvent).where(JobEvent.job_id == parent.id).order_by(JobEvent.id))
        ).scalars().all()

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
