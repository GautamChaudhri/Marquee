"""System routes — cache stats, tool capabilities, queue + generator health."""

from __future__ import annotations

import logging
import math
import os
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from marquee.api.job_submission import JobSubmissionResponse, submission_response
from marquee.api.routes.webhooks import webhook_state
from marquee.config import settings
from marquee.core import system_metrics
from marquee.core.configuration_cache import configuration_provider
from marquee.core.heal import latest_heal_summary
from marquee.core.jobs.contracts import TriggerKind
from marquee.core.jobs.labels import humanize_job_type
from marquee.core.jobs.pgqueuer_gateway import pgqueuer_gateway
from marquee.core.jobs.poster_parents import create_poster_parent
from marquee.core.jobs.readiness import connection_budget_report
from marquee.core.jobs.submission import Initiator, SubmissionError
from marquee.core.letterbox_heal import letterbox_heal_state
from marquee.core.pipeline_config import pipeline_settings
from marquee.database import get_db, pool_stats, reset_database
from marquee.media import binaries
from marquee.ml.hardware import effective_ocr_workers
from marquee.models import Job, SchemaContract, SystemMetricsSample
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
    job_rows = (await db.execute(select(Job.phase, func.count()).group_by(Job.phase))).all()
    supervisor = getattr(request.app.state, "worker_supervisor", None)
    return {
        "cache": _cache_stats(),
        "configuration": configuration_provider.health(),
        "heal": await latest_heal_summary(db),
        "letterbox_heal": letterbox_heal_state,
        "webhook": webhook_state,
        "tools": binaries.availability(),
        "media_jobs": {},
        "jobs": dict(job_rows),
        "ocr": _ocr_status(),
        "worker_supervisor": supervisor.status() if supervisor is not None else None,
    }


async def _worker_counts(db: AsyncSession) -> dict[str, int]:
    """Return counts from the final canonical phase vocabulary."""
    rows = (await db.execute(select(Job.phase, func.count()).group_by(Job.phase))).all()
    counts = {str(phase): count for phase, count in rows}
    return {
        "active": sum(counts.get(phase, 0) for phase in ("running", "stopping")),
        "queued": counts.get("queued", 0),
    }


def _counter_rate(
    current: int | float | None, previous: int | float | None, elapsed: float
) -> float | None:
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


async def _history_jobs(
    db: AsyncSession, *, start_at: datetime, end_at: datetime
) -> list[dict]:
    rows = (
        (
            await db.execute(
                select(Job)
                .where(
                    Job.started_at.is_not(None),
                    Job.started_at <= end_at,
                    func.coalesce(Job.terminal_at, end_at) >= start_at,
                )
                .order_by(Job.started_at.asc(), Job.id.asc())
            )
        )
        .scalars()
        .all()
    )
    history: list[dict] = []
    for job in rows:
        snapshot = job.subject_snapshot if isinstance(job.subject_snapshot, dict) else {}
        subject = (
            snapshot.get("display_name")
            or snapshot.get("title")
            or snapshot.get("name")
            or job.subject_reference
        )
        history.append(
            {
                "job_id": job.id,
                "type": job.type,
                "label": humanize_job_type(job.type),
                "status": job.outcome if job.phase == "terminal" else job.phase,
                "subject": subject,
                "started_at": job.started_at.isoformat() if job.started_at else None,
                "finished_at": job.terminal_at.isoformat() if job.terminal_at else None,
            }
        )
    return history


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
    data["db_pool"] = pool_stats()
    return data


@router.get("/job-transport")
async def job_transport_diagnostics(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Return bounded aggregate transport diagnostics without rows or payloads."""
    now = datetime.now(UTC)
    oldest = await db.scalar(
        select(func.min(Job.eligible_at)).where(
            Job.phase == "queued",
            Job.eligible_at <= now,
        )
    )
    queue = await pgqueuer_gateway.queue_statistics(db)
    held_failed = sum(item["count"] for item in queue if item["status"] == "failed")
    picked = sum(item["count"] for item in queue if item["status"] == "picked")
    contracts = list(await db.scalars(select(SchemaContract).order_by(SchemaContract.component)))
    connection_rows = await db.execute(
        text(
            """
            SELECT application_name, count(*)::int AS count
            FROM pg_stat_activity
            WHERE datname = current_database() AND application_name LIKE 'marquee:%'
            GROUP BY application_name
            ORDER BY application_name
            """
        )
    )
    role_connections = {
        row.application_name: row.count for row in connection_rows if row.application_name
    }

    supervisor = getattr(request.app.state, "worker_supervisor", None)
    supervisor_status = supervisor.status() if supervisor is not None else None
    workers = [] if supervisor_status is None else [
        child
        for child in supervisor_status["children"]
        if child["name"] == "scheduler" or child["name"].startswith("worker-")
    ]
    listener_healthy = bool(workers) and all(
        child["running"] and not child["degraded"] for child in workers
    )
    return {
        "queue": queue,
        "oldest_eligible_age_seconds": (
            max(0.0, (now - oldest).total_seconds()) if oldest is not None else None
        ),
        "picked": picked,
        "held_failed": held_failed,
        "listener": {
            "healthy": listener_healthy,
            "last_observed_event_at": None,
            "source": "embedded_supervisor" if supervisor is not None else "external",
        },
        "contracts": [
            {
                "component": contract.component,
                "expected_version": contract.expected_version,
                "durability": contract.durability,
                "catalog_fingerprint": contract.catalog_fingerprint,
            }
            for contract in contracts
        ],
        "connections": {
            "roles": role_connections,
            "observed": sum(role_connections.values()),
            "budget": connection_budget_report(),
        },
    }


@router.get("/status/generators")
async def generator_health():
    """Subtitle-generation provider health + capabilities."""
    from marquee.core.subtitles.generation import list_generators  # noqa: PLC0415

    return {"generators": await list_generators()}


@router.post("/heal", status_code=202)
async def trigger_heal(
    db: Annotated[AsyncSession, Depends(get_db)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
) -> JobSubmissionResponse:
    """Run the self-heal poster existence scan on demand."""
    initiator = Initiator(kind="system", identifier="system-api")
    if db.in_transaction():
        await db.commit()
    try:
        async with db.begin():
            result = await create_poster_parent(
                db,
                parent_job_type="poster_heal",
                idempotency_key=idempotency_key,
                trigger=TriggerKind.BATCH,
                initiator=initiator,
                priority=30,
            )
    except (SubmissionError, ValueError) as exc:
        raise HTTPException(status_code=422, detail="poster_heal_scope_invalid") from exc
    return submission_response(result.parent)


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
