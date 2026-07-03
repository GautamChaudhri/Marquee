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

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from marquee.core.media_files import resolve_media_file
from marquee.core.media_jobs import media_job_manager
from marquee.core.media_jobs.serialize import job_dict as _job_dict
from marquee.database import _get_session_factory, get_db
from marquee.models import Job, MediaJob, MediaJobEvent

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/media-jobs", tags=["media-jobs"])


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=UTC)


async def _generic_for_media_job(db: AsyncSession, media_job: MediaJob) -> Job | None:
    """Temporary bridge lookup while media-operation rows are phased out."""
    rows = (await db.execute(select(Job).where(Job.type == media_job.operation))).scalars().all()
    return next((row for row in rows if row.payload.get("media_job_id") == media_job.job_id), None)


def _effective_media_status(media_status: str, generic_status: str | None) -> str:
    if generic_status is None:
        return media_status
    if generic_status == "running":
        return "running" if media_status in {"planned", "queued"} else media_status
    if generic_status in {"claimed", "waiting_resource", "retry_scheduled", "paused", "queued"}:
        return "queued" if media_status == "planned" else media_status
    if generic_status == "succeeded":
        return generic_status if media_status in {"planned", "queued", "running"} else media_status
    if generic_status in {"failed", "dead_letter"}:
        return "failed" if media_status in {"planned", "queued", "running"} else media_status
    if generic_status in {"cancelled", "interrupted"}:
        return generic_status if media_status in {"planned", "queued", "running"} else media_status
    return media_status


async def _media_job_snapshot(db: AsyncSession, media_job: MediaJob) -> dict:
    generic = await _generic_for_media_job(db, media_job)
    effective_status = _effective_media_status(
        media_job.status, generic.status if generic else None
    )
    effective_error = json.loads(media_job.error_json) if media_job.error_json else None
    effective_result = json.loads(media_job.result_json) if media_job.result_json else None
    effective_progress = None
    started_at = media_job.confirmed_at
    completed_at = None
    updated_at = media_job.updated_at or media_job.created_at

    if generic is not None:
        started_at = generic.started_at or generic.claimed_at or started_at
        completed_at = (
            generic.finished_at
            if effective_status in {"succeeded", "failed", "cancelled", "interrupted"}
            else None
        )
        updated_at = generic.updated_at or updated_at
        if effective_error is None and effective_status in {"failed", "cancelled", "interrupted"}:
            effective_error = generic.error
        if effective_result is None and effective_status == "succeeded":
            effective_result = generic.result
        if generic.status == "running" and isinstance(generic.progress, dict):
            # The mirrored generic progress carries the operation narration
            # ("Removing English subtitle (SDH) — mkvmerge") — prefer it over
            # the MediaJob row's bare stage whenever the job is live.
            detail = generic.progress
            percent = detail.get("percent") or detail.get("done")
            if percent is None and media_job.progress_total:
                percent = int((media_job.progress_done / media_job.progress_total) * 100)
            effective_progress = {
                "stage": str(detail.get("stage") or generic.current_stage or "running"),
                "percent": int(percent or 0),
                "message": str(detail.get("message") or generic.current_stage or "Running"),
            }

    return _job_dict(
        media_job,
        status=effective_status,
        progress=effective_progress,
        result=effective_result,
        error=effective_error,
        updated_at=updated_at,
        started_at=started_at,
        completed_at=completed_at,
    )


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
        raise HTTPException(
            status_code=409, detail={"code": "plan_stale", "message": "plan expired"}
        )

    # Reject plans the backend itself has flagged as non-executable.
    if job.plan_json:
        plan = json.loads(job.plan_json)
        if not plan.get("capabilities", {}).get("can_execute", True):
            blocking = [
                w["code"]
                for w in plan.get("warnings", [])
                if w.get("code") in ("container_not_writable", "mkv_track_ids_unavailable")
            ]
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "plan_not_executable",
                    "message": "plan cannot be executed",
                    "blocking_warnings": blocking,
                },
            )

    # Re-validate the file signature so we never act on a changed file.
    try:
        resolved = await resolve_media_file(db, job.media_file_id)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=422, detail={"code": "file_unavailable", "message": str(exc)}
        ) from exc
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

    if job.operation == "letterbox_reencode" and job.media_file_id is not None:
        from sqlalchemy import select

        from marquee.models import MediaFile
        from marquee.models.letterbox import LetterboxState

        movie_file = await db.get(MediaFile, job.media_file_id)
        if movie_file and movie_file.movie_id is not None:
            stmt = select(LetterboxState).where(LetterboxState.movie_id == movie_file.movie_id)
            state = (await db.execute(stmt)).scalar_one_or_none()
            if state and state.status == "candidate":
                state.status = "tagged"
                state.reviewed = False

    await db.commit()
    return {"job_id": job_id, "status": "queued"}


@router.get("/{job_id}")
async def get_job(job_id: str, db: Annotated[AsyncSession, Depends(get_db)]):
    job = await db.get(MediaJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    return await _media_job_snapshot(db, job)


@router.get("/{job_id}/events")
async def job_events(
    job_id: str,
    request: Request,
):
    """SSE: replay persisted events, then stream live ones until completion."""
    factory = _get_session_factory()
    async with factory() as db:
        job = await db.get(MediaJob, job_id)
        if job is None:
            raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    factory = _get_session_factory()
    terminal = {"succeeded", "failed", "cancelled", "interrupted"}

    _pct = {
        ("preflight", "start"): 5,
        ("preflight", "done"): 15,
        ("remux", "start"): 20,
        ("remux", "done"): 65,
        ("validate", "start"): 70,
        ("validate", "done"): 80,
        ("replace", "start"): 85,
        ("external", "start"): 90,
        ("external", "done"): 95,
        ("restore", "start"): 90,
        ("done", "complete"): 100,
    }

    async def generator():
        import asyncio
        import time

        after = 0
        start = time.monotonic()
        while True:
            if await request.is_disconnected():
                logger.info(
                    "SSE client disconnected for media job %s (after event_id=%d)", job_id, after
                )
                return

            if time.monotonic() - start > 3600:
                logger.warning("SSE stream timeout for media job %s", job_id)
                yield 'event: error\ndata: {"message": "stream timeout"}\n\n'
                return

            async with factory() as stream_db:
                rows = (
                    (
                        await stream_db.execute(
                            select(MediaJobEvent)
                            .where(MediaJobEvent.job_id == job_id, MediaJobEvent.id > after)
                            .order_by(MediaJobEvent.id)
                        )
                    )
                    .scalars()
                    .all()
                )
                current_job = await stream_db.get(MediaJob, job_id)

            for event in rows:
                after = event.id
                event_dict = {
                    "id": event.id,
                    "job_id": job_id,
                    "stage": event.stage,
                    "state": event.state,
                    "message": event.message,
                    "progress": json.loads(event.progress_json) if event.progress_json else None,
                }
                stage = event_dict.get("stage")
                state = event_dict.get("state")
                if isinstance(stage, str) and isinstance(state, str):
                    pct = _pct.get((stage, state))
                    if pct is not None:
                        event_dict["percent"] = pct
                yield f"data: {json.dumps(event_dict)}\n\n"

            if current_job is None or current_job.status in terminal:
                yield "event: done\ndata: {}\n\n"
                return

            await asyncio.sleep(0.5)

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
    if operation:
        query = query.where(MediaJob.operation == operation)
    rows = (await db.execute(query)).scalars().all()
    jobs = [await _media_job_snapshot(db, job) for job in rows]
    if status:
        jobs = [job for job in jobs if job["status"] == status]
    return {"jobs": jobs}


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
