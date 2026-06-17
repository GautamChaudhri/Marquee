"""Taste routes — profile status and full rebuild (design 09 §17.4).

The taste-map endpoints (design 11) are added to this same router later.
"""

from __future__ import annotations

import asyncio
import logging
import multiprocessing
import os
import queue
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from marquee.api.deps import enforce_rate_limit, get_rate_limiter
from marquee.config import settings
from marquee.core.pipeline_config import pipeline_settings
from marquee.core.rate_limit import RateLimiter
from marquee.database import get_db
from marquee.ml import feedback_store
from marquee.models import Movie

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/taste", tags=["taste"])

_REBUILD_TIMEOUT_SECONDS = int(os.getenv("MARQUEE_TASTE_REBUILD_TIMEOUT_SECONDS", "1800"))
_REBUILD_POLL_SECONDS = 1.0


def _new_rebuild_state() -> dict:
    return {
        "status": "idle",
        "running": False,
        "started_at": None,
        "finished_at": None,
        "stage": None,
        "processed": 0,
        "total": 0,
        "error": None,
        "duration_s": None,
    }


# In-process state for the background rebuild monitor (polled via /status).
_rebuild_state: dict = _new_rebuild_state()


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


def _rebuild_worker(progress_queue) -> None:
    from marquee.ml.head_trainer import train_from_labels  # noqa: PLC0415
    from marquee.ml.taste_trainer import rebuild_profile  # noqa: PLC0415

    def emit(payload: dict) -> None:
        progress_queue.put({"type": "progress", **payload})

    try:
        rebuild_profile(progress_callback=emit)
        if pipeline_settings.HEAD_AUTO_RETRAIN:
            emit({"stage": "learned-head", "processed": 0, "total": 1})
            _head, info = train_from_labels()
            emit({"stage": "learned-head", "processed": 1, "total": 1, "head": info})
        progress_queue.put({"type": "done"})
    except Exception as exc:  # noqa: BLE001
        progress_queue.put({"type": "error", "error": str(exc)})
        raise


def _start_rebuild_process():
    context = multiprocessing.get_context("spawn")
    progress_queue = context.Queue()
    process = context.Process(
        target=_rebuild_worker,
        args=(progress_queue,),
        name="marquee-taste-rebuild",
    )
    process.start()
    return process, progress_queue


def _mark_rebuild_started() -> float:
    now = datetime.now(UTC).isoformat()
    _rebuild_state.clear()
    _rebuild_state.update(
        {
            **_new_rebuild_state(),
            "status": "running",
            "running": True,
            "started_at": now,
            "stage": "queued",
        }
    )
    return time.monotonic()


def _apply_rebuild_progress(message: dict) -> None:
    _rebuild_state.update(
        {
            key: message[key]
            for key in ("stage", "processed", "total")
            if key in message
        }
    )


def _finish_rebuild(status: str, started_monotonic: float, error: str | None = None) -> None:
    _rebuild_state.update(
        {
            "status": status,
            "running": False,
            "finished_at": datetime.now(UTC).isoformat(),
            "error": error,
            "duration_s": round(time.monotonic() - started_monotonic, 3),
        }
    )


async def _monitor_rebuild_process(process, progress_queue, started_monotonic: float) -> None:
    from marquee.pipeline.run_manager import run_manager  # noqa: PLC0415

    terminal_status: str | None = None
    terminal_error: str | None = None
    try:
        deadline = started_monotonic + _REBUILD_TIMEOUT_SECONDS
        while True:
            while True:
                try:
                    message = progress_queue.get_nowait()
                except queue.Empty:
                    break
                if message.get("type") == "progress":
                    _apply_rebuild_progress(message)
                elif message.get("type") == "done":
                    terminal_status = "completed"
                elif message.get("type") == "error":
                    terminal_status = "failed"
                    terminal_error = message.get("error") or "taste rebuild failed"

            if terminal_status is not None:
                process.join(timeout=10)
                if process.is_alive():
                    terminal_status = "timeout"
                    terminal_error = "rebuild process did not exit after reporting completion"
                    process.terminate()
                    process.join(timeout=10)
                    if process.is_alive():
                        process.kill()
                        process.join(timeout=10)
                break
            if not process.is_alive():
                if process.exitcode == 0:
                    terminal_status = "completed"
                else:
                    terminal_status = "failed"
                    terminal_error = terminal_error or f"rebuild process exited {process.exitcode}"
                break
            if time.monotonic() >= deadline:
                terminal_status = "timeout"
                terminal_error = f"taste rebuild exceeded {_REBUILD_TIMEOUT_SECONDS}s"
                logger.error(terminal_error)
                process.terminate()
                process.join(timeout=10)
                if process.is_alive():
                    process.kill()
                    process.join(timeout=10)
                break
            await asyncio.sleep(_REBUILD_POLL_SECONDS)
    except asyncio.CancelledError:
        terminal_status = "cancelled"
        terminal_error = "taste rebuild monitor cancelled"
        if process.is_alive():
            process.terminate()
            process.join(timeout=10)
        raise
    except Exception as exc:  # noqa: BLE001
        logger.exception("taste rebuild monitor failed")
        terminal_status = "failed"
        terminal_error = str(exc)
        if process.is_alive():
            process.terminate()
            process.join(timeout=10)
    finally:
        if terminal_status == "completed":
            run_manager.reset_extractor()
        _finish_rebuild(terminal_status or "failed", started_monotonic, terminal_error)
        run_manager.end_rebuild()
        run_manager.release_gpu_resources()
        progress_queue.close()


@router.post("/retrain", status_code=202)
async def retrain_taste(
    limiter: Annotated[RateLimiter, Depends(get_rate_limiter)],
):
    """Trigger a full taste-profile rebuild + head retrain in the background."""
    from marquee.pipeline.run_manager import run_manager  # noqa: PLC0415

    enforce_rate_limit(limiter, "taste_retrain", settings.RATE_TASTE_RETRAIN_SECONDS)
    if _rebuild_state["running"]:
        return {"status": "already_running", "started_at": _rebuild_state["started_at"]}
    busy = run_manager.gpu_busy()
    if busy is not None:
        raise HTTPException(
            status_code=409,
            detail={"message": "GPU is busy — cannot rebuild now", "active": busy},
        )
    run_manager.begin_rebuild()
    started_monotonic = _mark_rebuild_started()
    run_manager.release_gpu_resources()
    try:
        process, progress_queue = _start_rebuild_process()
    except Exception:
        run_manager.end_rebuild()
        _finish_rebuild("failed", started_monotonic, "could not start rebuild process")
        raise
    asyncio.create_task(_monitor_rebuild_process(process, progress_queue, started_monotonic))
    limiter.record("taste_retrain")
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
async def rebuild_map(
    limiter: Annotated[RateLimiter, Depends(get_rate_limiter)],
):
    """Force a taste-map rebuild in the background."""
    enforce_rate_limit(limiter, "taste_map_rebuild", settings.RATE_TASTE_MAP_REBUILD_SECONDS)

    async def _run():
        from marquee.ml.taste_map import build_map  # noqa: PLC0415

        try:
            await asyncio.to_thread(build_map)
        except Exception:  # noqa: BLE001
            logger.exception("taste map rebuild failed")

    asyncio.create_task(_run())
    limiter.record("taste_map_rebuild")
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
