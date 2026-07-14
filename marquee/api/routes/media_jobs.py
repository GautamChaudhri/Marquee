"""Fail-closed endpoints for the removed duplicate media-job lifecycle."""

from __future__ import annotations

from fastapi import APIRouter

from marquee.core.jobs.manager import UnmigratedJobPlatformError

router = APIRouter(prefix="/api/media-jobs", tags=["media-jobs"])


def _unavailable(operation: str) -> None:
    raise UnmigratedJobPlatformError(f"media_job.{operation}")


@router.post("/{job_id}/confirm")
async def confirm_job(job_id: str):
    _unavailable(f"confirm:{job_id}")


@router.get("/{job_id}")
async def get_job(job_id: str):
    _unavailable(f"get:{job_id}")


@router.get("/{job_id}/events")
async def job_events(job_id: str):
    _unavailable(f"events:{job_id}")


@router.post("/{job_id}/cancel")
async def cancel_job(job_id: str):
    _unavailable(f"cancel:{job_id}")


@router.get("")
async def list_jobs(operation: str | None = None, limit: int = 100):
    _unavailable(f"list:{operation or 'all'}:{limit}")


@router.post("/{job_id}/restore")
async def restore_backup(job_id: str):
    _unavailable(f"restore:{job_id}")


@router.delete("/{job_id}/backup")
async def delete_backup(job_id: str):
    _unavailable(f"delete_backup:{job_id}")
