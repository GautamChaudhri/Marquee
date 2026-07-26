"""Pipeline routes — trigger runs, stream progress, serve results + posters."""

from __future__ import annotations

import json
import logging
import os
import time
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy import exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from marquee.api.deps import enforce_rate_limit, get_rate_limiter
from marquee.api.job_submission import JobSubmissionResponse, submission_response
from marquee.api.library_serializers import enrich_movie
from marquee.api.results import (
    build_results_payload,
    diagnostic_candidates,
    feature_vector_from_archive,
    find_review_survivor,
    poster_url,
)
from marquee.api.routes.jobs import job_summary
from marquee.config import settings
from marquee.core.jobs.artifact_service import ArtifactError, verify_physical_artifact
from marquee.core.jobs.batches import BatchScope, create_fixed_batch
from marquee.core.jobs.contracts import TriggerKind
from marquee.core.jobs.mutation_documents import (
    PipelineCacheClearRequestV1,
    PosterMaintenanceRequestV1,
)
from marquee.core.jobs.pipeline_archives import load_pipeline_archive
from marquee.core.jobs.poster_parents import create_poster_parent
from marquee.core.jobs.poster_submission import poster_child_idempotency_key
from marquee.core.jobs.poster_summary import latest_poster_heal_summary
from marquee.core.jobs.submission import (
    IdempotencyConflictError,
    Initiator,
    SubjectLocator,
    SubmissionError,
    SubmissionIntent,
    submit_job,
)
from marquee.core.pipeline_config import PipelineSettings, pipeline_settings
from marquee.core.rate_limit import RateLimiter
from marquee.database import get_db
from marquee.models import (
    ArtworkEvent,
    Job,
    JobArtifact,
    MediaFile,
    Movie,
    PipelineRun,
)
from marquee.pipeline.gate import PosterGate
from marquee.pipeline.scorer import WeightedScorer

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/pipeline", tags=["pipeline"])
movies_router = APIRouter(prefix="/api/movies", tags=["movies"])

_REVIEW_QUEUE_STATUSES = {"completed", "flagged_manual"}
_STALE_BATCH_JOB_TO_RUN_STATUS = {
    "failed": "failed",
    "dead_letter": "failed",
    "cancelled": "cancelled",
    "interrupted": "interrupted",
}


def _downloaded():
    """Movie has a file on disk. Mirrors the library list's availability filter
    (``api/routes/library.py``) so the pipeline ignores undownloaded Radarr
    movies entirely — exactly like the films list does."""
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
            PipelineRun.media_type == "movie",
            PipelineRun.feedback_event_id.is_(None),
            PipelineRun.status.in_(_REVIEW_QUEUE_STATUSES),
            _downloaded(),
        )
        .group_by(PipelineRun.movie_id)
        .subquery()
    )


def _latest_run_per_movie():
    return (
        select(
            PipelineRun.movie_id.label("movie_id"),
            func.max(PipelineRun.started_at).label("started_at"),
        )
        .join(Movie, Movie.id == PipelineRun.movie_id)
        .where(PipelineRun.media_type == "movie", _downloaded())
        .group_by(PipelineRun.movie_id)
        .subquery()
    )


async def _repair_stale_batch_pipeline_runs(db: AsyncSession) -> int:
    rows = (
        await db.execute(
            select(PipelineRun, Job)
            .join(Job, Job.id == PipelineRun.batch_id)
            .where(
                PipelineRun.status == "running",
                PipelineRun.batch_id.is_not(None),
                Job.phase == "terminal",
                Job.outcome.in_(tuple(_STALE_BATCH_JOB_TO_RUN_STATUS)),
            )
        )
    ).all()
    if not rows:
        return 0

    repaired = 0
    now = datetime.now(UTC)
    for run, job in rows:
        new_status = _STALE_BATCH_JOB_TO_RUN_STATUS.get(job.outcome)
        if new_status is None:
            continue
        run.status = new_status
        run.completed_at = run.completed_at or job.terminal_at or now
        if not run.error and job.error:
            if isinstance(job.error, dict):
                run.error = str(job.error.get("message") or job.error.get("type") or job.error)
            else:
                run.error = str(job.error)
        repaired += 1

    if repaired:
        await db.commit()
        logger.info("PIPELINE RUN REPAIR | repaired=%d stale batch run row(s)", repaired)
    return repaired


