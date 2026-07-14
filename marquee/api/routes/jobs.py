"""Canonical job inspection and the narrow JMC2A control surface."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from marquee.core.jobs.labels import humanize_job_type
from marquee.core.jobs.manager import UnmigratedJobPlatformError
from marquee.core.jobs.pgqueuer_gateway import PgQueuerGatewayError, pgqueuer_gateway
from marquee.database import _get_session_factory, get_db
from marquee.models import Job, JobAttempt, JobEvent

router = APIRouter(prefix="/api/jobs", tags=["jobs"])
logger = logging.getLogger(__name__)

_SSE_TIMEOUT_SECONDS = 3600
_ACTIVE_PHASES = {"queued", "running", "stopping"}


def _display_status(job: Job) -> str:
    return job.outcome if job.phase == "terminal" and job.outcome else job.phase


def job_summary(job: Job, *, subject_title: str | None = None) -> dict:
    snapshot = job.subject_snapshot if isinstance(job.subject_snapshot, dict) else {}
    title = subject_title or snapshot.get("title") or snapshot.get("label")
    subject = None
    if job.subject_kind or job.subject_reference:
        subject = {
            "type": job.subject_kind,
            "id": job.subject_reference,
            "title": title,
            "snapshot": snapshot,
        }
    return {
        "job_id": job.id,
        "type": job.type,
        "label": humanize_job_type(job.type),
        "phase": job.phase,
        "outcome": job.outcome,
        "status": _display_status(job),
        "desired_state": job.desired_state,
        "priority": job.priority,
        "parent_id": job.parent_id,
        "root_id": job.root_id,
        "correlation_id": job.correlation_id,
        "retry_of_job_id": job.retry_of_job_id,
        "subject": subject,
        "stage": job.current_stage,
        "current_subject": job.current_subject,
        "progress": job.progress,
        "progress_sequence": job.progress_sequence,
        "attention": job.attention,
        "configuration_version": job.configuration_version,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "planned_at": job.planned_at.isoformat() if job.planned_at else None,
        "queued_at": job.queued_at.isoformat() if job.queued_at else None,
        "eligible_at": job.eligible_at.isoformat() if job.eligible_at else None,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "stopping_at": job.stopping_at.isoformat() if job.stopping_at else None,
        "terminal_at": job.terminal_at.isoformat() if job.terminal_at else None,
        "status_url": f"/api/jobs/{job.id}",
        "events_url": f"/api/jobs/{job.id}/events",
    }


def _job_detail_data(job: Job) -> dict:
    data = job_summary(job)
    data.update(
        {
            "payload_version": job.payload_version,
            "result_version": job.result_version,
            "error_version": job.error_version,
            "request": job.request,
            "plan": job.plan,
            "result": job.result,
            "error": job.error,
            "retry_policy": job.retry_policy,
            "execution_policy_id": job.execution_policy_id,
            "configuration_snapshot": job.configuration_snapshot,
            "trigger_kind": job.trigger_kind,
            "initiator": job.initiator,
            "feature_area": job.feature_area,
            "presentation_family": job.presentation_family,
            "dispatch_generation": job.dispatch_generation,
        }
    )
    return data


async def _load_child_jobs(db: AsyncSession, parent_id: str) -> list[Job]:
    return list(
        (
            await db.scalars(
                select(Job)
                .where(Job.parent_id == parent_id)
                .order_by(Job.created_at.asc(), Job.id.asc())
            )
        ).all()
    )


@router.get("")
async def list_jobs(
    db: Annotated[AsyncSession, Depends(get_db)],
    status: str | None = None,
    type: str | None = None,
    subject_type: str | None = None,
    subject_id: str | None = None,
    parent_id: str | None = None,
    correlation_id: str | None = None,
    active: bool = False,
    queued_only: bool = False,
    before: int | None = None,
    since: int | None = None,
    until: int | None = None,
    limit: int = Query(50, ge=1, le=200),
):
    query = select(Job)
    if status:
        if status in {"planned", "queued", "running", "stopping", "terminal"}:
            query = query.where(Job.phase == status)
        else:
            query = query.where(Job.outcome == status)
    if active:
        query = query.where(Job.phase.in_(_ACTIVE_PHASES))
    if queued_only:
        query = query.where(Job.phase == "queued")
    if type:
        query = query.where(Job.type == type)
    if subject_type:
        query = query.where(Job.subject_kind == subject_type)
    if subject_id:
        query = query.where(Job.subject_reference == subject_id)
    if parent_id:
        query = query.where(Job.parent_id == parent_id)
    if correlation_id:
        query = query.where(Job.correlation_id == correlation_id)
    if before:
        query = query.where(Job.created_at < datetime.fromtimestamp(before, UTC))
    if since:
        query = query.where(Job.created_at >= datetime.fromtimestamp(since, UTC))
    if until:
        query = query.where(Job.created_at <= datetime.fromtimestamp(until, UTC))
    order = (Job.priority.desc(), Job.created_at.asc(), Job.id.asc()) if queued_only else (
        Job.created_at.desc(),
        Job.id.desc(),
    )
    rows = list((await db.scalars(query.order_by(*order).limit(limit + 1))).all())
    has_more = len(rows) > limit
    rows = rows[:limit]
    next_before = (
        int(rows[-1].created_at.timestamp()) if has_more and rows and rows[-1].created_at else None
    )
    return {"jobs": [job_summary(row) for row in rows], "next_before": next_before}


@router.get("/metrics")
async def job_metrics(db: Annotated[AsyncSession, Depends(get_db)]):
    phase_counts = dict(
        (await db.execute(select(Job.phase, func.count()).group_by(Job.phase))).all()
    )
    outcome_counts = dict(
        (await db.execute(select(Job.outcome, func.count()).group_by(Job.outcome))).all()
    )
    outcome_counts.pop(None, None)
    return {
        "counts": {**phase_counts, **outcome_counts},
        "phases": phase_counts,
        "outcomes": outcome_counts,
        "resources": [],
        "workers": [],
    }


def _percentile(durations: list[float], p: float) -> float | None:
    if not durations:
        return None
    return round(durations[min(len(durations) - 1, int(len(durations) * p))], 3)


@router.get("/metrics/by-type")
async def job_metrics_by_type(
    db: Annotated[AsyncSession, Depends(get_db)],
    limit_per_type: int = Query(200, ge=1, le=2000),
):
    types = (await db.scalars(select(Job.type).distinct())).all()
    out: dict[str, dict] = {}
    for job_type in types:
        rows = (
            await db.execute(
                select(Job.outcome, Job.started_at, Job.terminal_at)
                .where(Job.type == job_type, Job.phase == "terminal")
                .order_by(Job.terminal_at.desc())
                .limit(limit_per_type)
            )
        ).all()
        if not rows:
            continue
        durations = sorted(
            (row.terminal_at - row.started_at).total_seconds()
            for row in rows
            if row.started_at and row.terminal_at
        )
        succeeded = sum(1 for row in rows if row.outcome == "succeeded")
        out[job_type] = {
            "sample_size": len(rows),
            "success_rate": round(succeeded / len(rows), 3),
            "duration_seconds": {
                "avg": round(sum(durations) / len(durations), 3) if durations else None,
                "p50": _percentile(durations, 0.5),
                "p95": _percentile(durations, 0.95),
            },
        }
    return {"by_type": out}


@router.get("/{job_id}")
async def get_job(job_id: str, db: Annotated[AsyncSession, Depends(get_db)]):
    job = await db.get(Job, job_id)
    if job is None:
        raise HTTPException(404, "Job not found")
    attempts = list(
        (
            await db.scalars(
                select(JobAttempt).where(JobAttempt.job_id == job_id).order_by(JobAttempt.number)
            )
        ).all()
    )
    events = list(
        (
            await db.scalars(
                select(JobEvent).where(JobEvent.job_id == job_id).order_by(JobEvent.id)
            )
        ).all()
    )
    children = await _load_child_jobs(db, job_id)
    data = _job_detail_data(job)
    data.update(
        {
            "attempts": [
                {
                    "id": attempt.id,
                    "number": attempt.number,
                    "fence_token": attempt.fence_token,
                    "phase": attempt.phase,
                    "outcome": attempt.outcome,
                    "worker_node_id": attempt.worker_node_id,
                    "started_at": attempt.started_at.isoformat() if attempt.started_at else None,
                    "finished_at": (
                        attempt.finished_at.isoformat() if attempt.finished_at else None
                    ),
                    "metrics": attempt.metrics,
                    "error": attempt.error,
                }
                for attempt in attempts
            ],
            "resources": [],
            "events": [
                {
                    "id": event.id,
                    "event_key": event.event_key,
                    "stage": event.stage,
                    "state": event.state,
                    "message": event.message,
                    "detail": event.detail,
                    "created_at": event.created_at.isoformat() if event.created_at else None,
                }
                for event in events
            ],
            "children": [_job_detail_data(child) for child in children],
        }
    )
    return data


@router.get("/{job_id}/children")
async def get_job_children(job_id: str, db: Annotated[AsyncSession, Depends(get_db)]):
    if await db.get(Job, job_id) is None:
        raise HTTPException(404, "Job not found")
    return {"children": [_job_detail_data(row) for row in await _load_child_jobs(db, job_id)]}


@router.get("/{job_id}/events")
async def job_events(
    job_id: str,
    request: Request,
    last_event_id: Annotated[str | None, Header()] = None,
):
    factory = _get_session_factory()
    async with factory() as db:
        if await db.get(Job, job_id) is None:
            raise HTTPException(404, "Job not found")
    after = int(last_event_id or 0)

    async def events():
        nonlocal after
        start = time.monotonic()
        while True:
            if await request.is_disconnected():
                return
            if time.monotonic() - start > _SSE_TIMEOUT_SECONDS:
                yield 'event: error\ndata: {"message": "stream timeout"}\n\n'
                return
            async with factory() as stream_db:
                rows = list(
                    (
                        await stream_db.scalars(
                            select(JobEvent)
                            .where(JobEvent.job_id == job_id, JobEvent.id > after)
                            .order_by(JobEvent.id)
                        )
                    ).all()
                )
                job = await stream_db.get(Job, job_id)
            for event in rows:
                after = event.id
                payload = {
                    "id": event.id,
                    "job_id": job_id,
                    "event_key": event.event_key,
                    "state": event.state,
                    "stage": event.stage,
                    "message": event.message,
                    "detail": event.detail,
                }
                yield f"id: {event.id}\ndata: {json.dumps(payload, default=str)}\n\n"
            if job is None or job.phase == "terminal":
                status = _display_status(job) if job else "unknown"
                yield f'event: done\ndata: {{"status": "{status}"}}\n\n'
                return
            await asyncio.sleep(0.5)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/{job_id}/cancel", status_code=202)
async def cancel_job(job_id: str, db: Annotated[AsyncSession, Depends(get_db)]):
    job = await db.get(Job, job_id)
    if job is None:
        raise HTTPException(404, "Job not found")
    if job.type != "system_noop":
        raise UnmigratedJobPlatformError(f"{job.type}.cancel")
    try:
        await pgqueuer_gateway.cancel_known_ticket(db, job_id=job_id)
        await db.commit()
    except PgQueuerGatewayError as exc:
        await db.rollback()
        raise HTTPException(409, str(exc)) from exc
    await db.refresh(job)
    return job_summary(job)


class PriorityUpdateRequest(BaseModel):
    priority: int


def _unmigrated_control(operation: str) -> None:
    raise UnmigratedJobPlatformError(operation)


@router.post("/{job_id}/pause", status_code=202)
async def pause_job(job_id: str):
    _unmigrated_control(f"{job_id}.pause")


@router.post("/{job_id}/resume", status_code=202)
async def resume_job(job_id: str):
    _unmigrated_control(f"{job_id}.resume")


@router.patch("/{job_id}/priority")
async def update_job_priority(job_id: str, body: PriorityUpdateRequest):
    _unmigrated_control(f"{job_id}.priority:{body.priority}")


@router.post("/{job_id}/retry", status_code=202)
async def retry_job(job_id: str):
    _unmigrated_control(f"{job_id}.retry")
