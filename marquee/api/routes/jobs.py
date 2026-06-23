"""UI-ready durable job inspection and control API."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from marquee.core.jobs import job_manager
from marquee.database import _get_session_factory, get_db
from marquee.models import Job, JobAttempt, JobEvent, JobResource, JobResourceReservation, JobWorker

router = APIRouter(prefix="/api/jobs", tags=["jobs"])
logger = logging.getLogger(__name__)

# SSE stream timeout (1 hour) prevents infinite streams if clients never close.
# Clients can reconnect using Last-Event-ID to resume from where they left off.
_SSE_TIMEOUT_SECONDS = 3600


def job_summary(job: Job) -> dict:
    return {
        "job_id": job.id,
        "type": job.type,
        "status": job.status,
        "priority": job.priority,
        "parent_id": job.parent_id,
        "root_id": job.root_id,
        "correlation_id": job.correlation_id,
        "subject": {"type": job.subject_type, "id": job.subject_id} if job.subject_type else None,
        "stage": job.current_stage,
        "progress": job.progress,
        "resource_request": job.resource_request,
        "attempt_count": job.attempt_count,
        "max_attempts": job.max_attempts,
        "cancel_requested": job.cancel_requested,
        "pause_requested": job.pause_requested,
        "scheduled_at": job.scheduled_at.isoformat() if job.scheduled_at else None,
        "claimed_at": job.claimed_at.isoformat() if job.claimed_at else None,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "finished_at": job.finished_at.isoformat() if job.finished_at else None,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "status_url": f"/api/jobs/{job.id}",
        "events_url": f"/api/jobs/{job.id}/events",
    }


@router.get("")
async def list_jobs(
    db: Annotated[AsyncSession, Depends(get_db)],
    status: str | None = None,
    type: str | None = None,
    subject_type: str | None = None,
    subject_id: str | None = None,
    parent_id: str | None = None,
    correlation_id: str | None = None,
    before: int | None = None,
    limit: int = Query(50, ge=1, le=200),
):
    query = select(Job).order_by(Job.created_at.desc(), Job.id.desc()).limit(limit + 1)
    if status:
        query = query.where(Job.status == status)
    if type:
        query = query.where(Job.type == type)
    if subject_type:
        query = query.where(Job.subject_type == subject_type)
    if subject_id:
        query = query.where(Job.subject_id == subject_id)
    if parent_id:
        query = query.where(Job.parent_id == parent_id)
    if correlation_id:
        query = query.where(Job.correlation_id == correlation_id)
    if before:
        query = query.where(Job.created_at < datetime.fromtimestamp(before, UTC))
    rows = (await db.execute(query)).scalars().all()
    has_more = len(rows) > limit
    rows = rows[:limit]
    next_before = (
        int(rows[-1].created_at.timestamp()) if has_more and rows and rows[-1].created_at else None
    )
    return {"jobs": [job_summary(row) for row in rows], "next_before": next_before}


@router.get("/metrics")
async def job_metrics(db: Annotated[AsyncSession, Depends(get_db)]):
    status_counts = dict(
        (await db.execute(select(Job.status, func.count()).group_by(Job.status))).all()
    )
    resources = (await db.execute(select(JobResource))).scalars().all()
    active = (
        await db.execute(
            select(
                JobResourceReservation.resource_key,
                func.coalesce(func.sum(JobResourceReservation.units), 0),
            )
            .where(JobResourceReservation.released_at.is_(None))
            .group_by(JobResourceReservation.resource_key)
        )
    ).all()
    in_use = dict(active)
    workers = (
        (await db.execute(select(JobWorker).order_by(JobWorker.heartbeat_at.desc())))
        .scalars()
        .all()
    )
    return {
        "counts": status_counts,
        "resources": [
            {
                "key": row.key,
                "capacity": row.capacity,
                "in_use": int(in_use.get(row.key, 0)),
                "enabled": row.enabled,
            }
            for row in resources
        ],
        "workers": [
            {"id": row.id, "status": row.status, "heartbeat_at": row.heartbeat_at.isoformat()}
            for row in workers
        ],
    }


@router.get("/{job_id}")
async def get_job(job_id: str, db: Annotated[AsyncSession, Depends(get_db)]):
    job = await db.get(Job, job_id)
    if job is None:
        raise HTTPException(404, "Job not found")
    attempts = (
        (
            await db.execute(
                select(JobAttempt).where(JobAttempt.job_id == job_id).order_by(JobAttempt.number)
            )
        )
        .scalars()
        .all()
    )
    reservations = (
        (
            await db.execute(
                select(JobResourceReservation).where(JobResourceReservation.job_id == job_id)
            )
        )
        .scalars()
        .all()
    )
    data = job_summary(job)
    data.update(
        {
            "payload": job.payload,
            "checkpoint": job.checkpoint,
            "result": job.result,
            "error": job.error,
            "attempts": [
                {
                    "number": a.number,
                    "status": a.status,
                    "worker_id": a.worker_id,
                    "started_at": a.started_at.isoformat() if a.started_at else None,
                    "finished_at": a.finished_at.isoformat() if a.finished_at else None,
                    "metrics": a.metrics,
                    "error": a.error,
                }
                for a in attempts
            ],
            "resources": [
                {
                    "key": r.resource_key,
                    "units": r.units,
                    "stage": r.stage,
                    "acquired_at": r.acquired_at.isoformat() if r.acquired_at else None,
                    "released_at": r.released_at.isoformat() if r.released_at else None,
                }
                for r in reservations
            ],
        }
    )
    return data


@router.get("/{job_id}/events")
async def job_events(
    job_id: str,
    request: Request,
    last_event_id: Annotated[str | None, Header()] = None,
):
    """Stream job events over SSE with disconnect detection and timeout.

    Clients reconnecting after disconnect should send the last ``id`` they
    received as the ``Last-Event-ID`` header — the stream replays all events
    after that ID and continues live.

    The stream terminates when:
      - The job reaches a terminal state (succeeded/failed/cancelled/...)
      - The client disconnects (browser tab closed, network interruption)
      - The stream exceeds 1 hour (timeout — client should reconnect)
    """
    factory = _get_session_factory()
    async with factory() as db:
        if await db.get(Job, job_id) is None:
            raise HTTPException(404, "Job not found")
    after = int(last_event_id or 0)
    factory = _get_session_factory()

    async def events():
        nonlocal after
        start = time.monotonic()
        while True:
            # Check client disconnect (browser tab closed, network drop)
            if await request.is_disconnected():
                logger.info("SSE client disconnected for job %s (after event_id=%d)", job_id, after)
                return

            # Enforce maximum stream duration (prevents infinite streams)
            elapsed = time.monotonic() - start
            if elapsed > _SSE_TIMEOUT_SECONDS:
                logger.warning(
                    "SSE stream timeout for job %s after %.0fs (client should reconnect)",
                    job_id,
                    elapsed,
                )
                yield 'event: error\ndata: {"message": "stream timeout — reconnect with Last-Event-ID"}\n\n'
                return

            async with factory() as stream_db:
                rows = (
                    (
                        await stream_db.execute(
                            select(JobEvent)
                            .where(JobEvent.job_id == job_id, JobEvent.id > after)
                            .order_by(JobEvent.id)
                        )
                    )
                    .scalars()
                    .all()
                )
                job = await stream_db.get(Job, job_id)

            for event in rows:
                after = event.id
                yield f"id: {event.id}\ndata: {json.dumps({'id': event.id, 'job_id': job_id, 'state': event.state, 'stage': event.stage, 'message': event.message, 'detail': event.detail}, default=str)}\n\n"

            if job is None or job.status in {
                "succeeded",
                "failed",
                "cancelled",
                "interrupted",
                "dead_letter",
            }:
                # Include final status so frontend can show appropriate UI
                yield f'event: done\ndata: {{"status": "{job.status if job else "unknown"}"}}\n\n'
                logger.debug(
                    "SSE stream complete for job %s (status=%s)",
                    job_id,
                    job.status if job else None,
                )
                return

            await asyncio.sleep(0.5)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/{job_id}/cancel", status_code=202)
async def cancel_job(job_id: str, db: Annotated[AsyncSession, Depends(get_db)]):
    job = await db.get(Job, job_id)
    if job is None:
        raise HTTPException(404, "Job not found")
    return job_summary(await job_manager.request_cancel(db, job))


@router.post("/{job_id}/pause", status_code=202)
async def pause_job(job_id: str, db: Annotated[AsyncSession, Depends(get_db)]):
    job = await db.get(Job, job_id)
    if job is None:
        raise HTTPException(404, "Job not found")
    return job_summary(await job_manager.set_paused(db, job, True))


@router.post("/{job_id}/resume", status_code=202)
async def resume_job(job_id: str, db: Annotated[AsyncSession, Depends(get_db)]):
    job = await db.get(Job, job_id)
    if job is None:
        raise HTTPException(404, "Job not found")
    return job_summary(await job_manager.set_paused(db, job, False))


@router.post("/{job_id}/retry", status_code=202)
async def retry_job(job_id: str, db: Annotated[AsyncSession, Depends(get_db)]):
    job = await db.get(Job, job_id)
    if job is None:
        raise HTTPException(404, "Job not found")
    if job.status not in {"failed", "interrupted", "cancelled", "dead_letter"}:
        raise HTTPException(409, f"Cannot retry a {job.status} job")
    retry = await job_manager.create(
        db,
        job_type=job.type,
        payload=job.payload,
        priority=job.priority,
        resources=job.resource_request,
        parent_id=job.parent_id,
        correlation_id=job.correlation_id,
        subject_type=job.subject_type,
        subject_id=job.subject_id,
        max_attempts=job.max_attempts,
    )
    return job_summary(retry)
