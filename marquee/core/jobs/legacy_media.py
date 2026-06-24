"""Bridge established media handlers into the central job worker.

The media-operation code already contains careful remux/re-encode safety
checks.  This adapter keeps its durable media row as an operation record while
the generic Job is the authoritative scheduling, lease, and UI record.
"""

from __future__ import annotations

import json
import logging

from marquee.core.jobs.handlers import register
from marquee.core.jobs.manager import job_manager
from marquee.database import _get_session_factory
from marquee.models import Job, MediaJob

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
        await db.commit()

    async def emit(_db, _job_id, stage, state, *, message=None, progress=None, persist=True):
        # Preserve the legacy event/audit stream while mirroring it into the
        # generic stream consumed by the job-management UI. Opens its own
        # short-lived session per call rather than holding one open for the
        # whole job — this fires frequently during long encodes/remuxes.
        async with factory() as emit_db:
            await media_job_manager.emit(
                emit_db,
                media_job_id,
                stage,
                state,
                message=message,
                progress=progress,
                persist=persist,
            )
            current = await emit_db.get(Job, job.id)
            if current is not None:
                current.current_stage = stage
                if progress is not None:
                    current.progress = progress
                await job_manager.emit(
                    emit_db,
                    current,
                    state=state,
                    stage=stage,
                    message=message,
                    detail=progress,
                    persist=persist,
                )
            await emit_db.commit()

    try:
        async with factory() as dispatch_db:
            media_job = await dispatch_db.get(MediaJob, media_job_id)
            result = await dispatch(dispatch_db, media_job, emit)
            media_job.status = "succeeded"
            media_job.result_json = json.dumps(result, default=str)
            await dispatch_db.commit()
        return result
    except Exception as exc:
        async with factory() as fail_db:
            media_job = await fail_db.get(MediaJob, media_job_id)
            if media_job is not None:
                media_job.status = "failed"
                media_job.error_json = json.dumps({"error": str(exc), "type": type(exc).__name__})
                await fail_db.commit()
        raise
    finally:
        if batch_id:
            async with factory() as batch_db:
                await media_job_manager.update_batch_progress(batch_db, batch_id)
        media_job_manager.stream(media_job_id).finish()


for _operation in (
    "subtitle_scan",
    "subtitle_remove",
    "subtitle_embed",
    "subtitle_metadata",
    "subtitle_extract",
    "subtitle_generate",
    "subtitle_policy",
    "subtitle_restore",
    "letterbox_reencode",
):
    register(_operation)(_run_media)
