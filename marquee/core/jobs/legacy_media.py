"""Bridge established media handlers into the central job worker.

The media-operation code already contains careful remux/re-encode safety
checks.  This adapter keeps its durable media row as an operation record while
the generic Job is the authoritative scheduling, lease, and UI record.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

from marquee.config import settings
from marquee.core.jobs.cancel_registry import JobCancelledError
from marquee.core.jobs.handlers import register
from marquee.core.jobs.manager import job_manager
from marquee.database import _get_session_factory
from marquee.models import Job, MediaFile, MediaJob, Movie

logger = logging.getLogger(__name__)


async def _run_media(job: Job) -> dict:
    from marquee.core.media_jobs.handlers import dispatch  # noqa: PLC0415
    from marquee.core.media_jobs.manager import media_job_manager  # noqa: PLC0415

    media_job_id = job.payload.get("media_job_id")
    if not media_job_id:
        raise RuntimeError("media bridge job has no media_job_id")
    factory = _get_session_factory()

    # Short session: flip to "running" and release the connection immediately —
    # the heavy work below (dispatch -> mutation/letterbox_reencode) takes its
    # own session and can run for minutes; this bridge shouldn't add a second
    # long-held connection on top of that.
    async with factory() as db:
        media_job = await db.get(MediaJob, media_job_id)
        generic = await db.get(Job, job.id)
        if media_job is None or generic is None:
            raise RuntimeError("media bridge state is missing")
        media_job.status = "running"
        batch_id = media_job.batch_id
        # Resolve subject context once so every mirrored progress event can say
        # WHAT is being worked on ("Dune (2021)" / file name), not just a stage.
        enrich: dict = {}
        if media_job.media_file_id is not None:
            media_file = await db.get(MediaFile, media_job.media_file_id)
            if media_file is not None:
                if media_file.path:
                    enrich["file"] = Path(media_file.path).name
                if media_file.movie_id is not None:
                    movie = await db.get(Movie, media_file.movie_id)
                    if movie is not None and movie.title:
                        enrich["title"] = movie.title
        await db.commit()

    def _detail(progress: dict | None, message: str | None = None) -> dict | None:
        """Generic-stream detail: media progress + subject context + message.

        The message rides inside the detail dict (not just the event column)
        so the polled ``Job.progress`` snapshot can narrate the operation.
        """
        detail = dict(enrich)
        if progress:
            detail.update(progress)
        if message:
            detail["message"] = message
        return detail or None

    async def emit(_db, _job_id, stage, state, *, message=None, progress=None, persist=True):
        # Preserve the legacy event/audit stream while mirroring it into the
        # generic stream consumed by the job-management UI. Persisted ticks use
        # one short-lived session and ONE commit covering both event rows —
        # during concurrent batch remuxes the old session-per-emit/two-commit
        # pattern was enough DB churn to starve the connection pool.
        if not persist:
            # Publish-only: no session at all. The polling UI reads persisted
            # Job.progress, so an emitter that only ever sends persist=False
            # would freeze the polled bar — letterbox self-throttles to one
            # persisted tick per second for exactly this reason.
            await media_job_manager.emit(
                None, media_job_id, stage, state, message=message, progress=progress, persist=False
            )
            await job_manager.emit(
                None,
                job,
                state=state,
                stage=stage,
                message=message,
                detail=_detail(progress, message),
                persist=False,
            )
            return
        async with factory() as emit_db:
            # Touch the generic Job row before the MediaJob so this transaction
            # takes row locks in the same order as request_cancel() (Job, then
            # MediaJob via _bridge_media_cancel); the reverse order can deadlock
            # against a cancel arriving mid-remux.
            current = await emit_db.get(Job, job.id)
            if current is not None:
                current.current_stage = stage
                detail = _detail(progress, message)
                if detail is not None:
                    # Merge so a message-only stage tick doesn't wipe the
                    # percent a previous progress tick established.
                    base = current.progress if isinstance(current.progress, dict) else {}
                    current.progress = {**base, **detail, "stage": stage}
                await emit_db.flush()
            payload = await media_job_manager.emit(
                emit_db,
                media_job_id,
                stage,
                state,
                message=message,
                progress=progress,
                persist=True,
                commit=False,
            )
            if current is not None:
                await job_manager.emit(
                    emit_db,
                    current,
                    state=state,
                    stage=stage,
                    message=message,
                    detail=_detail(progress, message),
                    persist=True,
                )
            await emit_db.commit()
        media_job_manager.stream(media_job_id).publish(payload)

    try:
        async with factory() as dispatch_db:
            media_job = await dispatch_db.get(MediaJob, media_job_id)
            result = await dispatch(dispatch_db, media_job, emit)
            media_job.status = "succeeded"
            media_job.result_json = json.dumps(result, default=str)
            await dispatch_db.commit()
        return result
    except asyncio.CancelledError as exc:
        async with factory() as fail_db:
            media_job = await fail_db.get(MediaJob, media_job_id)
            if media_job is not None:
                await fail_db.refresh(media_job, ["cancel_requested"])
                media_job.status = "cancelled" if media_job.cancel_requested else "interrupted"
                media_job.error_json = json.dumps(
                    {
                        "error": str(exc) or "media job interrupted",
                        "type": type(exc).__name__,
                        "operation": media_job.operation,
                        "stage": media_job.stage,
                        "media_file_id": media_job.media_file_id,
                    },
                    default=str,
                )
                await fail_db.commit()
        raise
    except Exception as exc:
        async with factory() as fail_db:
            media_job = await fail_db.get(MediaJob, media_job_id)
            if media_job is not None:
                media_file = (
                    await fail_db.get(MediaFile, media_job.media_file_id)
                    if media_job.media_file_id is not None
                    else None
                )
                request = None
                if media_job.request_json:
                    try:
                        request = json.loads(media_job.request_json)
                    except json.JSONDecodeError:
                        request = media_job.request_json
                if media_job.cancel_requested:
                    media_job.status = "cancelled"
                elif isinstance(exc, JobCancelledError):
                    media_job.status = "interrupted"
                else:
                    media_job.status = "failed"
                media_job.error_json = json.dumps(
                    {
                        "error": str(exc),
                        "type": type(exc).__name__,
                        "code": getattr(exc, "code", None),
                        "operation": media_job.operation,
                        "stage": media_job.stage,
                        "media_file_id": media_job.media_file_id,
                        "file_path": media_file.path if media_file is not None else None,
                        "request": request,
                    },
                    default=str,
                )
                await fail_db.commit()
        raise
    finally:
        if batch_id:
            async with factory() as batch_db:
                await media_job_manager.update_batch_progress(batch_db, batch_id)
        media_job_manager.stream(media_job_id).finish()


for _operation in (
    "subtitle_scan",
    "audio_remove",
    "track_remove",
    "subtitle_remove",
    "subtitle_embed",
    "subtitle_metadata",
    "subtitle_extract",
    "subtitle_generate",
    "subtitle_policy",
    "subtitle_restore",
    "letterbox_reencode",
):
    register(_operation, max_runtime_seconds=settings.JOB_MEDIA_MAX_RUNTIME_SECONDS)(_run_media)
