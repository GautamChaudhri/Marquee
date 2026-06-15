"""Taste routes — profile status and full rebuild (design 09 §17.4).

The taste-map endpoints (design 11) are added to this same router later.
"""

from __future__ import annotations

import asyncio
import logging
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from marquee.core.pipeline_config import pipeline_settings
from marquee.database import get_db
from marquee.ml import feedback_store
from marquee.models import Movie

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/taste", tags=["taste"])

# Simple in-process state for the background rebuild (polled via /status).
_rebuild_state: dict = {"running": False, "started_at": None, "finished_at": None, "error": None}


def _exemplar_stats() -> dict:
    from marquee.ml.taste_store import NumpyTasteStore  # noqa: PLC0415

    path = Path(pipeline_settings.TASTE_PROFILE_PATH)
    stats: dict = {
        "count": 0,
        "negatives": 0,
        "last_rebuild": None,
        "profile_present": path.exists(),
    }
    if path.exists():
        stats["last_rebuild"] = datetime.fromtimestamp(
            path.stat().st_mtime, tz=UTC
        ).isoformat()
        try:
            store = NumpyTasteStore()
            stats["count"] = store.size
            stats["negatives"] = store.negative_size
        except Exception as exc:  # noqa: BLE001
            logger.warning("taste status: could not load profile (%s)", exc)
    return stats


def _head_status() -> dict:
    from marquee.ml.head_trainer import _RUNS_DIR, build_training_data  # noqa: PLC0415

    rows = feedback_store.read_all()
    _x, targets, _names, n_movies = build_training_data(rows, _RUNS_DIR)
    n_samples = int(len(targets))
    active = Path(pipeline_settings.LEARNED_HEAD_PATH).exists()
    return {
        "active": active,
        "n_samples": n_samples,
        "activation": {
            "movies": {"have": n_movies, "need": pipeline_settings.HEAD_MIN_MOVIES},
            "labels": {"have": n_samples, "need": pipeline_settings.HEAD_MIN_LABELS},
        },
    }


@router.get("/status")
async def taste_status(db: Annotated[AsyncSession, Depends(get_db)]):
    summary = feedback_store.summary()

    # Genre spread over labeled movies (v2 rows carry movie_id).
    movie_ids = [
        int(key) for key in summary["movie_keys"] if key.lstrip("-").isdigit()
    ]
    genres: Counter[str] = Counter()
    if movie_ids:
        rows = (
            await db.execute(select(Movie.genres).where(Movie.id.in_(movie_ids)))
        ).scalars().all()
        for movie_genres in rows:
            for genre in movie_genres or []:
                genres[genre] += 1

    return {
        "labels": {
            "total": summary["total"],
            "movies": summary["movies"],
            "positives": summary["positives"],
            "negatives": summary["negatives"],
            "genres": dict(genres.most_common()),
        },
        "exemplars": _exemplar_stats(),
        "learned_head": _head_status(),
        "gate_alerts": feedback_store.gate_override_alerts(),
        "rebuild": _rebuild_state,
    }


async def _run_rebuild() -> None:
    from marquee.ml.head_trainer import train_from_labels  # noqa: PLC0415
    from marquee.ml.taste_trainer import rebuild_profile  # noqa: PLC0415
    from marquee.pipeline.run_manager import run_manager  # noqa: PLC0415

    _rebuild_state.update(
        running=True, started_at=datetime.now(UTC).isoformat(), finished_at=None, error=None
    )
    try:
        # Hold the GPU against pipeline runs for the duration of the rebuild.
        run_manager.begin_rebuild()
        try:
            await asyncio.to_thread(rebuild_profile)
            run_manager.reset_extractor()
            if pipeline_settings.HEAD_AUTO_RETRAIN:
                await asyncio.to_thread(train_from_labels)
        finally:
            run_manager.end_rebuild()
    except Exception as exc:  # noqa: BLE001
        logger.exception("taste rebuild failed")
        _rebuild_state["error"] = str(exc)
    finally:
        _rebuild_state.update(running=False, finished_at=datetime.now(UTC).isoformat())


