"""Pipeline routes — trigger runs, stream progress, serve results + posters."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from marquee.api.deps import enforce_rate_limit, get_rate_limiter, get_tmdb
from marquee.api.results import (
    build_results_payload,
    feature_vector_from_archive,
    find_candidate,
    poster_url,
)
from marquee.config import settings
from marquee.core.pipeline_config import PipelineSettings, pipeline_settings
from marquee.core.poster_sources.tmdb import TMDBClient
from marquee.core.rate_limit import RateLimiter
from marquee.database import get_db
from marquee.models import ArtworkEvent, Movie, PipelineRun
from marquee.pipeline.gate import PosterGate
from marquee.pipeline.run_manager import RunInProgressError, run_manager
from marquee.pipeline.scorer import WeightedScorer

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/pipeline", tags=["pipeline"])
movies_router = APIRouter(prefix="/api/movies", tags=["movies"])


# ---------------------------------------------------------------------------
# Run lifecycle
# ---------------------------------------------------------------------------


@router.post("/movie/{movie_id}/run", status_code=202)
async def run_pipeline(
    movie_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    tmdb: Annotated[TMDBClient, Depends(get_tmdb)],
    limiter: Annotated[RateLimiter, Depends(get_rate_limiter)],
):
    """Start a pipeline run for a movie. 202 + run_id, or 409 if one is active."""
    movie = (
        await db.execute(select(Movie).where(Movie.id == movie_id))
    ).scalar_one_or_none()
    if movie is None:
        raise HTTPException(status_code=404, detail=f"Movie id={movie_id} not found")
    if movie.tmdb_id is None:
        raise HTTPException(
            status_code=400, detail=f"Movie {movie.title!r} has no TMDB ID — run sync"
        )

    enforce_rate_limit(limiter, f"pipeline:{movie_id}", settings.RATE_PIPELINE_RUN_SECONDS)
    try:
        run_id = await run_manager.start(tmdb=tmdb, movie=movie)
    except RunInProgressError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "A pipeline run is already in progress",
                "active_run_id": exc.active_run_id,
            },
        ) from exc

    limiter.record(f"pipeline:{movie_id}")
    return {
        "run_id": run_id,
        "events_url": f"/api/pipeline/runs/{run_id}/events",
        "results_url": f"/api/pipeline/runs/{run_id}",
    }


@router.get("/runs/{run_id}/events")
async def stream_events(run_id: str):
    """SSE stream of live stage progress. Replays history, then live, then done."""
    state = run_manager.get_state(run_id)
    if state is None:
        raise HTTPException(
            status_code=404,
            detail=f"No live run {run_id}. It may have finished — GET the results.",
        )

    async def event_generator():
        from marquee.pipeline.run_manager import _SENTINEL  # noqa: PLC0415

        queue = state.subscribe()
        try:
            while True:
                event = await queue.get()
                if event is _SENTINEL:
                    yield "event: done\ndata: {}\n\n"
                    return
                yield f"data: {json.dumps(event)}\n\n"
        finally:
            state.unsubscribe(queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


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
        return {"run_id": run_id, "status": "running", "events_url": f"/api/pipeline/runs/{run_id}/events"}

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
    if not any(
        str(image_path).startswith(str(root.resolve()))
        for root in legacy_roots
    ):
        raise HTTPException(status_code=403, detail="Poster path outside run tree")
    if not image_path.is_file():
        raise HTTPException(status_code=404, detail="Poster file no longer on disk")
    return FileResponse(image_path)


class RescoreRequest(BaseModel):
    # Feature-name -> weight (e.g. {"knn_sim": 0.4, "face_area": 0.0}).
    weights: dict[str, float] | None = None
    # Gate knob -> floor (e.g. {"GATE_MIN_AESTHETIC": 4.0}).
    gates: dict[str, float] | None = None


def _clone_config(weights: dict[str, float] | None, gates: dict[str, float] | None) -> PipelineSettings:
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
# Run history (per movie)
# ---------------------------------------------------------------------------


@movies_router.get("/{movie_id}/runs")
async def list_movie_runs(
    movie_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Run history for a movie, newest first."""
    runs = (
        await db.execute(
            select(PipelineRun)
            .where(PipelineRun.movie_id == movie_id)
            .order_by(PipelineRun.started_at.desc())
        )
    ).scalars().all()
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
        await db.execute(
            select(ArtworkEvent)
            .where(ArtworkEvent.movie_id == movie_id)
            .order_by(ArtworkEvent.created_at.desc())
        )
    ).scalars().all()
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
