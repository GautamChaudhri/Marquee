"""Pipeline routes — trigger runs, stream progress, serve results + posters."""

from __future__ import annotations

import json
import logging
import os
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import BaseModel
from sqlalchemy import exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from marquee.api.deps import enforce_rate_limit, get_rate_limiter
from marquee.api.library_serializers import enrich_movie
from marquee.api.results import (
    build_results_payload,
    feature_vector_from_archive,
    find_candidate,
    poster_url,
)
from marquee.api.routes.jobs import job_summary
from marquee.api.routes.library import _coverage_by_media_file
from marquee.config import settings
from marquee.core.heal import latest_heal_summary
from marquee.core.jobs import job_manager
from marquee.core.jobs.manager import ACTIVE
from marquee.core.pipeline_config import PipelineSettings, pipeline_settings
from marquee.core.rate_limit import RateLimiter
from marquee.database import get_db
from marquee.models import (
    ArtworkEvent,
    Job,
    JobSchedule,
    LetterboxState,
    MediaFile,
    Movie,
    PipelineRun,
)
from marquee.pipeline.gate import PosterGate
from marquee.pipeline.run_manager import run_manager
from marquee.pipeline.scorer import WeightedScorer

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/pipeline", tags=["pipeline"])
movies_router = APIRouter(prefix="/api/movies", tags=["movies"])

_REVIEW_QUEUE_STATUSES = {"completed", "flagged_manual"}


def _downloaded():
    """Movie has a file on disk. Mirrors the library list's availability filter
    (``api/routes/library.py``) so the pipeline ignores undownloaded Radarr
    movies entirely — exactly like the films list and letterbox do."""
    return or_(
        Movie.movie_file_path.is_not(None),
        exists(
            select(MediaFile.id).where(
                MediaFile.movie_id == Movie.id,
                MediaFile.is_active.is_(True),
            )
        ),
    )


def _review_queue_latest():
    return (
        select(
            PipelineRun.movie_id.label("movie_id"),
            func.max(PipelineRun.started_at).label("started_at"),
        )
        .join(Movie, Movie.id == PipelineRun.movie_id)
        .where(
            PipelineRun.feedback_event_id.is_(None),
            PipelineRun.status.in_(_REVIEW_QUEUE_STATUSES),
            _downloaded(),
        )
        .group_by(PipelineRun.movie_id)
        .subquery()
    )


def _backup_stats() -> dict[str, int]:
    count = total_bytes = 0
    base = settings.poster_backup_path
    if base.is_dir():
        for entry in os.scandir(base):
            if entry.is_file() and entry.name.endswith(".jpg"):
                count += 1
                total_bytes += entry.stat().st_size
    return {"count": count, "bytes": total_bytes}


class MaintenanceRequest(BaseModel):
    dry_run: bool = False
    force: bool = False


# ---------------------------------------------------------------------------
# Run lifecycle
# ---------------------------------------------------------------------------


@router.post("/movie/{movie_id}/run", status_code=202)
async def run_pipeline(
    movie_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    limiter: Annotated[RateLimiter, Depends(get_rate_limiter)],
):
    """Start a pipeline run for a movie. 202 + run_id, or 409 if one is active."""
    movie = (await db.execute(select(Movie).where(Movie.id == movie_id))).scalar_one_or_none()
    if movie is None:
        raise HTTPException(status_code=404, detail=f"Movie id={movie_id} not found")
    if movie.tmdb_id is None:
        raise HTTPException(
            status_code=400, detail=f"Movie {movie.title!r} has no TMDB ID — run sync"
        )
    if not await db.scalar(
        select(exists(select(Movie.id).where(Movie.id == movie_id, _downloaded())))
    ):
        raise HTTPException(status_code=400, detail=f"Movie {movie.title!r} has no downloaded file")

    enforce_rate_limit(limiter, f"pipeline:{movie_id}", settings.RATE_PIPELINE_RUN_SECONDS)
    limiter.record(f"pipeline:{movie_id}")
    job = await job_manager.create(
        db,
        job_type="poster_pipeline",
        payload={"movie_id": movie.id},
        priority=90,
        resources={"gpu": 1, "network_external": 1},
        subject_type="movie",
        subject_id=movie.id,
        idempotency_key=f"poster-pipeline:{movie.id}:{int(__import__('time').time() // settings.RATE_PIPELINE_RUN_SECONDS)}",
    )
    response = job_summary(job)
    response["run_id"] = job.id
    response["results_url"] = f"/api/pipeline/runs/{job.id}"
    return response