def _backup_stats() -> dict[str, int]:
    count = total_bytes = 0
    base = settings.poster_backup_path
    if base.is_dir():
        for entry in os.scandir(base):
            if entry.is_file() and entry.name.endswith(".jpg"):
                count += 1
                total_bytes += entry.stat().st_size
    return {"count": count, "bytes": total_bytes}


# ---------------------------------------------------------------------------
# Run lifecycle
# ---------------------------------------------------------------------------


@router.post("/movie/{movie_id}/run", status_code=202)
async def run_pipeline(
    movie_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    limiter: Annotated[RateLimiter, Depends(get_rate_limiter)],
) -> JobSubmissionResponse:
    """Submit one canonical, non-deploying poster-analysis job for a movie."""
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
    if db.in_transaction():
        await db.commit()
    try:
        async with db.begin():
            result = await submit_job(
                db,
                job_type="poster_pipeline",
                request={
                    "movie_id": movie.id,
                    "tmdb_id": movie.tmdb_id,
                    "title": movie.title,
                    "source_descriptors": [
                        {"provider": "tmdb", "reference": f"movie:{movie.tmdb_id}"}
                    ],
                },
                subject=SubjectLocator(kind="movie", reference=str(movie.id)),
                trigger=TriggerKind.MANUAL,
                initiator=Initiator(kind="system", identifier="pipeline-api"),
                idempotency_key=(
                    f"poster_pipeline:movie:{movie.id}:"
                    f"{int(time.time() // settings.RATE_PIPELINE_RUN_SECONDS)}"
                ),
                priority=90,
            )
    except IdempotencyConflictError as exc:
        raise HTTPException(status_code=409, detail=exc.api_detail) from exc
    except SubmissionError as exc:
        raise HTTPException(status_code=422, detail=exc.code) from exc
    return submission_response(result)


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
            "status_url": f"/api/jobs/{run_id}/snapshot",
        }

    archive = await load_pipeline_archive(db, run)
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
    archive = await load_pipeline_archive(db, run)
    if archive is None:
        raise HTTPException(status_code=404, detail="Run archive unavailable")

    candidate = find_review_survivor(archive, orig_filename)
    if candidate is None:
        raise HTTPException(
            status_code=404, detail=f"No reviewable candidate {orig_filename!r} in run {run_id}"
        )

    artifact_id = candidate.get("artifact_id")
    if isinstance(artifact_id, int):
        artifact = await db.get(JobArtifact, artifact_id)
        metadata = (
            artifact.artifact_metadata
            if artifact is not None and isinstance(artifact.artifact_metadata, dict)
            else {}
        )
        if (
            artifact is None
            or artifact.job_id != run.job_id
            or artifact.attempt_id != run.attempt_id
            or artifact.kind != "evidence_image"
            or artifact.status != "available"
            or artifact.storage_key != candidate.get("artifact_storage_key")
            or artifact.checksum != candidate.get("artifact_checksum")
            or metadata.get("family") != "poster_pipeline"
            or metadata.get("role") != "review_candidate"
            or metadata.get("candidate_reference") != orig_filename
        ):
            raise HTTPException(status_code=404, detail="Candidate artifact unavailable")
        try:
            boundary, classified = await verify_physical_artifact(artifact)
        except ArtifactError as exc:
            raise HTTPException(status_code=404, detail="Candidate artifact unavailable") from exc
        return boundary.response(classified)

    raise HTTPException(status_code=404, detail="Candidate artifact unavailable")


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
    archive = await load_pipeline_archive(db, run)
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
    for candidate in diagnostic_candidates(archive):
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
) -> JobSubmissionResponse:
    """Create a ticketless poster-analysis parent with one immutable child per movie."""
    scope = body.scope
    if scope == "selected":
        if not body.movie_ids:
            raise HTTPException(status_code=400, detail="scope=selected requires movie_ids")
        query = select(Movie).where(
            Movie.id.in_(body.movie_ids), Movie.tmdb_id.is_not(None), _downloaded()
        )
    elif scope == "missing":
        query = select(Movie).where(
            Movie.poster_path.is_(None), Movie.tmdb_id.is_not(None), _downloaded()
        )
    elif scope == "all":
        query = select(Movie).where(Movie.tmdb_id.is_not(None), _downloaded())
    else:
        raise HTTPException(status_code=400, detail=f"unknown scope {scope!r}")

    movies = list((await db.execute(query.order_by(Movie.id))).scalars().all())
    if not movies:
        raise HTTPException(status_code=404, detail=f"no eligible movies for scope={scope!r}")
    cap = pipeline_settings.PIPELINE_BATCH_MAX_MOVIES
    if len(movies) > cap:
        raise HTTPException(
            status_code=400,
            detail=f"batch of {len(movies)} movies exceeds PIPELINE_BATCH_MAX_MOVIES={cap}",
        )
    nonce = uuid4().hex
    initiator = Initiator(kind="system", identifier="pipeline-api")
    children = [
        SubmissionIntent(
            job_type="poster_pipeline",
            request={
                "movie_id": movie.id,
                "tmdb_id": movie.tmdb_id,
                "title": movie.title,
                "source_descriptors": [{"provider": "tmdb", "reference": f"movie:{movie.tmdb_id}"}],
            },
            subject=SubjectLocator(kind="movie", reference=str(movie.id)),
            trigger=TriggerKind.BATCH,
            initiator=initiator,
            idempotency_key=f"poster_pipeline:batch-{nonce}-movie-{movie.id}",
            priority=80,
        )
        for movie in movies
    ]
    if db.in_transaction():
        await db.commit()
    try:
        async with db.begin():
            result = await create_fixed_batch(
                db,
                parent_job_type="poster_pipeline_batch",
                parent_request={"scope": scope, "selection_count": len(movies)},
                scope=BatchScope(
                    reference=nonce,
                    display_name="Poster analysis · movies",
                    summary=f"{scope} · {len(movies)} movies",
                ),
                trigger=TriggerKind.BATCH,
                initiator=initiator,
                idempotency_key=f"poster_pipeline_batch:manual-{nonce}",
                children=children,
            )
    except SubmissionError as exc:
        raise HTTPException(status_code=422, detail=exc.code) from exc
    return submission_response(result.parent)


