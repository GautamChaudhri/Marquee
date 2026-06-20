"""Durable generic job manager behavior."""

from __future__ import annotations

from sqlalchemy import select

from marquee.core.jobs.manager import job_manager
from marquee.models import Job, JobResourceReservation


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
