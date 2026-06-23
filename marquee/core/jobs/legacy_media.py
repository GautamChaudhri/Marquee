"""Bridge established media handlers into the central job worker.

The media-operation code already contains careful remux/re-encode safety
checks.  This adapter keeps its durable media row as an operation record while
the generic Job is the authoritative scheduling, lease, and UI record.
"""

from __future__ import annotations

import logging

from marquee.core.jobs.handlers import register
from marquee.core.jobs.manager import job_manager
from marquee.database import _get_session_factory
from marquee.models import Job, MediaJob

logger = logging.getLogger(__name__)


async def _run_media(job: Job) -> dict:
    from marquee.core.media_jobs.handlers import dispatch  # noqa: PLC0415
    from marquee.core.media_jobs.manager import media_job_manager  # noqa: PLC0415

    media_job = None
    media_job_id = job.payload.get("media_job_id")
    if not media_job_id:
        raise RuntimeError("media bridge job has no media_job_id")
    factory = _get_session_factory()
    async with factory() as db:
        media_job = await db.get(MediaJob, media_job_id)
        generic = await db.get(Job, job.id)
        if media_job is None or generic is None:
            raise RuntimeError("media bridge state is missing")
        media_job.status = "running"
        await db.commit()

        async def emit(_db, _job_id, stage, state, *, message=None, progress=None, persist=True):
            # Preserve the legacy event/audit stream while mirroring it into
            # the generic stream consumed by the future job-management UI.
            await media_job_manager.emit(
                db,
                media_job.job_id,
                stage,
                state,
                message=message,
                progress=progress,
                persist=persist,
            )
            current = await db.get(Job, job.id)
            if current is not None:
                current.current_stage = stage
                if progress is not None:
                    current.progress = progress
                await job_manager.emit(
                    db,
                    current,
                    state=state,
                    stage=stage,
                    message=message,
                    detail=progress,
                    persist=persist,
                )
            await db.commit()

        try:
            result = await dispatch(db, media_job, emit)
            media_job.status = "succeeded"
            await db.commit()
            return result
        except Exception as exc:
            import json as _json

            media_job.status = "failed"
            media_job.error_json = _json.dumps({"error": str(exc), "type": type(exc).__name__})
            await db.commit()
            raise
        finally:
            if media_job is not None:
                media_job_manager.stream(media_job.job_id).finish()


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