@router.get("/summary")
async def pipeline_summary(db: Annotated[AsyncSession, Depends(get_db)]):
    await _repair_stale_batch_pipeline_runs(db)
    downloaded = _downloaded()
    total_movies = await db.scalar(select(func.count(Movie.id)).where(downloaded))
    movies_with_poster = await db.scalar(
        select(func.count(Movie.id)).where(downloaded, Movie.poster_path.is_not(None))
    )
    latest = _review_queue_latest()
    latest_runs = _latest_run_per_movie()
    movies_in_review = await db.scalar(select(func.count()).select_from(latest))
    movies_in_run = await db.scalar(
        select(func.count())
        .select_from(latest_runs)
        .join(
            PipelineRun,
            (PipelineRun.movie_id == latest_runs.c.movie_id)
            & (PipelineRun.started_at == latest_runs.c.started_at),
        )
        .where(PipelineRun.status == "running")
    )

    active_jobs = (
        (
            await db.execute(
                select(Job)
                .where(
                    Job.type.in_(("poster_pipeline", "poster_pipeline_batch")),
                    Job.phase.in_(("queued", "running", "stopping")),
                )
                .order_by(Job.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    running_jobs = []
    for job in active_jobs:
        summary = job_summary(job)
        payload = job.request if isinstance(job.request, dict) else {}
        if job.type == "poster_pipeline_batch":
            summary["movie_count"] = len(payload.get("movie_ids") or [])
        elif job.subject_type == "movie" or payload.get("movie_id") is not None:
            summary["movie_count"] = 1
        else:
            summary["movie_count"] = 0
        running_jobs.append(summary)

    heal_schedule = None

    total = total_movies or 0
    with_poster = movies_with_poster or 0
    return {
        "total_movies": total,
        "movies_with_poster": with_poster,
        "movies_missing_poster": max(total - with_poster, 0),
        "movies_in_review": movies_in_review or 0,
        "movies_in_run": movies_in_run or 0,
        "running_jobs": running_jobs,
        "last_heal": await latest_poster_heal_summary(db),
        "heal_schedule": heal_schedule,
        "backups": _backup_stats(),
    }


@router.post("/rescan-posters", status_code=202)
async def rescan_posters(
    db: Annotated[AsyncSession, Depends(get_db)],
) -> JobSubmissionResponse:
    """Submit the canonical read-only poster projection rescan."""
    if db.in_transaction():
        await db.commit()
    try:
        async with db.begin():
            result = await submit_job(
                db,
                job_type="poster_rescan",
                request={"scope": "all"},
                subject=SubjectLocator(kind="poster_candidate_set", reference="all"),
                trigger=TriggerKind.MANUAL,
                initiator=Initiator(kind="system", identifier="pipeline-api"),
                idempotency_key=f"poster_rescan:all:{int(time.time() // 30)}",
                priority=35,
            )
    except IdempotencyConflictError as exc:
        raise HTTPException(status_code=409, detail=exc.api_detail) from exc
    except SubmissionError as exc:
        raise HTTPException(status_code=422, detail=exc.code) from exc
    return submission_response(result)


@router.post("/backup-all", status_code=202)
async def backup_all_posters(
    db: Annotated[AsyncSession, Depends(get_db)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
) -> JobSubmissionResponse:
    initiator = Initiator(kind="system", identifier="pipeline-api")
    if db.in_transaction():
        await db.commit()
    try:
        async with db.begin():
            result = await create_poster_parent(
                db,
                parent_job_type="poster_backup_all",
                idempotency_key=idempotency_key,
                trigger=TriggerKind.BATCH,
                initiator=initiator,
            )
    except (SubmissionError, ValueError) as exc:
        raise HTTPException(status_code=422, detail="poster_backup_scope_invalid") from exc
    return submission_response(result.parent)


@router.post("/maintenance", status_code=202)
async def poster_maintenance(
    body: PosterMaintenanceRequestV1,
    db: Annotated[AsyncSession, Depends(get_db)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
) -> JobSubmissionResponse:
    if db.in_transaction():
        await db.commit()
    try:
        async with db.begin():
            result = await submit_job(
                db,
                job_type="poster_maintenance",
                request=body.model_dump(mode="json"),
                subject=SubjectLocator(kind="maintenance_scope", reference="poster-maintenance"),
                trigger=TriggerKind.MANUAL,
                initiator=Initiator(kind="system", identifier="pipeline-api"),
                idempotency_key=idempotency_key,
                priority=30,
            )
    except SubmissionError as exc:
        raise HTTPException(status_code=422, detail=exc.code) from exc
    return submission_response(result)


# ---------------------------------------------------------------------------
# Pipeline cache (downloaded posters + working artifacts — never head/taste data)
# ---------------------------------------------------------------------------


@router.get("/cache")
async def get_pipeline_cache():
    """Read-only size projection for the canonical cache-clear job."""

    def directory_size(path: Path) -> int:
        if not path.exists():
            return 0
        return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())

    sizes = {
        "runs_work": directory_size(settings.runs_work_path),
        "staging": directory_size(settings.poster_staging_path),
        "embeddings": directory_size(Path(pipeline_settings.EMBEDDING_CACHE_DIR)),
        "archives": directory_size(settings.runs_archive_path),
    }
    clearable = sizes["runs_work"] + sizes["staging"] + sizes["embeddings"]
    return {
        "sizes_bytes": sizes,
        "clearable_bytes": clearable,
        "total_bytes": sum(sizes.values()),
    }


class ReviewQueueApproveAutoRequest(BaseModel):
    deploy: bool = True


@router.post("/cache/clear", status_code=202)
async def clear_pipeline_cache(
    body: PipelineCacheClearRequestV1,
    db: Annotated[AsyncSession, Depends(get_db)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
) -> JobSubmissionResponse:
    """Clear downloaded-poster pipeline caches. Never touches head/taste data."""
    if db.in_transaction():
        await db.commit()
    try:
        async with db.begin():
            result = await submit_job(
                db,
                job_type="pipeline_cache_clear",
                request=body.model_dump(mode="json"),
                subject=SubjectLocator(kind="maintenance_scope", reference="pipeline-cache"),
                trigger=TriggerKind.MANUAL,
                initiator=Initiator(kind="system", identifier="pipeline-api"),
                idempotency_key=idempotency_key,
                priority=40,
            )
    except SubmissionError as exc:
        raise HTTPException(status_code=422, detail=exc.code) from exc
    return submission_response(result)


@router.post("/posters/reset", status_code=202)
async def reset_deployed_posters(
    db: Annotated[AsyncSession, Depends(get_db)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
) -> JobSubmissionResponse:
    """Seal a canonical reset parent with one isolated child per poster subject."""
    initiator = Initiator(kind="system", identifier="pipeline-api")
    if db.in_transaction():
        await db.commit()
    try:
        async with db.begin():
            result = await create_poster_parent(
                db,
                parent_job_type="poster_deploy_reset",
                idempotency_key=idempotency_key,
                trigger=TriggerKind.BATCH,
                initiator=initiator,
                priority=30,
            )
    except (SubmissionError, ValueError) as exc:
        raise HTTPException(status_code=422, detail="poster_reset_scope_invalid") from exc
    return submission_response(result.parent)


# ---------------------------------------------------------------------------
# Aggregate metrics (cheap — from PipelineRun rows, no archive reads)
# ---------------------------------------------------------------------------


def aggregate_run_metrics(runs: list[PipelineRun]) -> dict:
    """Cross-run aggregates from a list of PipelineRun rows.

    Shared by the movie ``/metrics`` endpoint and the TV
    ``/api/pipeline/tv/metrics`` endpoint — only the run query filter differs.
    """
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


@router.get("/metrics")
async def pipeline_metrics(
    db: Annotated[AsyncSession, Depends(get_db)],
    limit: int = 500,
):
    """Cross-run aggregates for a metrics dashboard, from recent PipelineRun rows."""
    limit = min(max(limit, 1), 5000)
    runs = (
        (
            await db.execute(
                select(PipelineRun)
                .where(PipelineRun.media_type == "movie")
                .order_by(PipelineRun.started_at.desc())
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    return aggregate_run_metrics(runs)


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
    await _repair_stale_batch_pipeline_runs(db)
    page = max(page, 1)
    page_size = min(max(page_size, 1), 200)

    latest = _review_queue_latest()
    base = (
        select(PipelineRun, Movie)
        .join(
            latest,
            (latest.c.movie_id == PipelineRun.movie_id)
            & (latest.c.started_at == PipelineRun.started_at),
        )
        .join(Movie, Movie.id == PipelineRun.movie_id)
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

    movies = [movie for _, movie in rows]
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

    items = []
    for run, movie in rows:
        mf = mf_by_movie.get(movie.id)
        items.append(
            {
                "movie": enrich_movie(movie, mf),
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


@router.post("/review-queue/approve-auto")
async def approve_review_queue_auto(
    body: ReviewQueueApproveAutoRequest,
    request: Request,
    response: Response,
    db: Annotated[AsyncSession, Depends(get_db)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
):
    """Approve every approvable run in the current review queue."""
    from marquee.api.routes.feedback import FeedbackRequest, apply_feedback_request  # noqa: PLC0415

    if body.deploy and idempotency_key is None:
        raise HTTPException(status_code=422, detail="Idempotency-Key is required for deploy")
    await _repair_stale_batch_pipeline_runs(db)

    latest = _review_queue_latest()
    rows = (
        await db.execute(
            select(PipelineRun, Movie)
            .join(
                latest,
                (latest.c.movie_id == PipelineRun.movie_id)
                & (latest.c.started_at == PipelineRun.started_at),
            )
            .join(Movie, Movie.id == PipelineRun.movie_id)
            .where(
                PipelineRun.feedback_event_id.is_(None),
                PipelineRun.status.in_(_REVIEW_QUEUE_STATUSES),
            )
            .order_by(PipelineRun.started_at.desc())
        )
    ).all()

    approved = 0
    skipped_no_auto = 0
    failed = 0
    errors: list[dict[str, object]] = []
    deployment_jobs: list[dict[str, object]] = []

    for run, movie in rows:
        if run.status != "completed" or not run.auto_pick_filename:
            skipped_no_auto += 1
            continue
        try:
            result = await apply_feedback_request(
                FeedbackRequest(
                    run_id=run.run_id,
                    action="approve",
                    deploy=body.deploy,
                    idempotency_key=(
                        poster_child_idempotency_key(idempotency_key, run.run_id)
                        if idempotency_key is not None
                        else None
                    ),
                ),
                request,
                db,
            )
            if result.get("deployment_job") is not None:
                deployment_jobs.append(result["deployment_job"])
            approved += 1
        except HTTPException as exc:
            await db.rollback()
            failed += 1
            errors.append(
                {
                    "run_id": run.run_id,
                    "movie_id": movie.id,
                    "title": movie.title,
                    "error": exc.detail,
                }
            )
        except Exception:  # noqa: BLE001
            await db.rollback()
            failed += 1
            errors.append(
                {
                    "run_id": run.run_id,
                    "movie_id": movie.id,
                    "title": movie.title,
                    "error": "feedback_apply_failed",
                }
            )

    if body.deploy:
        response.status_code = 202
    return {
        "total": len(rows),
        "approved": approved,
        "skipped_no_auto": skipped_no_auto,
        "failed": failed,
        "errors": errors,
        "deployment_jobs": deployment_jobs,
    }


@router.post("/review/reset", status_code=200)
async def reset_review_queue(
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Reset the review queue's disposition only (JMC6H H19).

    Marks review-queue PipelineRuns as reviewed so they leave the Review tab. It
    does NOT touch deployed artwork or poster DB state: clearing or removing a
    deployed poster is a separately authorized canonical ``poster_reset`` job, so
    the database and the on-disk poster can never be left disagreeing here.
    """
    # Find all PipelineRuns currently in the review queue.
    result = await db.execute(
        select(PipelineRun).where(
            PipelineRun.media_type == "movie",
            PipelineRun.feedback_event_id.is_(None),
            PipelineRun.status.in_(_REVIEW_QUEUE_STATUSES),
        )
    )
    review_runs = result.scalars().all()

    if not review_runs:
        return {"reset": 0}

    # Mark the review-queue runs as reviewed (review disposition only).
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

    # H19: this route never nulls poster DB state. Clearing or removing deployed
    # artwork is the separately authorized canonical poster_reset job, so the
    # database and the on-disk poster cannot be left disagreeing.
    await db.commit()
    logger.info("REVIEW RESET | runs_cleared=%d | posters_reset=0", len(review_runs))
    return {
        "reset": len(review_runs),
        "runs_cleared": len(review_runs),
        "posters_reset": 0,
    }


@movies_router.get("/{movie_id}/runs")
async def list_movie_runs(
    movie_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Run history for a movie, newest first."""
    await _repair_stale_batch_pipeline_runs(db)
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