@router.post("/retrain", status_code=202)
async def retrain_taste():
    """Trigger a full taste-profile rebuild + head retrain in the background."""
    from marquee.pipeline.run_manager import run_manager  # noqa: PLC0415

    if _rebuild_state["running"]:
        return {"status": "already_running", "started_at": _rebuild_state["started_at"]}
    busy = run_manager.gpu_busy()
    if busy is not None:
        raise HTTPException(
            status_code=409,
            detail={"message": "GPU is busy — cannot rebuild now", "active": busy},
        )
    asyncio.create_task(_run_rebuild())
    return {"status": "started", "poll": "/api/taste/status"}


# ---------------------------------------------------------------------------
# Taste map (design 11)
# ---------------------------------------------------------------------------


class CandidateOverlayRequest(BaseModel):
    run_id: str


@router.get("/map")
async def get_taste_map(recompute: bool = False):
    """3D/2D projection of the taste profile, with clusters + outliers."""
    from marquee.ml.taste_map import load_map  # noqa: PLC0415

    try:
        return await asyncio.to_thread(load_map, recompute)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/map/rebuild", status_code=202)
async def rebuild_map():
    """Force a taste-map rebuild in the background."""

    async def _run():
        from marquee.ml.taste_map import build_map  # noqa: PLC0415

        try:
            await asyncio.to_thread(build_map)
        except Exception:  # noqa: BLE001
            logger.exception("taste map rebuild failed")

    asyncio.create_task(_run())
    return {"status": "started", "poll": "/api/taste/map"}


@router.post("/map/candidates")
async def overlay_candidates(
    body: CandidateOverlayRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Project a run's ranked candidates into the taste-map space."""
    from marquee.ml.taste_map import project  # noqa: PLC0415
    from marquee.models import PipelineRun  # noqa: PLC0415
    from marquee.pipeline.features import load_cached_embedding  # noqa: PLC0415
    from marquee.pipeline.run_manager import run_manager  # noqa: PLC0415

    run = (
        await db.execute(select(PipelineRun).where(PipelineRun.run_id == body.run_id))
    ).scalar_one_or_none()
    if run is None:
        raise HTTPException(status_code=404, detail=f"Run {body.run_id} not found")
    archive = run_manager.load_archive(body.run_id, run.archive_path)
    if archive is None:
        raise HTTPException(status_code=404, detail="Run archive unavailable")

    ranked = sorted(
        (c for c in archive.get("candidates", []) if c.get("rank") is not None),
        key=lambda c: c["rank"],
    )
    items = []
    embeddings = []
    for candidate in ranked:
        emb = load_cached_embedding(candidate["orig_filename"])
        if emb is None:
            continue
        items.append(candidate)
        embeddings.append(emb)

    if not embeddings:
        return {"run_id": body.run_id, "candidates": []}

    import numpy as np  # noqa: PLC0415

    projections = await asyncio.to_thread(project, np.stack(embeddings))
    return {
        "run_id": body.run_id,
        "candidates": [
            {
                "orig_filename": candidate["orig_filename"],
                "rank": candidate.get("rank"),
                "final_score": candidate.get("final_score"),
                **projection,
            }
            for candidate, projection in zip(items, projections, strict=True)
        ],
    }


def _safe_exemplar_name(name: str) -> str:
    if "/" in name or "\\" in name or ".." in name:
        raise HTTPException(status_code=400, detail="Invalid exemplar name")
    return name


@router.get("/exemplars/{name}/image")
async def exemplar_image(name: str, size: str = "thumb"):
    """Serve an exemplar's thumbnail (webp) or full-size training image."""
    from marquee.ml.taste_map import exemplar_source, thumbnail_path  # noqa: PLC0415

    name = _safe_exemplar_name(name)
    path = thumbnail_path(name) if size == "thumb" else exemplar_source(name)
    if path is None:
        # Fall back to full-size if the thumbnail hasn't been generated.
        path = exemplar_source(name)
    if path is None:
        raise HTTPException(status_code=404, detail=f"No image for exemplar {name!r}")
    return FileResponse(path)


@router.get("/exemplars/{name}/neighbors")
async def exemplar_neighbors(name: str):
    """The k nearest exemplars to this one (click-to-explore)."""
    from marquee.ml.taste_map import neighbors_of  # noqa: PLC0415

    name = _safe_exemplar_name(name)
    result = await asyncio.to_thread(neighbors_of, name)
    if result is None:
        raise HTTPException(status_code=404, detail=f"Exemplar {name!r} not in profile")
    return {"name": name, "neighbors": result}
