"""UI-ready durable job inspection and control API."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from marquee.config import settings
from marquee.core.jobs import job_manager
from marquee.core.jobs.labels import humanize_job_type
from marquee.core.jobs.manager import ACTIVE, TERMINAL
from marquee.database import _get_session_factory, get_db
from marquee.models import (
    Episode,
    EpisodeMediaFile,
    Job,
    JobAttempt,
    JobEvent,
    JobResource,
    JobResourceReservation,
    JobWorker,
    MediaFile,
    MediaJob,
    Movie,
    Series,
)

router = APIRouter(prefix="/api/jobs", tags=["jobs"])
logger = logging.getLogger(__name__)

# SSE stream timeout (1 hour) prevents infinite streams if clients never close.
# Clients can reconnect using Last-Event-ID to resume from where they left off.
_SSE_TIMEOUT_SECONDS = 3600

# "Currently queued" statuses — the complement of manager.ACTIVE/TERMINAL.
# Sourced from the literal status strings manager.py actually assigns (grep,
# not guessed) — api/routes/system.py's _worker_counts() got this wrong by
# inventing statuses ("in_progress", "pending") that are never real values.
_QUEUED_ISH = {"queued", "waiting_resource", "paused", "retry_scheduled"}


async def _resolve_subject_titles(
    db: AsyncSession, jobs: list[Job]
) -> dict[tuple[str | None, str | None], str]:
    """Batch-resolve ``{(subject_type, subject_id): display_title}`` for a page of jobs.

    Movie subjects (``subject_type in {"movie", "radarr_movie"}``) resolve
    directly. ``media_file`` subjects (subtitle / letterbox-reencode jobs)
    resolve through ``MediaFile.movie_id`` when movie-backed, or through
    ``episode_media_files`` -> ``Episode`` -> ``Series`` for a
    ``"Series S01E02"``-style label when episode-backed — there is no direct
    ``series``/``episode`` ``subject_type`` anywhere in this codebase today.
    Anything else (batch/maintenance pseudo-subjects) is left unresolved; the
    frontend falls back to the raw id.
    """
    titles: dict[tuple[str | None, str | None], str] = {}

    movie_ids = {
        int(j.subject_id) for j in jobs if j.subject_type in ("movie", "radarr_movie") and j.subject_id
    }
    if movie_ids:
        rows = (
            await db.execute(select(Movie.id, Movie.title, Movie.year).where(Movie.id.in_(movie_ids)))
        ).all()
        for r in rows:
            label = f"{r.title} ({r.year})" if r.year else r.title
            titles[("movie", str(r.id))] = label
            titles[("radarr_movie", str(r.id))] = label

    media_file_ids = {
        int(j.subject_id) for j in jobs if j.subject_type == "media_file" and j.subject_id
    }
    if media_file_ids:
        mf_rows = (
            await db.execute(
                select(MediaFile.id, MediaFile.movie_id).where(MediaFile.id.in_(media_file_ids))
            )
        ).all()
        movie_backed = {r.id: r.movie_id for r in mf_rows if r.movie_id}
        if movie_backed:
            mv_rows = (
                await db.execute(
                    select(Movie.id, Movie.title, Movie.year).where(Movie.id.in_(movie_backed.values()))
                )
            ).all()
            mv_by_id = {r.id: (r.title, r.year) for r in mv_rows}
            for mf_id, mv_id in movie_backed.items():
                title, year = mv_by_id.get(mv_id, (None, None))
                if title:
                    titles[("media_file", str(mf_id))] = f"{title} ({year})" if year else title

        episode_backed_ids = [r.id for r in mf_rows if not r.movie_id]
        if episode_backed_ids:
            ep_rows = (
                await db.execute(
                    select(
                        EpisodeMediaFile.media_file_id,
                        Episode.series_id,
                        Episode.season_number,
                        Episode.episode_number,
                    )
                    .join(Episode, Episode.id == EpisodeMediaFile.episode_id)
                    .where(EpisodeMediaFile.media_file_id.in_(episode_backed_ids))
                )
            ).all()
            series_ids = {r.series_id for r in ep_rows}
            series_rows = (
                (await db.execute(select(Series.id, Series.title).where(Series.id.in_(series_ids))))
                .all()
                if series_ids
                else []
            )
            series_by_id = {r.id: r.title for r in series_rows}
            for r in ep_rows:
                series_title = series_by_id.get(r.series_id)
                if series_title:
                    titles[("media_file", str(r.media_file_id))] = (
                        f"{series_title} S{r.season_number:02d}E{r.episode_number:02d}"
                    )

    return titles


def job_summary(job: Job, *, subject_title: str | None = None) -> dict:
    return {
        "job_id": job.id,
        "type": job.type,
        "label": humanize_job_type(job.type),
        "status": job.status,
        "priority": job.priority,
        "parent_id": job.parent_id,
        "root_id": job.root_id,
        "correlation_id": job.correlation_id,
        "subject": (
            {"type": job.subject_type, "id": job.subject_id, "title": subject_title}
            if job.subject_type
            else None
        ),
        "stage": job.current_stage,
        "progress": job.progress,
        "resource_request": job.resource_request,
        "attempt_count": job.attempt_count,
        "max_attempts": job.max_attempts,
        "cancel_requested": job.cancel_requested,
        "pause_requested": job.pause_requested,
        "scheduled_at": job.scheduled_at.isoformat() if job.scheduled_at else None,
        "claimed_at": job.claimed_at.isoformat() if job.claimed_at else None,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "finished_at": job.finished_at.isoformat() if job.finished_at else None,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "status_url": f"/api/jobs/{job.id}",
        "events_url": f"/api/jobs/{job.id}/events",
    }


def _decode_media_blob(value):
    if value is None:
        return None
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def _linked_media_job_id(job: Job) -> str | None:
    if not isinstance(job.payload, dict):
        return None
    media_job_id = job.payload.get("media_job_id")
    return str(media_job_id) if media_job_id else None


def _job_detail_data(job: Job, *, subject_title: str | None, media_job: MediaJob | None) -> dict:
    request = _decode_media_blob(media_job.request_json) if media_job is not None else None
    plan = _decode_media_blob(media_job.plan_json) if media_job is not None else None
    media_result = _decode_media_blob(media_job.result_json) if media_job is not None else None
    media_error = _decode_media_blob(media_job.error_json) if media_job is not None else None
    data = job_summary(job, subject_title=subject_title)
    data.update(
        {
            "payload": job.payload,
            "checkpoint": job.checkpoint,
            "request": request,
            "plan": plan,
            "result": media_result if media_result is not None else job.result,
            "error": media_error if media_error is not None else job.error,
            "media_job_id": media_job.job_id if media_job is not None else None,
        }
    )
    return data


async def _load_linked_media_jobs(db: AsyncSession, jobs: list[Job]) -> dict[str, MediaJob]:
    media_job_ids = list(
        {
            media_job_id
            for media_job_id in (_linked_media_job_id(job) for job in jobs)
            if media_job_id
        }
    )
    if not media_job_ids:
        return {}
    rows = (
        await db.execute(select(MediaJob).where(MediaJob.job_id.in_(media_job_ids)))
    ).scalars().all()
    return {row.job_id: row for row in rows}


async def _load_child_jobs(db: AsyncSession, parent_id: str) -> list[Job]:
    return (
        (
            await db.execute(
                select(Job)
                .where(Job.parent_id == parent_id)
                .order_by(Job.created_at.asc(), Job.id.asc())
            )
        )
        .scalars()
        .all()
    )


async def _serialize_job_details(db: AsyncSession, jobs: list[Job]) -> list[dict]:
    if not jobs:
        return []
    titles = await _resolve_subject_titles(db, jobs)
    media_jobs = await _load_linked_media_jobs(db, jobs)
    return [
        _job_detail_data(
            job,
            subject_title=titles.get((job.subject_type, job.subject_id)),
            media_job=media_jobs.get(_linked_media_job_id(job) or ""),
        )
        for job in jobs
    ]


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
    """``active=true`` / ``queued_only=true`` are shorthands over manager's
    real ``ACTIVE``/queued-ish status sets — "currently running" or
    "currently queued" isn't one status string, and inventing ad-hoc status
    literals at the call site is exactly the mistake that made
    ``system.py``'s worker counts silently wrong."""
    query = select(Job).order_by(Job.created_at.desc(), Job.id.desc()).limit(limit + 1)
    if status:
        query = query.where(Job.status == status)
    if active:
        query = query.where(Job.status.in_(ACTIVE))
    if queued_only:
        query = query.where(Job.status.in_(_QUEUED_ISH))
    if type:
        query = query.where(Job.type == type)
    if subject_type:
        query = query.where(Job.subject_type == subject_type)
    if subject_id:
        query = query.where(Job.subject_id == subject_id)
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
    rows = (await db.execute(query)).scalars().all()
    has_more = len(rows) > limit
    rows = rows[:limit]
    next_before = (
        int(rows[-1].created_at.timestamp()) if has_more and rows and rows[-1].created_at else None
    )
    titles = await _resolve_subject_titles(db, rows)
    return {
        "jobs": [
            job_summary(row, subject_title=titles.get((row.subject_type, row.subject_id)))
            for row in rows
        ],
        "next_before": next_before,
    }


