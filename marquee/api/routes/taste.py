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
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from marquee.api.deps import enforce_rate_limit, get_rate_limiter
from marquee.api.routes.jobs import job_summary
from marquee.config import settings
from marquee.core.jobs import job_manager
from marquee.core.pipeline_config import pipeline_settings
from marquee.core.rate_limit import RateLimiter
from marquee.database import get_db
from marquee.ml import artifact_registry, feedback_store
from marquee.ml.namespaces import TasteNamespace, get_namespace
from marquee.models import Job, Movie

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/taste", tags=["taste"])

_REBUILD_TIMEOUT_SECONDS = int(os.getenv("MARQUEE_TASTE_REBUILD_TIMEOUT_SECONDS", "1800"))
_REBUILD_POLL_SECONDS = 1.0


def _new_rebuild_state() -> dict:
    return {
        "status": "idle",
        "running": False,
        "started_at": None,
        "updated_at": None,
        "stage_started_at": None,
        "finished_at": None,
        "stage": None,
        "substage": None,
        "current_item": None,
        "message": None,
        "pid": None,
        "processed": 0,
        "total": 0,
        "error": None,
        "duration_s": None,
    }


# In-process state for the background rebuild monitor (polled via /status).
_rebuild_state: dict = _new_rebuild_state()
_active_rebuild_process = None
_rebuild_cancel_requested: str | None = None


def _artifact_error(exc: Exception) -> HTTPException:
    if isinstance(exc, artifact_registry.ArtifactRegistryUnavailableError):
        return HTTPException(status_code=503, detail=str(exc))
    if isinstance(exc, FileNotFoundError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, RuntimeError):
        return HTTPException(status_code=409, detail=str(exc))
    return HTTPException(status_code=400, detail=str(exc))


def _exemplar_stats(ns: TasteNamespace | None = None) -> dict:
    from marquee.ml.taste_store import NumpyTasteStore  # noqa: PLC0415

    ns = ns or get_namespace("movies")
    path = ns.profile_path
    stats: dict = {
        "count": 0,
        "negatives": 0,
        "last_rebuild": None,
        "profile_present": path.exists(),
    }
    if path.exists():
        stats["last_rebuild"] = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC).isoformat()
        try:
            store = NumpyTasteStore(path)
            stats["count"] = store.size
            stats["negatives"] = store.negative_size
            # For TV, add per-kind counts
            if ns.library == "tv":
                kind_counts: Counter[str] = Counter()
                for meta in store.metadata:
                    kind_counts[meta.get("asset_kind", "unknown")] += 1
                stats["by_kind"] = dict(kind_counts)
        except Exception as exc:  # noqa: BLE001
            logger.warning("taste status: could not load profile (%s)", exc)
    return stats


def _head_status(ns: TasteNamespace | None = None) -> dict:
    """Activation progress for the learned head, in whichever training mode is
    active. Keeps a stable shape (``n_samples`` + ``activation.{movies,labels}``)
    so the UI is mode-agnostic; ``mode`` tells it whether the unit is pairs or
    labels. In pairwise mode ``n_samples``/``labels`` carry the derived
    within-movie preference-pair counts."""
    from marquee.ml.head_trainer import (  # noqa: PLC0415
        _LEGACY_RUNS_DIRS,
        build_inversion_training_data,
        build_training_data,
    )

    ns = ns or get_namespace("movies")
    rows = feedback_store.read_all(ns)
    active = ns.head_path.exists()

    if pipeline_settings.HEAD_TRAIN_MODE == "pairwise":
        _d, _w, _names, n_movies, n_pairs = build_inversion_training_data(rows)
        return {
            "active": active,
            "mode": "pairwise",
            "n_samples": n_pairs,
            "activation": {
                "movies": {"have": n_movies, "need": pipeline_settings.HEAD_MIN_MOVIES},
                "labels": {"have": n_pairs, "need": pipeline_settings.HEAD_MIN_PAIRS},
            },
        }

    _x, targets, _names, n_movies = build_training_data(
        rows, (settings.runs_work_path, *_LEGACY_RUNS_DIRS)
    )
    n_samples = int(len(targets))
    return {
        "active": active,
        "mode": "pointwise",
        "n_samples": n_samples,
        "activation": {
            "movies": {"have": n_movies, "need": pipeline_settings.HEAD_MIN_MOVIES},
            "labels": {"have": n_samples, "need": pipeline_settings.HEAD_MIN_LABELS},
        },
    }