@router.get("/runs/{run_id}/events")
async def stream_events(run_id: str):
    """Compatibility redirect to the worker-independent durable event stream."""
    return RedirectResponse(url=f"/api/jobs/{run_id}/events", status_code=307)


async def _load_run(db: AsyncSession, run_id: str) -> PipelineRun:
    run = (
        await db.execute(select(PipelineRun).where(PipelineRun.run_id == run_id))
    ).scalar_one_or_none()
    if run is None:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")
    return run


@router.get("/runs/{run_id}")
async def get_run_results(
    run_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Full results payload for a run (auto-pick, ranked, rejected-by-stage)."""
    run = await _load_run(db, run_id)
    if run.status == "running":
        return {
            "run_id": run_id,
            "status": "running",
            "events_url": f"/api/pipeline/runs/{run_id}/events",
        }

    archive = run_manager.load_archive(run_id, run.archive_path)
    if archive is None:
        raise HTTPException(
            status_code=404, detail=f"Run {run_id} archive is missing or unreadable"
        )
    return build_results_payload(
        archive,
        run_id=run_id,
        status=run.status,
        reviewed=run.feedback_event_id is not None,
        scorer=run.scorer_name,
    )


@router.get("/runs/{run_id}/posters/{orig_filename}")
async def get_run_poster(
    run_id: str,
    orig_filename: str,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Serve a candidate's image file from inside the run's working dir."""
    run = await _load_run(db, run_id)
    archive = run_manager.load_archive(run_id, run.archive_path)
    if archive is None:
        raise HTTPException(status_code=404, detail="Run archive unavailable")

    candidate = find_candidate(archive, orig_filename)
    if candidate is None:
        raise HTTPException(
            status_code=404, detail=f"No candidate {orig_filename!r} in run {run_id}"
        )

    image_path = Path(candidate["image_path"]).resolve()
    # Confine served files to the live run tree or the legacy experiments
    # trees — never accept the filename as a path; always resolve from the
    # recorded record.
    legacy_roots = [
        settings.runs_work_path,
        Path(__file__).resolve().parents[2] / "experiments" / "runs",
        Path(__file__).resolve().parents[1] / "experiments" / "runs",
    ]
    if not any(str(image_path).startswith(str(root.resolve())) for root in legacy_roots):
        raise HTTPException(status_code=403, detail="Poster path outside run tree")
    if not image_path.is_file():
        raise HTTPException(status_code=404, detail="Poster file no longer on disk")
    return FileResponse(image_path)


class RescoreRequest(BaseModel):
    # Feature-name -> weight (e.g. {"knn_sim": 0.4, "face_area": 0.0}).
    weights: dict[str, float] | None = None
    # Gate knob -> floor (e.g. {"GATE_MIN_AESTHETIC": 4.0}).
    gates: dict[str, float] | None = None


def _clone_config(
    weights: dict[str, float] | None, gates: dict[str, float] | None
) -> PipelineSettings:
    """A throwaway PipelineSettings with weight/gate overrides applied."""
    merged = pipeline_settings.model_dump()
    for feature_name, value in (weights or {}).items():
        merged[f"WEIGHT_{feature_name.upper()}"] = value
    for knob, value in (gates or {}).items():
        merged[knob] = value
    return PipelineSettings(**merged)


@router.post("/runs/{run_id}/rescore")
async def rescore_run(
    run_id: str,
    body: RescoreRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Re-rank an archived run's candidates under new weights/gate floors.

    Pure arithmetic over the archived feature vectors — no images, no
    inference. This is the engine behind live knob sliders in the UI.
    """
    run = await _load_run(db, run_id)
    archive = run_manager.load_archive(run_id, run.archive_path)
    if archive is None:
        raise HTTPException(status_code=404, detail="Run archive unavailable")

    try:
        config = _clone_config(body.weights, body.gates)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"Invalid rescore params: {exc}") from exc

    gate = PosterGate(config)
    scorer = WeightedScorer(config)

    reranked: list[dict] = []
    gated_out: list[dict] = []
    for candidate in archive.get("candidates", []):
        if candidate.get("rank") is None:
            continue  # only previously-ranked candidates have full features
        features = feature_vector_from_archive(candidate)
        if features is None:
            continue
        # Re-apply the style + detail floors with the new gate values.
        style = gate.evaluate_style(features)
        detail = gate.evaluate_detail(features) if style.passed else style
        if not (style.passed and detail.passed):
            gated_out.append(
                {
                    "orig_filename": candidate["orig_filename"],
                    "gate_reason": (style if not style.passed else detail).reason,
                }
            )
            continue
        score, contributions = scorer.score(features)
        reranked.append(
            {
                "orig_filename": candidate["orig_filename"],
                "previous_rank": candidate.get("rank"),
                "final_score": score,
                "contributions": contributions,
                "poster_url": poster_url(run_id, candidate["orig_filename"]),
            }
        )

    reranked.sort(key=lambda c: c["final_score"], reverse=True)
    for new_rank, entry in enumerate(reranked, 1):
        entry["rank"] = new_rank

    return {
        "run_id": run_id,
        "weights": body.weights or {},
        "gates": body.gates or {},
        "ranked": reranked,
        "gated_out": gated_out,
    }


# ---------------------------------------------------------------------------
# Batch runs (cross-movie, stage-batched)
# ---------------------------------------------------------------------------


class BatchRunRequest(BaseModel):
    # "missing" (movies with no poster yet) | "all" | "selected" (movie_ids).
    scope: str = "missing"
    movie_ids: list[int] | None = None


@router.post("/batch", status_code=202)
async def run_pipeline_batch(
    body: BatchRunRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Enqueue one stage-batched run over many movies (OCR/DINO load once)."""
    scope = body.scope
    if scope == "selected":
        if not body.movie_ids:
            raise HTTPException(status_code=400, detail="scope=selected requires movie_ids")
        query = select(Movie.id).where(
            Movie.id.in_(body.movie_ids), Movie.tmdb_id.is_not(None), _downloaded()
        )
    elif scope == "missing":
        query = select(Movie.id).where(
            Movie.poster_path.is_(None), Movie.tmdb_id.is_not(None), _downloaded()
        )
    elif scope == "all":
        query = select(Movie.id).where(Movie.tmdb_id.is_not(None), _downloaded())
    else:
        raise HTTPException(status_code=400, detail=f"unknown scope {scope!r}")

    movie_ids = list((await db.execute(query.order_by(Movie.id))).scalars().all())
    if not movie_ids:
        raise HTTPException(status_code=404, detail=f"no eligible movies for scope={scope!r}")
    cap = pipeline_settings.PIPELINE_BATCH_MAX_MOVIES
    if len(movie_ids) > cap:
        raise HTTPException(
            status_code=400,
            detail=f"batch of {len(movie_ids)} movies exceeds PIPELINE_BATCH_MAX_MOVIES={cap}",
        )
    job = await job_manager.create(
        db,
        job_type="poster_pipeline_batch",
        payload={"movie_ids": movie_ids, "scope": scope},
        priority=80,
        resources={"gpu": 1, "network_external": 1},
        subject_type="pipeline_batch",
        subject_id=scope,
        max_attempts=1,
        idempotency_key=f"poster-batch:{scope}:{int(time.time() // 30)}",
    )
    response = job_summary(job)
    response["movie_count"] = len(movie_ids)
    return response


@router.get("/summary")
async def pipeline_summary(db: Annotated[AsyncSession, Depends(get_db)]):
    downloaded = _downloaded()
    total_movies = await db.scalar(select(func.count(Movie.id)).where(downloaded))
    movies_with_poster = await db.scalar(
        select(func.count(Movie.id)).where(downloaded, Movie.poster_path.is_not(None))
    )
    latest = _review_queue_latest()
    movies_in_review = await db.scalar(select(func.count()).select_from(latest))
    movies_in_run = await db.scalar(
        select(func.count(func.distinct(PipelineRun.movie_id)))
        .join(Movie, Movie.id == PipelineRun.movie_id)
        .where(PipelineRun.status == "running", downloaded)
    )

    active_jobs = (
        (
            await db.execute(
                select(Job)
                .where(Job.type.in_(("poster_pipeline", "poster_pipeline_batch")), Job.status.in_(ACTIVE))
                .order_by(Job.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    running_jobs = []
    for job in active_jobs:
        summary = job_summary(job)
        payload = job.payload if isinstance(job.payload, dict) else {}
        if job.type == "poster_pipeline_batch":
            summary["movie_count"] = len(payload.get("movie_ids") or [])
        elif job.subject_type == "movie" or payload.get("movie_id") is not None:
            summary["movie_count"] = 1
        else:
            summary["movie_count"] = 0
        running_jobs.append(summary)

    schedule = await db.get(JobSchedule, "poster-heal")
    heal_schedule = (
        {
            "enabled": schedule.enabled,
            "interval_minutes": schedule.interval_seconds // 60,
            "next_run_at": schedule.next_run_at.isoformat() if schedule.next_run_at else None,
        }
        if schedule
        else None
    )

    total = total_movies or 0
    with_poster = movies_with_poster or 0
    return {
        "total_movies": total,
        "movies_with_poster": with_poster,
        "movies_missing_poster": max(total - with_poster, 0),
        "movies_in_review": movies_in_review or 0,
        "movies_in_run": movies_in_run or 0,
        "running_jobs": running_jobs,
        "last_heal": await latest_heal_summary(db),
        "heal_schedule": heal_schedule,
        "backups": _backup_stats(),
    }


@router.post("/rescan-posters", status_code=202)
async def rescan_posters(db: Annotated[AsyncSession, Depends(get_db)]):
    job = await job_manager.create(
        db,
        job_type="poster_rescan",
        payload={},
        priority=35,
        resources={"media_read": 1},
        subject_type="maintenance",
        subject_id="poster-rescan",
        max_attempts=1,
        idempotency_key=f"poster-rescan:{int(time.time() // 30)}",
    )
    return job_summary(job)


@router.post("/backup-all", status_code=202)
async def backup_all_posters(db: Annotated[AsyncSession, Depends(get_db)]):
    job = await job_manager.create(
        db,
        job_type="poster_backup_all",
        payload={},
        priority=35,
        resources={"media_read": 1},
        subject_type="maintenance",
        subject_id="poster-backup-all",
        max_attempts=1,
        idempotency_key=f"poster-backup-all:{int(time.time() // 30)}",
    )
    return job_summary(job)


@router.post("/maintenance", status_code=202)
async def poster_maintenance(
    body: MaintenanceRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    job = await job_manager.create(
        db,
        job_type="poster_maintenance",
        payload=body.model_dump(),
        priority=30,
        resources={"network_external": 1, "maintenance_exclusive": 1},
        subject_type="maintenance",
        subject_id="poster-maintenance",
        max_attempts=1,
        idempotency_key=(
            f"poster-maintenance:{body.dry_run}:{body.force}:{int(time.time() // 30)}"
        ),
    )
    return job_summary(job)


# ---------------------------------------------------------------------------
# Pipeline cache (downloaded posters + working artifacts — never head/taste data)
# ---------------------------------------------------------------------------


@router.get("/cache")
async def get_pipeline_cache():
    """On-disk size of each poster-pipeline cache (for the Clear button)."""
    from marquee.core.pipeline_cache import cache_sizes  # noqa: PLC0415

    return cache_sizes()


class CacheClearRequest(BaseModel):
    include_embeddings: bool = True
    include_archives: bool = False


@router.post("/cache/clear")
async def clear_pipeline_cache(
    body: CacheClearRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Clear downloaded-poster pipeline caches. Never touches head/taste data."""
    job = await job_manager.create_and_run(
        db,
        job_type="pipeline_cache_clear",
        payload={
            "include_embeddings": body.include_embeddings,
            "include_archives": body.include_archives,
        },
        priority=40,
        subject_type="pipeline_cache",
        subject_id="default",
        max_attempts=1,
        worker_id="inline-api",
    )
    return {**job_summary(job), **(job.result or {})}


@router.post("/posters/reset")
async def reset_deployed_posters(
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Delete every deployed poster and reset movies to missing.

    Enqueues a ``poster_deploy_reset`` durable job that walks all movies
    with a deployed poster, deletes the poster file from the media folder
    (keeping the ``data/cache/posters`` copies as restore fallbacks), and
    resets all ``poster_*`` columns so the movies reappear in the Run tab.
    """
    job = await job_manager.create_and_run(
        db,
        job_type="poster_deploy_reset",
        payload={},
        priority=30,
        subject_type="pipeline_posters",
        subject_id="deploy_reset",
        max_attempts=1,
        worker_id="inline-api",
    )
    return {**job_summary(job), **(job.result or {})}


# ---------------------------------------------------------------------------
# Aggregate metrics (cheap — from PipelineRun rows, no archive reads)
# ---------------------------------------------------------------------------


@router.get("/metrics")
async def pipeline_metrics(
    db: Annotated[AsyncSession, Depends(get_db)],
    limit: int = 500,
):
    """Cross-run aggregates for a metrics dashboard, from recent PipelineRun rows."""
    limit = min(max(limit, 1), 5000)
    runs = (
        (await db.execute(select(PipelineRun).order_by(PipelineRun.started_at.desc()).limit(limit)))
        .scalars()
        .all()
    )

    by_status: Counter[str] = Counter()
    by_scorer: Counter[str] = Counter()
    count_totals: dict[str, float] = defaultdict(float)
    count_n: dict[str, int] = defaultdict(int)
    stage_totals: dict[str, float] = defaultdict(float)
    stage_n: dict[str, int] = defaultdict(int)
    durations: list[float] = []
    batches: set[str] = set()

    for run in runs:
        by_status[run.status] += 1
        if run.scorer_name:
            by_scorer[run.scorer_name] += 1
        if run.batch_id:
            batches.add(run.batch_id)
        if run.duration_seconds is not None:
            durations.append(run.duration_seconds)
        for key, value in (json.loads(run.counts_json) if run.counts_json else {}).items():
            if isinstance(value, (int, float)):
                count_totals[key] += value
                count_n[key] += 1
        for key, value in (json.loads(run.timings_json) if run.timings_json else {}).items():
            if isinstance(value, (int, float)):
                stage_totals[key] += value
                stage_n[key] += 1

    durations.sort()

    def _pct(p: float) -> float | None:
        if not durations:
            return None
        return round(durations[min(len(durations) - 1, int(len(durations) * p))], 3)

    return {
        "window_runs": len(runs),
        "by_status": dict(by_status),
        "by_scorer": dict(by_scorer),
        "distinct_batches": len(batches),
        "duration_seconds": {
            "avg": round(sum(durations) / len(durations), 3) if durations else None,
            "p50": _pct(0.5),
            "p90": _pct(0.9),
            "max": round(max(durations), 3) if durations else None,
        },
        "avg_counts": {
            k: round(count_totals[k] / count_n[k], 2) for k in count_totals if count_n[k]
        },
        "total_counts": {k: round(v, 2) for k, v in count_totals.items()},
        "avg_stage_seconds": {
            k: round(stage_totals[k] / stage_n[k], 3) for k in stage_totals if stage_n[k]
        },
        "total_stage_seconds": {k: round(v, 3) for k, v in stage_totals.items()},
    }


# ---------------------------------------------------------------------------
# Run history (per movie)
# ---------------------------------------------------------------------------


@router.get("/review-queue")
async def review_queue(
    db: Annotated[AsyncSession, Depends(get_db)],
    page: int = 1,
    page_size: int = 50,
):
    """Latest unreviewed poster-pipeline run per movie for the review page."""
    page = max(page, 1)
    page_size = min(max(page_size, 1), 200)

    latest = _review_queue_latest()
    base = (
        select(PipelineRun, Movie, LetterboxState)
        .join(
            latest,
            (latest.c.movie_id == PipelineRun.movie_id)
            & (latest.c.started_at == PipelineRun.started_at),
        )
        .join(Movie, Movie.id == PipelineRun.movie_id)
        .outerjoin(LetterboxState, LetterboxState.movie_id == Movie.id)
        .where(
            PipelineRun.feedback_event_id.is_(None),
            PipelineRun.status.in_(_REVIEW_QUEUE_STATUSES),
        )
    )
    total = (await db.execute(select(func.count()).select_from(latest))).scalar_one()
    rows = (
        await db.execute(
            base.order_by(PipelineRun.started_at.desc())
            .limit(page_size)
            .offset((page - 1) * page_size)
        )
    ).all()

    movies = [movie for _, movie, _ in rows]
    movie_ids = [movie.id for movie in movies]
    media_rows = (
        (
            await db.execute(
                select(MediaFile).where(
                    MediaFile.movie_id.in_(movie_ids),
                    MediaFile.is_active.is_(True),
                )
            )
        )
        .scalars()
        .all()
        if movie_ids
        else []
    )
    mf_by_movie = {mf.movie_id: mf for mf in media_rows}
    coverage = await _coverage_by_media_file(db, [mf.id for mf in media_rows])

    items = []
    for run, movie, lb in rows:
        mf = mf_by_movie.get(movie.id)
        items.append(
            {
                "movie": enrich_movie(
                    movie,
                    mf,
                    coverage.get(mf.id) if mf else None,
                    lb.status if lb else None,
                ),
                "run": {
                    "run_id": run.run_id,
                    "status": run.status,
                    "started_at": run.started_at.isoformat() if run.started_at else None,
                    "completed_at": run.completed_at.isoformat() if run.completed_at else None,
                    "scorer_name": run.scorer_name,
                    "counts": json.loads(run.counts_json) if run.counts_json else None,
                    "reviewed": False,
                },
                "auto_pick_poster_url": (
                    poster_url(run.run_id, run.auto_pick_filename)
                    if run.auto_pick_filename
                    else None
                ),
                "results_url": f"/api/pipeline/runs/{run.run_id}",
            }
        )

    return {"total": total, "page": page, "page_size": page_size, "items": items}


@router.post("/review/reset", status_code=200)
async def reset_review_queue(
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Reset all movies in the review queue back to the run stage.

    Clears poster DB state AND marks review-queue PipelineRuns so they
    disappear from the Review tab. Does NOT delete poster files from disk.
    """
    # Find all PipelineRuns currently in the review queue.
    result = await db.execute(
        select(PipelineRun).where(
            PipelineRun.feedback_event_id.is_(None),
            PipelineRun.status.in_(_REVIEW_QUEUE_STATUSES),
        )
    )
    review_runs = result.scalars().all()

    if not review_runs:
        return {"reset": 0}

    # Collect unique movie IDs and mark their PipelineRuns as reviewed.
    movie_ids = list({run.movie_id for run in review_runs})
    reset_key = f"review_reset_{int(time.time())}"

    for run in review_runs:
        run.feedback_event_id = reset_key
        db.add(
            ArtworkEvent(
                movie_id=run.movie_id,
                action="review_reset",
                source="manual",
                detail=json.dumps({"run_id": run.run_id, "status": run.status}),
            )
        )

    # Also clear poster state for any of these movies that have a poster deployed.
    movies_with_poster = (
        (
            await db.execute(
                select(Movie).where(
                    Movie.id.in_(movie_ids),
                    Movie.poster_path.isnot(None),
                )
            )
        )
        .scalars()
        .all()
    )

    for movie in movies_with_poster:
        movie.poster_path = None
        movie.poster_source = None
        movie.poster_source_url = None
        movie.poster_ai_selected = False
        movie.poster_embedding = None
        movie.poster_sha256 = None
        movie.poster_phash = None
        movie.poster_user_approved = False
        movie.poster_deployed_filename = None
        movie.poster_deployed_at = None

    await db.commit()
    logger.info(
        "REVIEW RESET | runs_cleared=%d | posters_reset=%d",
        len(review_runs),
        len(movies_with_poster),
    )
    return {
        "reset": len(review_runs),
        "runs_cleared": len(review_runs),
        "posters_reset": len(movies_with_poster),
    }


@movies_router.get("/{movie_id}/runs")
async def list_movie_runs(
    movie_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Run history for a movie, newest first."""
    runs = (
        (
            await db.execute(
                select(PipelineRun)
                .where(PipelineRun.movie_id == movie_id)
                .order_by(PipelineRun.started_at.desc())
            )
        )
        .scalars()
        .all()
    )
    return {
        "movie_id": movie_id,
        "runs": [
            {
                "run_id": r.run_id,
                "status": r.status,
                "started_at": r.started_at.isoformat() if r.started_at else None,
                "completed_at": r.completed_at.isoformat() if r.completed_at else None,
                "scorer_name": r.scorer_name,
                "counts": json.loads(r.counts_json) if r.counts_json else None,
                "reviewed": r.feedback_event_id is not None,
            }
            for r in runs
        ],
    }


@movies_router.get("/{movie_id}/artwork-events")
async def list_artwork_events(
    movie_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Deploy/restore history for a movie, newest first."""
    events = (
        (
            await db.execute(
                select(ArtworkEvent)
                .where(ArtworkEvent.movie_id == movie_id)
                .order_by(ArtworkEvent.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    return {
        "movie_id": movie_id,
        "events": [
            {
                "id": e.id,
                "action": e.action,
                "source": e.source,
                "detail": json.loads(e.detail) if e.detail else None,
                "created_at": e.created_at.isoformat() if e.created_at else None,
            }
            for e in events
        ],
    }