@router.get("/metrics")
async def job_metrics(db: Annotated[AsyncSession, Depends(get_db)]):
    status_counts = dict(
        (await db.execute(select(Job.status, func.count()).group_by(Job.status))).all()
    )
    resources = (await db.execute(select(JobResource))).scalars().all()
    active = (
        await db.execute(
            select(
                JobResourceReservation.resource_key,
                func.coalesce(func.sum(JobResourceReservation.units), 0),
            )
            .where(JobResourceReservation.released_at.is_(None))
            .group_by(JobResourceReservation.resource_key)
        )
    ).all()
    in_use = dict(active)

    logical_pools = {
        "gpu",
        "media_read",
        "media_write",
        "transcode",
        "network_external",
        "maintenance_exclusive",
    }
    filtered_resources = []
    for row in resources:
        used = int(in_use.get(row.key, 0))
        if row.key not in logical_pools and not (row.key.startswith("media-file:") and used > 0):
            continue
        filtered_resources.append(
            {
                "key": row.key,
                "capacity": row.capacity,
                "in_use": used,
                "enabled": row.enabled,
            }
        )

    worker_cutoff = datetime.now(UTC) - timedelta(seconds=settings.JOB_LEASE_SECONDS)
    workers = (
        (
            await db.execute(
                select(JobWorker)
                .where(
                    JobWorker.status.in_(("starting", "running", "draining")),
                    JobWorker.heartbeat_at >= worker_cutoff,
                )
                .order_by(JobWorker.heartbeat_at.desc())
            )
        )
        .scalars()
        .all()
    )
    return {
        "counts": status_counts,
        "resources": filtered_resources,
        "workers": [
            {"id": row.id, "status": row.status, "heartbeat_at": row.heartbeat_at.isoformat()}
            for row in workers
        ],
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
    """Success rate + duration percentiles per job type.

    Computed over each type's most recent ``limit_per_type`` terminal jobs —
    a bounded, in-Python percentile (mirroring ``pipeline.py``'s
    ``pipeline_metrics()``) rather than a DB-side percentile function, for
    portability. One query per distinct job type (~25 registered types);
    acceptable for a low-traffic admin page.
    """
    types = (await db.execute(select(Job.type).distinct())).scalars().all()
    out: dict[str, dict] = {}
    for job_type in types:
        rows = (
            await db.execute(
                select(Job.status, Job.started_at, Job.finished_at)
                .where(Job.type == job_type, Job.status.in_(TERMINAL))
                .order_by(Job.finished_at.desc())
                .limit(limit_per_type)
            )
        ).all()
        if not rows:
            continue
        durations = sorted(
            (r.finished_at - r.started_at).total_seconds()
            for r in rows
            if r.started_at and r.finished_at
        )
        succeeded = sum(1 for r in rows if r.status == "succeeded")
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
    attempts = (
        (
            await db.execute(
                select(JobAttempt).where(JobAttempt.job_id == job_id).order_by(JobAttempt.number)
            )
        )
        .scalars()
        .all()
    )
    reservations = (
        (
            await db.execute(
                select(JobResourceReservation).where(JobResourceReservation.job_id == job_id)
            )
        )
        .scalars()
        .all()
    )
    # The SSE endpoint (/{job_id}/events) only streams live updates — a
    # terminal job's full audit trail has nowhere else to come from, so the
    # detail view includes it directly, same as attempts/resources below.
    events = (
        (
            await db.execute(
                select(JobEvent).where(JobEvent.job_id == job_id).order_by(JobEvent.id)
            )
        )
        .scalars()
        .all()
    )
    media_jobs = await _load_linked_media_jobs(db, [job])
    children = await _load_child_jobs(db, job_id)
    child_details = await _serialize_job_details(db, children)
    titles = await _resolve_subject_titles(db, [job])
    data = _job_detail_data(
        job,
        subject_title=titles.get((job.subject_type, job.subject_id)),
        media_job=media_jobs.get(_linked_media_job_id(job) or ""),
    )
    data.update(
        {
            "attempts": [
                {
                    "number": a.number,
                    "status": a.status,
                    "worker_id": a.worker_id,
                    "started_at": a.started_at.isoformat() if a.started_at else None,
                    "finished_at": a.finished_at.isoformat() if a.finished_at else None,
                    "metrics": a.metrics,
                    "error": a.error,
                }
                for a in attempts
            ],
            "resources": [
                {
                    "key": r.resource_key,
                    "units": r.units,
                    "stage": r.stage,
                    "acquired_at": r.acquired_at.isoformat() if r.acquired_at else None,
                    "released_at": r.released_at.isoformat() if r.released_at else None,
                }
                for r in reservations
            ],
            "events": [
                {
                    "id": e.id,
                    "stage": e.stage,
                    "state": e.state,
                    "message": e.message,
                    "detail": e.detail,
                    "created_at": e.created_at.isoformat() if e.created_at else None,
                }
                for e in events
            ],
            "children": child_details,
        }
    )
    return data


@router.get("/{job_id}/children")
async def get_job_children(job_id: str, db: Annotated[AsyncSession, Depends(get_db)]):
    job = await db.get(Job, job_id)
    if job is None:
        raise HTTPException(404, "Job not found")
    children = await _load_child_jobs(db, job_id)
    return {"children": await _serialize_job_details(db, children)}


@router.get("/{job_id}/events")
async def job_events(
    job_id: str,
    request: Request,
    last_event_id: Annotated[str | None, Header()] = None,
):
    """Stream job events over SSE with disconnect detection and timeout.

    Clients reconnecting after disconnect should send the last ``id`` they
    received as the ``Last-Event-ID`` header — the stream replays all events
    after that ID and continues live.

    The stream terminates when:
      - The job reaches a terminal state (succeeded/failed/cancelled/...)
      - The client disconnects (browser tab closed, network interruption)
      - The stream exceeds 1 hour (timeout — client should reconnect)
    """
    factory = _get_session_factory()
    async with factory() as db:
        if await db.get(Job, job_id) is None:
            raise HTTPException(404, "Job not found")
    after = int(last_event_id or 0)
    factory = _get_session_factory()

    async def events():
        nonlocal after
        start = time.monotonic()
        while True:
            # Check client disconnect (browser tab closed, network drop)
            if await request.is_disconnected():
                logger.info("SSE client disconnected for job %s (after event_id=%d)", job_id, after)
                return

            # Enforce maximum stream duration (prevents infinite streams)
            elapsed = time.monotonic() - start
            if elapsed > _SSE_TIMEOUT_SECONDS:
                logger.warning(
                    "SSE stream timeout for job %s after %.0fs (client should reconnect)",
                    job_id,
                    elapsed,
                )
                yield 'event: error\ndata: {"message": "stream timeout — reconnect with Last-Event-ID"}\n\n'
                return

            async with factory() as stream_db:
                rows = (
                    (
                        await stream_db.execute(
                            select(JobEvent)
                            .where(JobEvent.job_id == job_id, JobEvent.id > after)
                            .order_by(JobEvent.id)
                        )
                    )
                    .scalars()
                    .all()
                )
                job = await stream_db.get(Job, job_id)

            for event in rows:
                after = event.id
                yield f"id: {event.id}\ndata: {json.dumps({'id': event.id, 'job_id': job_id, 'state': event.state, 'stage': event.stage, 'message': event.message, 'detail': event.detail}, default=str)}\n\n"

            if job is None or job.status in {
                "succeeded",
                "failed",
                "cancelled",
                "interrupted",
                "dead_letter",
            }:
                # Include final status so frontend can show appropriate UI
                yield f'event: done\ndata: {{"status": "{job.status if job else "unknown"}"}}\n\n'
                logger.debug(
                    "SSE stream complete for job %s (status=%s)",
                    job_id,
                    job.status if job else None,
                )
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
    return job_summary(await job_manager.request_cancel(db, job))


@router.post("/{job_id}/pause", status_code=202)
async def pause_job(job_id: str, db: Annotated[AsyncSession, Depends(get_db)]):
    job = await db.get(Job, job_id)
    if job is None:
        raise HTTPException(404, "Job not found")
    return job_summary(await job_manager.set_paused(db, job, True))


@router.post("/{job_id}/resume", status_code=202)
async def resume_job(job_id: str, db: Annotated[AsyncSession, Depends(get_db)]):
    job = await db.get(Job, job_id)
    if job is None:
        raise HTTPException(404, "Job not found")
    return job_summary(await job_manager.set_paused(db, job, False))


@router.post("/{job_id}/retry", status_code=202)
async def retry_job(job_id: str, db: Annotated[AsyncSession, Depends(get_db)]):
    job = await db.get(Job, job_id)
    if job is None:
        raise HTTPException(404, "Job not found")
    if job.status not in {"failed", "interrupted", "cancelled", "dead_letter"}:
        raise HTTPException(409, f"Cannot retry a {job.status} job")
    retry = await job_manager.create(
        db,
        job_type=job.type,
        payload=job.payload,
        priority=job.priority,
        resources=job.resource_request,
        parent_id=job.parent_id,
        correlation_id=job.correlation_id,
        subject_type=job.subject_type,
        subject_id=job.subject_id,
        max_attempts=job.max_attempts,
    )
    return job_summary(retry)
