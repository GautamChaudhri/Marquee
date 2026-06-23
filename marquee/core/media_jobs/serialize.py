"""Shared MediaJob -> API dict serialization.

Used by the media-jobs routes (status/list endpoints) and by any other route
that needs to embed a job snapshot inline (e.g. the subtitles inspect
endpoint embedding the active job for a media file).
"""

from __future__ import annotations

import json

from marquee.models import MediaJob

_STAGE_PERCENT = {
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


def job_dict(job: MediaJob) -> dict:
    progress = None
    if job.status == "running" or job.status in ("succeeded", "completed"):
        percent = 0
        if job.status in ("succeeded", "completed"):
            percent = 100
        elif job.progress_total > 0:
            percent = int((job.progress_done / job.progress_total) * 100)
        else:
            percent = (
                _STAGE_PERCENT.get((job.stage, "start"), 0)
                or _STAGE_PERCENT.get((job.stage, "done"), 0)
                or 0
            )

        progress = {
            "stage": job.stage or "running",
            "percent": percent,
            "message": f"Stage: {job.stage or 'running'}",
        }

    return {
        "job_id": job.job_id,
        "operation": job.operation,
        "status": job.status,
        "stage": job.stage,
        "trigger": job.trigger,
        "media_file_id": job.media_file_id,
        "batch_id": job.batch_id,
        "progress_done": job.progress_done,
        "progress_total": job.progress_total,
        "progress": progress,
        "events_url": f"/api/media-jobs/{job.job_id}/events",
        "plan": json.loads(job.plan_json) if job.plan_json else None,
        "result": json.loads(job.result_json) if job.result_json else None,
        "error": json.loads(job.error_json) if job.error_json else None,
        "plan_expires_at": job.plan_expires_at.isoformat() if job.plan_expires_at else None,
        "created_at": job.created_at.isoformat() if job.created_at else None,
    }