def _validate_library(library: str) -> TasteNamespace:
    """Validate library param and return namespace; raises 400 on unknown value."""
    try:
        return get_namespace(library)
    except ValueError as exc:
        from fastapi import HTTPException  # noqa: PLC0415
        raise HTTPException(status_code=400, detail=f"Unknown library {library!r}; must be 'movies' or 'tv'") from exc


@router.get("/status")
async def taste_status(
    db: Annotated[AsyncSession, Depends(get_db)],
    library: str = "movies",
):
    ns = _validate_library(library)
    registry_state = await artifact_registry.ensure_registry(db)
    summary = feedback_store.summary(ns)

    # Genre spread over labeled movies (v2 rows carry movie_id).
    movie_ids = [int(key) for key in summary["movie_keys"] if key.lstrip("-").isdigit()]
    genres: Counter[str] = Counter()
    if movie_ids:
        rows = (
            (await db.execute(select(Movie.genres).where(Movie.id.in_(movie_ids)))).scalars().all()
        )
        for movie_genres in rows:
            for genre in movie_genres or []:
                genres[genre] += 1

    active_profile = await artifact_registry.active_artifact_summary(
        db, ns.artifact_kind_profile
    )
    active_head = await artifact_registry.active_artifact_summary(
        db, ns.artifact_kind_head
    )
    exemplars = _exemplar_stats(ns)
    if active_profile is not None:
        profile_summary = active_profile.get("summary") or {}
        exemplars["unique_movies"] = profile_summary.get("unique_movies", 0)
        exemplars["duplicate_groups"] = profile_summary.get("duplicate_groups", 0)

    return {
        "library": library,
        "labels": {
            "total": summary["total"],
            "movies": summary["movies"],
            "positives": summary["positives"],
            "negatives": summary["negatives"],
            "genres": dict(genres.most_common()),
        },
        "exemplars": exemplars,
        "learned_head": _head_status(ns),
        "active_profile": active_profile,
        "active_head": active_head,
        "artifact_registry": registry_state,
        "gate_alerts": feedback_store.gate_override_alerts(ns),
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
            "updated_at": now,
            "stage_started_at": now,
            "stage": "queued",
            "message": "Taste profile rebuild queued.",
        }
    )
    return time.monotonic()


def _apply_rebuild_progress(message: dict) -> None:
    now = datetime.now(UTC).isoformat()
    previous_stage = _rebuild_state.get("stage")
    stage = message.get("stage", previous_stage)
    _rebuild_state.update(
        {
            key: message[key]
            for key in (
                "stage",
                "substage",
                "current_item",
                "message",
                "pid",
                "processed",
                "total",
            )
            if key in message
        }
    )
    _rebuild_state["updated_at"] = now
    if stage != previous_stage:
        _rebuild_state["stage_started_at"] = now


def _finish_rebuild(status: str, started_monotonic: float, error: str | None = None) -> None:
    now = datetime.now(UTC).isoformat()
    _rebuild_state.update(
        {
            "status": status,
            "running": False,
            "updated_at": now,
            "finished_at": now,
            "error": error,
            "duration_s": round(time.monotonic() - started_monotonic, 3),
        }
    )


async def _monitor_rebuild_process(process, progress_queue, started_monotonic: float) -> None:
    from marquee.pipeline.run_manager import run_manager  # noqa: PLC0415

    global _active_rebuild_process, _rebuild_cancel_requested
    terminal_status: str | None = None
    terminal_error: str | None = None
    try:
        deadline = started_monotonic + _REBUILD_TIMEOUT_SECONDS
        while True:
            if _rebuild_cancel_requested:
                terminal_status = "cancelled"
                terminal_error = _rebuild_cancel_requested
                if process.is_alive():
                    process.terminate()
                    process.join(timeout=10)
                    if process.is_alive():
                        process.kill()
                        process.join(timeout=10)
                break
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
        _active_rebuild_process = None
        _rebuild_cancel_requested = None


