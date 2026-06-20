"""Media-job lifecycle routes (design §25.2) — confirm, status, SSE, cancel.

A planned mutation is confirmed (revalidated against the live file signature,
then queued for the durable worker), observed via persisted+live SSE, and can be
cancelled. Backups/quarantined sidecars are restorable here.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from marquee.core.media_files import resolve_media_file
from marquee.core.media_jobs import media_job_manager
from marquee.core.media_jobs.manager import _SENTINEL
from marquee.database import get_db
from marquee.models import Job, MediaJob

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/media-jobs", tags=["media-jobs"])


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=UTC)


def _job_dict(job: MediaJob) -> dict:
    return {
        "job_id": job.job_id,
        "operation": job.operation,
        "status": job.status,
        "stage": job.stage,
        "trigger": job.trigger,
        "media_file_id": job.media_file_id,
        "batch_id": job.batch_id,
        "plan": json.loads(job.plan_json) if job.plan_json else None,
        "result": json.loads(job.result_json) if job.result_json else None,
        "error": json.loads(job.error_json) if job.error_json else None,
        "plan_expires_at": job.plan_expires_at.isoformat() if job.plan_expires_at else None,
        "created_at": job.created_at.isoformat() if job.created_at else None,
    }


async def _generic_for_media_job(db: AsyncSession, media_job: MediaJob) -> Job | None:
    """Temporary bridge lookup while media-operation rows are phased out."""
    rows = (await db.execute(select(Job).where(Job.type == media_job.operation))).scalars().all()
    return next((row for row in rows if row.payload.get("media_job_id") == media_job.job_id), None)


@router.post("/{job_id}/confirm")
async def confirm_job(job_id: str, db: Annotated[AsyncSession, Depends(get_db)]):
    """Revalidate a planned job against the live file, then queue it (§23.2)."""
    job = await db.get(MediaJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    if job.status != "planned":
        raise HTTPException(status_code=409, detail={"code": "not_planned", "status": job.status})
    plan_expires_at = _as_utc(job.plan_expires_at)
    if plan_expires_at and plan_expires_at < datetime.now(UTC):
        job.status = "failed"
        job.error_json = json.dumps({"code": "plan_stale", "error": "plan expired"})
        await db.commit()
        raise HTTPException(status_code=409, detail={"code": "plan_stale", "message": "plan expired"})

    # Re-validate the file signature so we never act on a changed file.
    try:
        resolved = await resolve_media_file(db, job.media_file_id)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=422, detail={"code": "file_unavailable", "message": str(exc)}) from exc
    if job.input_signature and resolved.signature != job.input_signature:
        raise HTTPException(
            status_code=409,
            detail={"code": "plan_stale", "message": "file changed since plan was created"},
        )

    job.status = "queued"
    job.confirmed_at = datetime.now(UTC)
    generic = await _generic_for_media_job(db, job)
    if generic is not None:
        generic.status = "queued"
        generic.scheduled_at = datetime.now(UTC)
    await db.commit()
    return {"job_id": job_id, "status": "queued"}


@router.get("/{job_id}")
async def get_job(job_id: str, db: Annotated[AsyncSession, Depends(get_db)]):
    job = await db.get(MediaJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    return _job_dict(job)


@router.get("/{job_id}/events")
async def job_events(job_id: str, db: Annotated[AsyncSession, Depends(get_db)]):
    """SSE: replay persisted events, then stream live ones until completion."""
    job = await db.get(MediaJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    stream = media_job_manager.stream(job_id)
    history = await media_job_manager.persisted_events(db, job_id)
    terminal = {"succeeded", "failed", "cancelled", "interrupted"}
    already_done = job.status in terminal

    async def generator():
        queue = stream.subscribe()
        seen = set()
        try:
            for event in history:
                seen.add(event["id"])
                yield f"data: {json.dumps(event)}\n\n"
            if already_done:
                yield "event: done\ndata: {}\n\n"
                return
            while True:
                event = await queue.get()
                if event is _SENTINEL:
                    yield "event: done\ndata: {}\n\n"
                    return
                if event.get("id") in seen:
                    continue
                yield f"data: {json.dumps(event)}\n\n"
        finally:
            stream.unsubscribe(queue)

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/{job_id}/cancel")
async def cancel_job(job_id: str, db: Annotated[AsyncSession, Depends(get_db)]):
    job = await db.get(MediaJob, job_id)
    ok = await media_job_manager.cancel(db, job_id)
    if not ok:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    if job is not None:
        generic = await _generic_for_media_job(db, job)
        if generic is not None:
            from marquee.core.jobs import job_manager  # noqa: PLC0415

            await job_manager.request_cancel(db, generic)
    return {"job_id": job_id, "cancel_requested": True}


@router.get("")
async def list_jobs(
    db: Annotated[AsyncSession, Depends(get_db)],
    status: str | None = None,
    operation: str | None = None,
    limit: int = 100,
):
    query = select(MediaJob).order_by(MediaJob.created_at.desc()).limit(min(limit, 500))
    if status:
        query = query.where(MediaJob.status == status)
    if operation:
        query = query.where(MediaJob.operation == operation)
    rows = (await db.execute(query)).scalars().all()
    return {"jobs": [_job_dict(j) for j in rows]}


@router.post("/{job_id}/restore")
async def restore_backup(job_id: str, db: Annotated[AsyncSession, Depends(get_db)]):
    """Restore a tracked backup / quarantined sidecar from a job (§16.7)."""
    from marquee.core.subtitles.backup import restore_job_backup  # noqa: PLC0415

    result = await restore_job_backup(db, job_id)
    if result is None:
        raise HTTPException(status_code=404, detail="No restorable backup for this job")
    return result


@router.delete("/{job_id}/backup")
async def delete_backup(job_id: str, db: Annotated[AsyncSession, Depends(get_db)]):
    """Permanently delete a job's tracked backup (explicit; never automatic)."""
    from marquee.core.subtitles.backup import delete_job_backup  # noqa: PLC0415

    result = await delete_job_backup(db, job_id)
    if result is None:
        raise HTTPException(status_code=404, detail="No backup for this job")
    return result
