"""System routes — cache stats, tool capabilities, queue + generator health."""

from __future__ import annotations

import logging
import math
import os
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from marquee.api.routes.jobs import _QUEUED_ISH, _resolve_subject_titles, job_summary
from marquee.api.routes.webhooks import webhook_state
from marquee.config import settings
from marquee.core import system_metrics
from marquee.core.heal import heal_state
from marquee.core.jobs import job_manager
from marquee.core.jobs.labels import humanize_job_type
from marquee.core.jobs.manager import ACTIVE
from marquee.core.letterbox_heal import letterbox_heal_state
from marquee.core.pipeline_config import pipeline_settings
from marquee.database import get_db, reset_database
from marquee.media import binaries
from marquee.ml.hardware import effective_ocr_workers
from marquee.models import Job, MediaJob, SystemMetricsSample
from marquee.pipeline.ocr_filter import active_worker_status, paddle_cuda_available

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/system", tags=["system"])
_HISTORY_WINDOWS = {"15m": 15 * 60, "1h": 60 * 60, "6h": 6 * 60 * 60, "24h": 24 * 60 * 60}


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


async def _worker_counts(db: AsyncSession) -> dict[str, int]:
    """Active/queued job counts for the dashboard cards.

    Bug fix: this used to define its own ``_ACTIVE_JOB_STATUSES``/
    ``_QUEUED_JOB_STATUSES`` sets containing statuses the job manager never
    actually sets (``"in_progress"``, ``"processing"``, ``"pending"``), so
    these counts were silently wrong since the endpoint shipped. Reuses
    ``manager.ACTIVE`` and ``jobs._QUEUED_ISH`` — the real status vocabulary
    — instead of a second, drifted copy.
    """
    rows = (await db.execute(select(Job.status, func.count()).group_by(Job.status))).all()
    counts = {str(status): n for status, n in rows}
    return {
        "active": sum(n for s, n in counts.items() if s in ACTIVE),
        "queued": sum(n for s, n in counts.items() if s in _QUEUED_ISH),
    }


def _counter_rate(current: int | float | None, previous: int | float | None, elapsed: float) -> float | None:
    if current is None or previous is None or elapsed <= 0 or current < previous:
        return None
    return (current - previous) / elapsed


def _avg(values: list[float | int | None]) -> float | None:
    present = [float(value) for value in values if value is not None]
    if not present:
        return None
    return round(sum(present) / len(present), 3)