class TasteRetrainRequest(BaseModel):
    # "training_dir" (curated folder, default) | "library" (deployed posters).
    source: str = "training_dir"
    library: str = "movies"


@router.post("/retrain", status_code=202)
async def retrain_taste(
    limiter: Annotated[RateLimiter, Depends(get_rate_limiter)],
    db: Annotated[AsyncSession, Depends(get_db)],
    body: TasteRetrainRequest | None = None,
):
    """Rebuild the taste profile in the background (job platform).

    ``source="training_dir"`` (default) uses the curated positive folder;
    ``source="library"`` rebuilds from every deployed poster.
    ``library="movies"`` (default) | ``"tv"``.
    """
    source = (body.source if body else None) or "training_dir"
    library = (body.library if body else None) or "movies"
    if source not in ("training_dir", "library"):
        raise HTTPException(status_code=400, detail=f"unknown source {source!r}")
    _validate_library(library)  # raises 400 on bad value
    enforce_rate_limit(limiter, "taste_retrain", settings.RATE_TASTE_RETRAIN_SECONDS)
    limiter.record("taste_retrain")
    job = await job_manager.create(
        db=db,
        job_type="taste_rebuild",
        priority=90,
        resources={"gpu": 1},
        subject_type="taste_profile",
        subject_id=library,
        payload={"source": source, "library": library},
        idempotency_key=f"taste-rebuild:{library}:{source}:{int(time.time() // settings.RATE_TASTE_RETRAIN_SECONDS)}",
        max_attempts=1,
    )
    return job_summary(job)


@router.post("/head/retrain", status_code=202)
async def retrain_learned_head(
    limiter: Annotated[RateLimiter, Depends(get_rate_limiter)],
    db: Annotated[AsyncSession, Depends(get_db)],
    library: str = "movies",
):
    """Train the learned head (UI "Key Art Engine") from accumulated labels.

    Picks accumulate labels + exemplars into storage; this is the manual
    trigger that (re)trains the head from them, through the job manager. Cheap
    numpy fit — no GPU reservation.
    """
    _validate_library(library)
    enforce_rate_limit(limiter, "head_retrain", settings.RATE_TASTE_RETRAIN_SECONDS)
    limiter.record("head_retrain")
    job = await job_manager.create(
        db=db,
        job_type="learned_head_train",
        priority=70,
        subject_type="learned_head",
        subject_id=library,
        payload={"library": library},
        idempotency_key=f"head-train:{library}:{int(time.time() // settings.RATE_TASTE_RETRAIN_SECONDS)}",
        max_attempts=1,
    )
    return job_summary(job)


