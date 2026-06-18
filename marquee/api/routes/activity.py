"""Global activity feed assembled from existing audit/progress tables."""

from __future__ import annotations

import json
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from marquee.database import get_db
from marquee.models import ArtworkEvent, MediaJob, MediaJobEvent, Movie, PipelineRun

router = APIRouter(prefix="/api/activity", tags=["activity"])


def _loads(raw: Any) -> Any:
    if raw is None:
        return None
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return raw
    return raw


def _level(*values: str | None) -> str:
    text = " ".join(v or "" for v in values).lower()
    if any(token in text for token in ("fail", "error", "cancel", "interrupt")):
        return "warn"
    if any(token in text for token in ("succeeded", "completed", "deploy", "restore")):
        return "ok"
    return "info"


def _movie_label(movie: Movie | None, fallback: str = "Movie") -> str:
    if movie is None:
        return fallback
    return f"{movie.title} ({movie.year})" if movie.year else movie.title


@router.get("")
async def activity_feed(
    db: Annotated[AsyncSession, Depends(get_db)],
    limit: int = Query(100, ge=1, le=500),
):
    """Newest activity across poster events, media-job events, and pipeline runs."""
    artwork_rows = (
        await db.execute(
            select(ArtworkEvent, Movie)
            .join(Movie, Movie.id == ArtworkEvent.movie_id)
            .order_by(ArtworkEvent.created_at.desc())
            .limit(limit)
        )
    ).all()
    job_event_rows = (
        await db.execute(
            select(MediaJobEvent, MediaJob)
            .join(MediaJob, MediaJob.job_id == MediaJobEvent.job_id)
            .order_by(MediaJobEvent.created_at.desc())
            .limit(limit)
        )
    ).all()
    run_rows = (
        await db.execute(
            select(PipelineRun, Movie)
            .join(Movie, Movie.id == PipelineRun.movie_id)
            .order_by(PipelineRun.started_at.desc())
            .limit(limit)
        )
    ).all()

    events: list[dict[str, Any]] = []
    for event, movie in artwork_rows:
        label = _movie_label(movie)
        events.append(
            {
                "id": f"artwork:{event.id}",
                "level": _level(event.action),
                "message": f"Poster {event.action.replace('_', ' ')} for {label}",
                "detail": _loads(event.detail),
                "ts": event.created_at.isoformat() if event.created_at else None,
                "source": event.source,
                "entity_type": "artwork_event",
                "entity_id": event.id,
                "movie_id": event.movie_id,
            }
        )

    for event, job in job_event_rows:
        state = event.state or job.status
        events.append(
            {
                "id": f"media-job-event:{event.id}",
                "level": _level(state, event.message),
                "message": event.message or f"{job.operation.replace('_', ' ')} {state}",
                "detail": {
                    "job_id": job.job_id,
                    "operation": job.operation,
                    "stage": event.stage,
                    "state": event.state,
                    "progress": _loads(event.progress_json),
                },
                "ts": event.created_at.isoformat() if event.created_at else None,
                "source": "media_job",
                "entity_type": "media_job_event",
                "entity_id": event.id,
            }
        )

    for run, movie in run_rows:
        label = _movie_label(movie)
        ts = run.completed_at or run.started_at
        events.append(
            {
                "id": f"pipeline-run:{run.run_id}",
                "level": _level(run.status, run.error),
                "message": f"Poster pipeline {run.status} for {label}",
                "detail": {
                    "run_id": run.run_id,
                    "scorer_name": run.scorer_name,
                    "counts": _loads(run.counts_json),
                    "error": run.error,
                    "reviewed": run.feedback_event_id is not None,
                },
                "ts": ts.isoformat() if ts else None,
                "source": "pipeline",
                "entity_type": "pipeline_run",
                "entity_id": run.run_id,
                "movie_id": run.movie_id,
            }
        )

    events.sort(key=lambda item: item["ts"] or "", reverse=True)
    return {"events": events[:limit]}
