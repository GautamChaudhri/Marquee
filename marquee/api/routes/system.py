"""System routes — cache stats, tool capabilities, queue + generator health."""

from __future__ import annotations

import logging
import os
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from marquee.api.routes.jobs import job_summary
from marquee.api.routes.webhooks import webhook_state
from marquee.config import settings
from marquee.core import system_metrics
from marquee.core.heal import heal_state
from marquee.core.jobs import job_manager
from marquee.core.letterbox_heal import letterbox_heal_state
from marquee.core.pipeline_config import pipeline_settings
from marquee.database import get_db, reset_database
from marquee.media import binaries
from marquee.ml.hardware import effective_ocr_workers
from marquee.models import Job, MediaJob
from marquee.pipeline.ocr_filter import active_worker_status, paddle_cuda_available

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/system", tags=["system"])


def _cache_stats() -> dict:
    base = settings.poster_cache_path / "movies"
    count = total_bytes = 0
    if base.is_dir():
        for entry in os.scandir(base):
            if entry.is_file() and entry.name.endswith(".jpg"):
                count += 1
                total_bytes += entry.stat().st_size
    return {"posters": count, "bytes": total_bytes}


def _ocr_status() -> dict:
    return {
        "device": pipeline_settings.OCR_DEVICE,
        "configured_workers": pipeline_settings.OCR_WORKERS,
        "effective_workers": effective_ocr_workers(),
        "paddle_cuda_available": paddle_cuda_available(),
        "workers": active_worker_status(),
    }


@router.get("/status")
async def system_status(request: Request, db: Annotated[AsyncSession, Depends(get_db)]):
    queue_rows = (
        await db.execute(select(MediaJob.status, func.count()).group_by(MediaJob.status))
    ).all()
    job_rows = (await db.execute(select(Job.status, func.count()).group_by(Job.status))).all()
    supervisor = getattr(request.app.state, "worker_supervisor", None)
    return {
        "cache": _cache_stats(),
        "heal": heal_state,
        "letterbox_heal": letterbox_heal_state,
        "webhook": webhook_state,
        "tools": binaries.availability(),
        "media_jobs": dict(queue_rows),
        "jobs": dict(job_rows),
        "ocr": _ocr_status(),
        "worker_supervisor": supervisor.status() if supervisor is not None else None,
    }


_ACTIVE_JOB_STATUSES = {"running", "in_progress", "processing"}
_QUEUED_JOB_STATUSES = {"queued", "pending"}


async def _worker_counts(db: AsyncSession) -> dict[str, int]:
    rows = (
        await db.execute(select(Job.status, func.count()).group_by(Job.status))
    ).all()
    counts = {str(status): n for status, n in rows}
    return {
        "active": sum(n for s, n in counts.items() if s in _ACTIVE_JOB_STATUSES),
        "queued": sum(n for s, n in counts.items() if s in _QUEUED_JOB_STATUSES),
    }


@router.get("/metrics")
async def system_metrics_endpoint(db: Annotated[AsyncSession, Depends(get_db)]):
    """Host telemetry for the dashboard CPU/GPU/RAM cards (frontend G1).

    Point-in-time readings; the frontend accumulates sparkline history from
    successive polls. ``gpu`` is ``null`` on hosts without an NVIDIA GPU.
    """
    data = system_metrics.collect(settings.metrics_disk_path)
    data["workers"] = await _worker_counts(db)
    return data


@router.get("/status/generators")
async def generator_health():
    """Subtitle-generation provider health + capabilities."""
    from marquee.core.subtitles.generation import list_generators  # noqa: PLC0415

    return {"generators": await list_generators()}


@router.post("/heal")
async def trigger_heal(db: Annotated[AsyncSession, Depends(get_db)]):
    """Run the self-heal poster existence scan on demand."""
    job = await job_manager.create(
        db, job_type="poster_heal", priority=30, resources={"network_external": 1}
    )
    return job_summary(job)


@router.post("/release-gpu")
async def release_gpu_resources():
    """Drop Marquee's process-local ML caches before another GPU workload."""
    from marquee.pipeline.run_manager import run_manager  # noqa: PLC0415

    busy = run_manager.gpu_busy()
    if busy is not None:
        return {"status": "busy", "active": busy}
    return {"status": "released", **run_manager.release_gpu_resources()}


@router.post("/reset-db")
async def reset_database_endpoint(db: Annotated[AsyncSession, Depends(get_db)]):
    """Delete all application data while keeping the current schema in place."""
    return await reset_database(db)
