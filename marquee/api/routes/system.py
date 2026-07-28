"""System routes — cache stats, host telemetry, and queue health."""

from __future__ import annotations

import logging
import math
import os
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from marquee.api.job_submission import JobSubmissionResponse, submission_response
from marquee.api.system_operations import (
    OperationsConnectionBudget,
    OperationsDatabase,
    OperationsEvents,
    OperationsEvidenceRetention,
    OperationsHistoryResponse,
    OperationsNode,
    OperationsRuntimeInstance,
    OperationsRuntimeInstances,
    OperationsSchedules,
    OperationsScheduleState,
    OperationsSnapshot,
    OperationsStorage,
    OperationsTransport,
    OperationsWorkers,
)
from marquee.config import settings
from marquee.core import system_metrics
from marquee.core.configuration_cache import configuration_provider
from marquee.core.jobs.contracts import TriggerKind
from marquee.core.jobs.labels import humanize_job_type
from marquee.core.jobs.manifest import JOB_DEFINITION_REGISTRY
from marquee.core.jobs.pgqueuer_gateway import pgqueuer_gateway
from marquee.core.jobs.poster_parents import create_poster_parent
from marquee.core.jobs.poster_summary import latest_poster_heal_summary
from marquee.core.jobs.readiness import connection_budget_report, schedule_catalog_report
from marquee.core.jobs.submission import Initiator, SubmissionError
from marquee.core.pipeline_config import pipeline_settings
from marquee.database import get_db, pool_stats, reset_database
from marquee.ml.hardware import effective_ocr_workers
from marquee.models import (
    Job,
    JobArtifact,
    JobLog,
    RuntimeInstance,
    SchemaContract,
    SystemMetricsSample,
)
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
        "heal": await latest_poster_heal_summary(db),
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


def _sanitized_runtime_instance(
    instance: RuntimeInstance, *, now: datetime
) -> OperationsRuntimeInstance:
    capabilities = instance.capabilities if isinstance(instance.capabilities, dict) else {}
    containment_raw = capabilities.get("containment", {})
    containment_items = containment_raw.items() if isinstance(containment_raw, dict) else ()
    containment = {
        str(key)[:50]: value for key, value in containment_items if isinstance(value, (str, bool))
    }
    availability: dict[str, bool] = {}
    gpu = capabilities.get("gpu")
    if isinstance(gpu, dict):
        availability["gpu"] = bool(gpu.get("available"))
    return OperationsRuntimeInstance(
        role=instance.role,
        node_label=instance.node_label[:100],
        build=instance.build[:64],
        readiness=instance.readiness,
        heartbeat_fresh=instance.stopped_at is None and instance.heartbeat_expires_at > now,
        last_heartbeat_at=instance.last_heartbeat_at,
        entrypoints=sorted(set(instance.advertised_entrypoints))[:32],
        containment=containment,
        capability_availability=availability,
    )