@router.post("/retrain/cancel")
async def cancel_retrain_taste(
    db: Annotated[AsyncSession, Depends(get_db)],
    library: str = "movies",
):
    """Request cancellation through the durable job lifecycle."""
    _validate_library(library)
    job = (
        await db.execute(
            select(Job)
            .where(
                Job.type == "taste_rebuild",
                Job.subject_id == library,
                Job.status.in_(("queued", "waiting_resource", "claimed", "running")),
            )
            .order_by(Job.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if job is None:
        return {"status": "not_running"}
    return job_summary(await job_manager.request_cancel(db, job))


@router.get("/profiles")
async def list_taste_profiles(
    db: Annotated[AsyncSession, Depends(get_db)],
    library: str = "movies",
):
    ns = _validate_library(library)
    registry_state = await artifact_registry.registry_status(db)
    rows = await artifact_registry.list_artifacts(db, ns.artifact_kind_profile)
    return {
        "library": library,
        "profiles": [artifact_registry.artifact_to_summary(row) for row in rows],
        "artifact_registry": registry_state,
    }


@router.get("/profiles/{artifact_id}")
async def get_taste_profile(
    artifact_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    library: str = "movies",
):
    ns = _validate_library(library)
    try:
        return await artifact_registry.artifact_detail(
            db, ns.artifact_kind_profile, artifact_id
        )
    except Exception as exc:  # noqa: BLE001
        raise _artifact_error(exc) from exc


@router.post("/profiles/{artifact_id}/activate")
async def activate_taste_profile(
    artifact_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    library: str = "movies",
):
    from marquee.ml.taste_map import build_map  # noqa: PLC0415

    ns = _validate_library(library)
    try:
        row = await artifact_registry.activate_artifact(
            db, ns.artifact_kind_profile, artifact_id
        )
        await asyncio.to_thread(build_map, namespace=ns)
        return {"profile": artifact_registry.artifact_to_summary(row), "map_rebuilt": True}
    except Exception as exc:  # noqa: BLE001
        raise _artifact_error(exc) from exc


@router.post("/profiles/{artifact_id}/archive")
async def archive_taste_profile(
    artifact_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    try:
        row = await artifact_registry.archive_artifact(
            db, artifact_registry.KIND_TASTE_PROFILE, artifact_id
        )
        return {"profile": artifact_registry.artifact_to_summary(row)}
    except Exception as exc:  # noqa: BLE001
        raise _artifact_error(exc) from exc


@router.delete("/profiles/{artifact_id}")
async def delete_taste_profile(
    artifact_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    try:
        await artifact_registry.delete_artifact(db, artifact_registry.KIND_TASTE_PROFILE, artifact_id)
        return {"deleted": artifact_id}
    except Exception as exc:  # noqa: BLE001
        raise _artifact_error(exc) from exc


@router.get("/profiles/{artifact_id}/exemplars")
async def list_taste_profile_exemplars(
    artifact_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    try:
        return {"exemplars": await artifact_registry.profile_exemplars(db, artifact_id)}
    except Exception as exc:  # noqa: BLE001
        raise _artifact_error(exc) from exc


@router.delete("/profiles/{artifact_id}/exemplars/{name}")
async def delete_taste_profile_exemplar(
    artifact_id: str,
    name: str,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    from marquee.ml.taste_map import build_map  # noqa: PLC0415

    try:
        name = _safe_exemplar_name(name)
        row = await artifact_registry.delete_profile_exemplar(db, artifact_id, name)
        await asyncio.to_thread(build_map)
        return {"profile": artifact_registry.artifact_to_summary(row), "removed": name}
    except Exception as exc:  # noqa: BLE001
        raise _artifact_error(exc) from exc


@router.get("/heads")
async def list_learned_heads(db: Annotated[AsyncSession, Depends(get_db)]):
    registry_state = await artifact_registry.registry_status(db)
    rows = await artifact_registry.list_artifacts(db, artifact_registry.KIND_LEARNED_HEAD)
    return {
        "heads": [artifact_registry.artifact_to_summary(row) for row in rows],
        "artifact_registry": registry_state,
    }


@router.get("/heads/{artifact_id}")
async def get_learned_head(
    artifact_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    try:
        return await artifact_registry.artifact_detail(
            db, artifact_registry.KIND_LEARNED_HEAD, artifact_id
        )
    except Exception as exc:  # noqa: BLE001
        raise _artifact_error(exc) from exc


@router.post("/heads/{artifact_id}/activate")
async def activate_learned_head(
    artifact_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    try:
        row = await artifact_registry.activate_artifact(
            db, artifact_registry.KIND_LEARNED_HEAD, artifact_id
        )
        return {"head": artifact_registry.artifact_to_summary(row)}
    except Exception as exc:  # noqa: BLE001
        raise _artifact_error(exc) from exc


@router.post("/heads/{artifact_id}/archive")
async def archive_learned_head(
    artifact_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    try:
        row = await artifact_registry.archive_artifact(
            db, artifact_registry.KIND_LEARNED_HEAD, artifact_id
        )
        return {"head": artifact_registry.artifact_to_summary(row)}
    except Exception as exc:  # noqa: BLE001
        raise _artifact_error(exc) from exc


@router.delete("/heads/{artifact_id}")
async def delete_learned_head(
    artifact_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    try:
        await artifact_registry.delete_artifact(db, artifact_registry.KIND_LEARNED_HEAD, artifact_id)
        return {"deleted": artifact_id}
    except Exception as exc:  # noqa: BLE001
        raise _artifact_error(exc) from exc


# ---------------------------------------------------------------------------
# Taste map (design 11)
# ---------------------------------------------------------------------------


class CandidateOverlayRequest(BaseModel):
    run_id: str


@router.get("/map")
async def get_taste_map(recompute: bool = False, library: str = "movies"):
    """3D/2D projection of the taste profile, with clusters + outliers."""
    from marquee.ml.taste_map import load_map  # noqa: PLC0415

    ns = _validate_library(library)
    try:
        return load_map(recompute, namespace=ns)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/map/rebuild", status_code=202)
async def rebuild_map(
    limiter: Annotated[RateLimiter, Depends(get_rate_limiter)],
    db: Annotated[AsyncSession, Depends(get_db)],
    library: str = "movies",
):
    """Force a taste-map rebuild in the background."""
    _validate_library(library)
    enforce_rate_limit(limiter, "taste_map_rebuild", settings.RATE_TASTE_MAP_REBUILD_SECONDS)

    limiter.record("taste_map_rebuild")
    job = await job_manager.create(
        db,
        job_type="taste_map",
        priority=50,
        resources={"gpu": 1},
        subject_type="taste_profile",
        subject_id=library,
        payload={"library": library},
        idempotency_key=f"taste-map:{library}:{int(time.time() // settings.RATE_TASTE_MAP_REBUILD_SECONDS)}",
    )
    return job_summary(job)


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

    projections = project(np.stack(embeddings))
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


@router.post("/enrich")
async def enrich_profile(
    limiter: Annotated[RateLimiter, Depends(get_rate_limiter)],
):
    """Run profile enrichment (genres, years, tmdb_ids) and rebuild the map."""
    from marquee.ml.profile_enrich import enrich  # noqa: PLC0415
    from marquee.ml.taste_map import build_map  # noqa: PLC0415

    enforce_rate_limit(limiter, "taste_enrich", settings.RATE_TASTE_ENRICH_SECONDS)
    limiter.record("taste_enrich")
    path = await asyncio.to_thread(enrich)
    result = await asyncio.to_thread(build_map)
    return {"profile_path": str(path), "map_rebuilt": True, **result}


def _safe_exemplar_name(name: str) -> str:
    if "/" in name or "\\" in name or ".." in name:
        raise HTTPException(status_code=400, detail="Invalid exemplar name")
    return name


@router.get("/exemplars/{name}/image")
async def exemplar_image(name: str, size: str = "thumb", library: str = "movies"):
    """Serve an exemplar's thumbnail (webp) or full-size training image."""
    from marquee.ml.taste_map import exemplar_source, thumbnail_path  # noqa: PLC0415

    ns = _validate_library(library)
    name = _safe_exemplar_name(name)
    path = thumbnail_path(name, namespace=ns) if size == "thumb" else exemplar_source(name, namespace=ns)
    if path is None:
        # Fall back to full-size if the thumbnail hasn't been generated.
        path = exemplar_source(name, namespace=ns)
    if path is None:
        raise HTTPException(status_code=404, detail=f"No image for exemplar {name!r}")
    return FileResponse(path)


@router.get("/exemplars/{name}/neighbors")
async def exemplar_neighbors(name: str, library: str = "movies"):
    """The k nearest exemplars to this one (click-to-explore)."""
    from marquee.ml.taste_map import neighbors_of  # noqa: PLC0415

    ns = _validate_library(library)
    name = _safe_exemplar_name(name)
    result = neighbors_of(name, namespace=ns)
    if result is None:
        raise HTTPException(status_code=404, detail=f"Exemplar {name!r} not in profile")
    return {"name": name, "neighbors": result}