def _history_bucket(points: list[dict]) -> dict:
    ts = points[min(len(points) - 1, len(points) // 2)]["ts"]
    jobs = max((int(point.get("active_jobs") or 0) for point in points), default=0)
    return {
        "ts": datetime.fromtimestamp(ts, UTC).isoformat(),
        "cpu_avg": _avg([point.get("cpu_avg") for point in points]),
        "gpu_util": _avg([point.get("gpu_util") for point in points]),
        "gpu_mem": _avg([point.get("gpu_mem") for point in points]),
        "gpu_enc": _avg([point.get("gpu_enc") for point in points]),
        "gpu_dec": _avg([point.get("gpu_dec") for point in points]),
        "ram_pct": _avg([point.get("ram_pct") for point in points]),
        "disk_read_bps": _avg([point.get("disk_read_bps") for point in points]),
        "disk_write_bps": _avg([point.get("disk_write_bps") for point in points]),
        "net_recv_bps": _avg([point.get("net_recv_bps") for point in points]),
        "net_sent_bps": _avg([point.get("net_sent_bps") for point in points]),
        "active_jobs": jobs,
    }


def _downsample_history(points: list[dict], target_points: int) -> list[dict]:
    if len(points) <= target_points:
        return [
            {
                **point,
                "ts": datetime.fromtimestamp(point["ts"], UTC).isoformat(),
            }
            for point in points
        ]
    bucket_size = max(1, math.ceil(len(points) / target_points))
    downsampled: list[dict] = []
    for start in range(0, len(points), bucket_size):
        bucket = points[start : start + bucket_size]
        if bucket:
            downsampled.append(_history_bucket(bucket))
    return downsampled


async def _history_jobs(db: AsyncSession, *, start_at: datetime, end_at: datetime) -> list[dict]:
    rows = (
        (
            await db.execute(
                select(Job)
                .where(
                    Job.started_at.is_not(None),
                    Job.started_at <= end_at,
                    func.coalesce(Job.finished_at, end_at) >= start_at,
                )
                .order_by(Job.started_at.asc(), Job.id.asc())
            )
        )
        .scalars()
        .all()
    )
    titles = await _resolve_subject_titles(db, rows)
    return [
        {
            "job_id": job.id,
            "type": job.type,
            "label": humanize_job_type(job.type),
            "status": job.status,
            "subject": titles.get((job.subject_type, job.subject_id)),
            "started_at": job.started_at.isoformat() if job.started_at else None,
            "finished_at": job.finished_at.isoformat() if job.finished_at else None,
        }
        for job in rows
    ]


@router.get("/metrics/history")
async def system_metrics_history(
    db: Annotated[AsyncSession, Depends(get_db)],
    window: str = "1h",
    resolution: int = 120,
):
    window_seconds = _HISTORY_WINDOWS.get(window)
    if window_seconds is None:
        return {"window": window, "points": [], "jobs": []}
    resolution = min(max(resolution, 12), 240)
    end_at = datetime.now(UTC)
    start_at = end_at - timedelta(seconds=window_seconds)
    samples = (
        (
            await db.execute(
                select(SystemMetricsSample)
                .where(SystemMetricsSample.created_at >= start_at)
                .order_by(SystemMetricsSample.created_at.asc(), SystemMetricsSample.id.asc())
            )
        )
        .scalars()
        .all()
    )
    points: list[dict] = []
    previous: SystemMetricsSample | None = None
    for sample in samples:
        elapsed = (
            (sample.created_at - previous.created_at).total_seconds()
            if previous is not None and previous.created_at is not None
            else 0.0
        )
        point = {
            "ts": sample.created_at.timestamp(),
            "cpu_avg": sample.cpu.get("avg") if isinstance(sample.cpu, dict) else None,
            "gpu_util": sample.gpu.get("util") if isinstance(sample.gpu, dict) else None,
            "gpu_mem": sample.gpu.get("memUtil") if isinstance(sample.gpu, dict) else None,
            "gpu_enc": sample.gpu.get("enc") if isinstance(sample.gpu, dict) else None,
            "gpu_dec": sample.gpu.get("dec") if isinstance(sample.gpu, dict) else None,
            "ram_pct": sample.ram.get("pct") if isinstance(sample.ram, dict) else None,
            "disk_read_bps": _counter_rate(
                sample.disk.get("readBytes") if isinstance(sample.disk, dict) else None,
                previous.disk.get("readBytes")
                if previous is not None and isinstance(previous.disk, dict)
                else None,
                elapsed,
            ),
            "disk_write_bps": _counter_rate(
                sample.disk.get("writeBytes") if isinstance(sample.disk, dict) else None,
                previous.disk.get("writeBytes")
                if previous is not None and isinstance(previous.disk, dict)
                else None,
                elapsed,
            ),
            "net_recv_bps": _counter_rate(
                sample.net.get("bytesRecv") if isinstance(sample.net, dict) else None,
                previous.net.get("bytesRecv")
                if previous is not None and isinstance(previous.net, dict)
                else None,
                elapsed,
            ),
            "net_sent_bps": _counter_rate(
                sample.net.get("bytesSent") if isinstance(sample.net, dict) else None,
                previous.net.get("bytesSent")
                if previous is not None and isinstance(previous.net, dict)
                else None,
                elapsed,
            ),
            "active_jobs": len(sample.active_jobs) if sample.active_jobs else 0,
        }
        points.append(point)
        previous = sample
    return {
        "window": window,
        "start_at": start_at.isoformat(),
        "end_at": end_at.isoformat(),
        "points": _downsample_history(points, resolution),
        "jobs": await _history_jobs(db, start_at=start_at, end_at=end_at),
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
    job = await job_manager.create_and_run(
        db,
        job_type="poster_heal",
        priority=30,
        subject_type="maintenance",
        subject_id="poster-heal",
        worker_id="inline-api",
    )
    return {**job_summary(job), **(job.result or {})}


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