async def _runtime_instance_summary(
    db: AsyncSession, *, now: datetime
) -> OperationsRuntimeInstances:
    active_condition = (
        RuntimeInstance.stopped_at.is_(None)
        & (RuntimeInstance.readiness != "stopped")
        & (RuntimeInstance.heartbeat_expires_at > now)
    )
    stale_condition = (
        RuntimeInstance.stopped_at.is_(None)
        & (RuntimeInstance.readiness != "stopped")
        & (RuntimeInstance.heartbeat_expires_at <= now)
    )
    stopped_condition = (RuntimeInstance.stopped_at.is_not(None)) | (
        RuntimeInstance.readiness == "stopped"
    )
    active = int(await db.scalar(select(func.count()).where(active_condition)) or 0)
    stale = int(await db.scalar(select(func.count()).where(stale_condition)) or 0)
    stopped = int(await db.scalar(select(func.count()).where(stopped_condition)) or 0)
    last_heartbeat_at = await db.scalar(select(func.max(RuntimeInstance.last_heartbeat_at)))
    role_rows = (
        await db.execute(
            select(RuntimeInstance.role, func.count())
            .where(active_condition)
            .group_by(RuntimeInstance.role)
        )
    ).all()
    roles = {str(role): int(count) for role, count in role_rows}
    limit = settings.JOB_RUNTIME_QUERY_LIMIT
    rows = list(
        await db.scalars(
            select(RuntimeInstance)
            .order_by(RuntimeInstance.last_heartbeat_at.desc(), RuntimeInstance.id)
            .limit(limit + 1)
        )
    )
    instances = [_sanitized_runtime_instance(item, now=now) for item in rows[:limit]]
    capable_entrypoints = {
        entrypoint
        for item in rows
        if item.role == "worker"
        and item.readiness == "ready"
        and item.stopped_at is None
        and item.heartbeat_expires_at > now
        for entrypoint in item.advertised_entrypoints
    }
    required_entrypoints = {
        definition.entrypoint for definition in JOB_DEFINITION_REGISTRY if definition.enabled
    }
    return OperationsRuntimeInstances(
        active=active,
        stale=stale,
        stopped=stopped,
        roles=roles,
        last_heartbeat_at=last_heartbeat_at,
        scheduler_present=roles.get("scheduler", 0) > 0,
        capability_mismatches=sorted(required_entrypoints - capable_entrypoints),
        instances=instances,
        truncated=len(rows) > limit,
    )


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
) -> tuple[list[dict], bool]:
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
                .limit(201)
            )
        )
        .scalars()
        .all()
    )
    truncated = len(rows) > 200
    history: list[dict] = []
    for job in rows[:200]:
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
    return history, truncated


@router.get("/metrics/history", response_model=OperationsHistoryResponse)
async def system_metrics_history(
    db: Annotated[AsyncSession, Depends(get_db)],
    window: Literal["15m", "1h", "6h", "24h"] = "1h",
    resolution: int = 120,
):
    window_seconds = _HISTORY_WINDOWS[window]
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
    jobs, jobs_truncated = await _history_jobs(db, start_at=start_at, end_at=end_at)
    return {
        "window": window,
        "start_at": start_at.isoformat(),
        "end_at": end_at.isoformat(),
        "points": _downsample_history(points, resolution),
        "jobs": jobs,
        "jobs_truncated": jobs_truncated,
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

    runtime_instances = await _runtime_instance_summary(db, now=now)
    listener_last_heartbeat_at = await db.scalar(
        select(func.max(RuntimeInstance.last_heartbeat_at)).where(
            RuntimeInstance.role == "worker",
            RuntimeInstance.readiness == "ready",
            RuntimeInstance.stopped_at.is_(None),
            RuntimeInstance.heartbeat_expires_at > now,
        )
    )
    listener_healthy = listener_last_heartbeat_at is not None
    return {
        "queue": queue,
        "oldest_eligible_age_seconds": (
            max(0.0, (now - oldest).total_seconds()) if oldest is not None else None
        ),
        "picked": picked,
        "held_failed": held_failed,
        "listener": {
            "healthy": listener_healthy,
            "last_observed_event_at": listener_last_heartbeat_at,
            "source": "runtime_instances.worker",
        },
        "runtime_instances": runtime_instances,
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


@router.get("/operations", response_model=OperationsSnapshot)
async def operations_snapshot(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> OperationsSnapshot:
    """One bounded typed snapshot for the lazy secondary Operations surface."""
    metrics = system_metrics.collect(settings.metrics_disk_path)
    worker_counts = await _worker_counts(db)
    transport = await job_transport_diagnostics(request, db)
    supervisor = getattr(request.app.state, "worker_supervisor", None)
    listener = transport["listener"]
    connections = transport["connections"]
    budget = connections["budget"]
    runtime_instances = transport["runtime_instances"]
    schedule_report = schedule_catalog_report()
    pool = pool_stats()
    cache = _cache_stats()
    cpu = metrics["cpu"]
    ram = metrics["ram"]
    gpu = metrics["gpu"]
    disk = metrics["disk"]
    network = metrics["net"]
    now = datetime.now(UTC)
    artifact_overdue, artifact_oldest = (
        await db.execute(
            select(func.count(), func.min(JobArtifact.expires_at)).where(
                JobArtifact.status == "available", JobArtifact.expires_at <= now
            )
        )
    ).one()
    log_overdue, log_oldest = (
        await db.execute(
            select(func.count(), func.min(JobLog.expires_at)).where(
                JobLog.seal_status == "sealed", JobLog.expires_at <= now
            )
        )
    ).one()
    overdue_times = [value for value in (artifact_oldest, log_oldest) if value is not None]

    return OperationsSnapshot(
        generated_at=datetime.now(UTC),
        node=OperationsNode(
            cpu_model=str(cpu["model"]),
            cpu_percent=cpu.get("avg"),
            cpu_temperature_c=cpu.get("temp"),
            ram_percent=ram.get("pct"),
            ram_used_bytes=ram.get("used"),
            ram_total_bytes=ram.get("total"),
            gpu_model=gpu.get("model") if gpu else None,
            gpu_percent=gpu.get("util") if gpu else None,
            gpu_memory_percent=gpu.get("memUtil") if gpu else None,
            network_received_bytes=network.get("bytesRecv"),
            network_sent_bytes=network.get("bytesSent"),
            uptime=str(metrics["uptime"]),
        ),
        workers=OperationsWorkers(
            active=worker_counts["active"],
            queued=worker_counts["queued"],
            supervisor_available=supervisor is not None,
            listener_healthy=bool(listener["healthy"]),
            runtime_instances=runtime_instances,
        ),
        transport=OperationsTransport(
            picked=int(transport["picked"]),
            held_failed=int(transport["held_failed"]),
            oldest_eligible_age_seconds=transport["oldest_eligible_age_seconds"],
        ),
        database=OperationsDatabase(
            observed_connections=int(connections["observed"]),
            connection_roles=connections["roles"],
            pool_size=pool["size"],
            pool_checked_in=pool["checked_in"],
            pool_checked_out=pool["checked_out"],
            pool_overflow=pool["overflow"],
            budget=OperationsConnectionBudget(
                configured=int(budget["configured"]),
                maximum=int(budget["maximum"]),
                within_budget=bool(budget["within_budget"]),
            ),
        ),
        events=OperationsEvents(
            listener_healthy=bool(listener["healthy"]),
            source=str(listener["source"]),
            last_observed_event_at=listener["last_observed_event_at"],
        ),
        storage=OperationsStorage(
            poster_cache_items=cache["posters"],
            poster_cache_bytes=cache["bytes"],
            disk_percent=disk.get("pct"),
            disk_used_bytes=disk.get("used"),
            disk_total_bytes=disk.get("total"),
        ),
        schedules=OperationsSchedules(
            production_schedules_enabled=bool(schedule_report["production_schedules_enabled"]),
            scheduler_present=runtime_instances.scheduler_present,
            effectively_enabled=sum(
                bool(item["effectively_enabled"]) for item in schedule_report["schedules"]
            ),
            schedules=[
                OperationsScheduleState(
                    key=str(item["key"]),
                    registered=bool(item["registered"]),
                    individually_activated=bool(item["individually_activated"]),
                    configured=bool(item["configured"]),
                    effectively_enabled=bool(item["effectively_enabled"]),
                    disabled_reason=(
                        str(item["disabled_reason"])
                        if item["disabled_reason"] is not None
                        else None
                    ),
                )
                for item in schedule_report["schedules"]
            ],
        ),
        evidence_retention=OperationsEvidenceRetention(
            overdue_artifacts=int(artifact_overdue),
            overdue_logs=int(log_overdue),
            oldest_overdue_at=min(overdue_times) if overdue_times else None,
            overdue=bool(artifact_overdue or log_overdue),
        ),
        contracts=transport["contracts"],
    )


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


@router.post("/reset-db")
async def reset_database_endpoint(db: Annotated[AsyncSession, Depends(get_db)]):
    """Return the installation to a clean state, keeping the current schema.

    Live jobs are cancelled and awaited first so nothing is left running against
    deleted rows, and the job evidence on disk is removed with the rows that owned
    it. The response reports anything that refused to stop in time.
    """
    return await reset_database(db)
