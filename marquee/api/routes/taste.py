"""Taste routes — profile status and full rebuild (design 09 §17.4).

The taste-map endpoints (design 11) are added to this same router later.
"""

from __future__ import annotations

import hashlib
import logging
import time
from collections import Counter
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from marquee.api.deps import enforce_rate_limit, get_rate_limiter
from marquee.api.job_submission import JobSubmissionResponse, submission_response
from marquee.api.routes.jobs import job_summary
from marquee.config import settings
from marquee.core.jobs import control as job_control
from marquee.core.jobs.contracts import TriggerKind
from marquee.core.jobs.submission import (
    IdempotencyConflictError,
    Initiator,
    SubjectLocator,
    SubmissionError,
    submit_job,
)
from marquee.core.pipeline_config import pipeline_settings
from marquee.core.rate_limit import RateLimiter
from marquee.database import get_db
from marquee.ml import artifact_registry, feedback_store
from marquee.ml.namespaces import TasteNamespace, get_namespace
from marquee.models import Job, MlActivePublication, Movie

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/taste", tags=["taste"])

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


async def _canonical_rebuild_status(db: AsyncSession, library: str) -> dict[str, object]:
    job = await db.scalar(
        select(Job)
        .where(
            Job.type.in_(("taste_rebuild", "taste_map", "taste_enrich")),
            Job.subject_reference.in_(
                (
                    f"taste_profile:{library}",
                    f"taste_map:{library}",
                    f"taste_enrichment:{library}",
                )
            ),
        )
        .order_by(Job.created_at.desc())
        .limit(1)
    )
    if job is None:
        return {"status": "idle", "running": False, "job_id": None}
    return {
        "status": job.outcome or job.phase,
        "running": job.phase != "terminal",
        "job_id": job.id,
        "snapshot_url": f"/api/jobs/{job.id}/snapshot",
        "detail_url": f"/projection-room/jobs/{job.id}",
    }

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
        "rebuild": await _canonical_rebuild_status(db, library),
    }


class TasteRetrainRequest(BaseModel):
    # "training_dir" (curated folder, default) | "library" (deployed posters).
    source: str = "training_dir"
    library: str = "movies"


async def _submit_ml_publication(
    db: AsyncSession,
    *,
    job_type: str,
    family: str,
    library: str,
    request: dict[str, object],
    idempotency_key: str,
    priority: int,
) -> JobSubmissionResponse:
    """Snapshot the active generation and submit one canonical publication job."""
    generation = await db.scalar(
        select(MlActivePublication.generation).where(
            MlActivePublication.family == f"{family}:{library}"
        )
    )
    request["expected_generation"] = int(generation or 0)
    request["seed"] = 0
    if db.in_transaction():
        await db.commit()
    try:
        async with db.begin():
            result = await submit_job(
                db,
                job_type=job_type,
                request=request,
                subject=SubjectLocator(
                    kind="model_profile_training",
                    reference=f"{family}:{library}",
                ),
                trigger=TriggerKind.MANUAL,
                initiator=Initiator(kind="system", identifier="taste-api"),
                idempotency_key=idempotency_key,
                priority=priority,
            )
    except IdempotencyConflictError as exc:
        raise HTTPException(status_code=409, detail=exc.api_detail) from exc
    except SubmissionError as exc:
        raise HTTPException(status_code=422, detail=exc.code) from exc
    return submission_response(result)


@router.post("/retrain", status_code=202)
async def retrain_taste(
    limiter: Annotated[RateLimiter, Depends(get_rate_limiter)],
    db: Annotated[AsyncSession, Depends(get_db)],
    body: TasteRetrainRequest | None = None,
) -> JobSubmissionResponse:
    """Submit an immutable taste-profile publication job."""
    source = (body.source if body else None) or "training_dir"
    library = (body.library if body else None) or "movies"
    if source not in ("training_dir", "library"):
        raise HTTPException(status_code=400, detail=f"unknown source {source!r}")
    _validate_library(library)
    enforce_rate_limit(limiter, "taste_retrain", settings.RATE_TASTE_RETRAIN_SECONDS)
    limiter.record("taste_retrain")
    return await _submit_ml_publication(
        db,
        job_type="taste_rebuild",
        family="taste_profile",
        library=library,
        request={"source": source, "library": library},
        idempotency_key=(
            f"taste_rebuild:{library}:{source}:"
            f"{int(time.time() // settings.RATE_TASTE_RETRAIN_SECONDS)}"
        ),
        priority=90,
    )


@router.post("/head/retrain", status_code=202)
async def retrain_learned_head(
    limiter: Annotated[RateLimiter, Depends(get_rate_limiter)],
    db: Annotated[AsyncSession, Depends(get_db)],
    library: str = "movies",
) -> JobSubmissionResponse:
    """Submit an immutable learned-head publication job."""
    _validate_library(library)
    enforce_rate_limit(limiter, "head_retrain", settings.RATE_TASTE_RETRAIN_SECONDS)
    limiter.record("head_retrain")
    return await _submit_ml_publication(
        db,
        job_type="learned_head_train",
        family="learned_head",
        library=library,
        request={
            "library": library,
            "feedback_revision": (
                f"manual:{int(time.time() // settings.RATE_TASTE_RETRAIN_SECONDS)}"
            ),
            "mutation": "manual",
        },
        idempotency_key=(
            f"learned_head_train:{library}:"
            f"{int(time.time() // settings.RATE_TASTE_RETRAIN_SECONDS)}"
        ),
        priority=70,
    )


@router.post("/retrain/cancel")
async def cancel_retrain_taste(
    db: Annotated[AsyncSession, Depends(get_db)],
    library: str = "movies",
):
    """Request cancellation through the canonical durable lifecycle."""
    _validate_library(library)
    job = (
        await db.execute(
            select(Job)
            .where(
                Job.type == "taste_rebuild",
                Job.subject_reference == f"taste_profile:{library}",
                Job.phase.in_(("planned", "queued", "running", "stopping")),
            )
            .order_by(Job.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if job is None:
        return {"status": "not_running"}
    expected_fence_token = job.fence_token
    if db.in_transaction():
        await db.commit()
    result = await job_control.cancel(
        db,
        job_id=job.id,
        expected_fence_token=expected_fence_token,
    )
    return job_summary(result.job)


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
    """Reject manual pointer mutation; publication belongs to the owning job."""
    del artifact_id, db, library
    raise HTTPException(status_code=409, detail="activation_is_job_owned")


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
    try:
        name = _safe_exemplar_name(name)
        row = await artifact_registry.delete_profile_exemplar(db, artifact_id, name)
        successor = await _submit_ml_publication(
            db,
            job_type="taste_map",
            family="taste_map",
            library="movies",
            request={
                "library": "movies",
                "profile_revision": artifact_id,
                "trigger_reference": f"exemplar_deleted:{name}",
            },
            idempotency_key=(
                "taste_map:exemplar-delete:"
                f"{artifact_id}:{hashlib.sha256(name.encode()).hexdigest()[:16]}"
            ),
            priority=50,
        )
        return {
            "profile": artifact_registry.artifact_to_summary(row),
            "removed": name,
            "successor_job": successor.model_dump(mode="json"),
        }
    except HTTPException:
        raise
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
    """Reject manual pointer mutation; publication belongs to the owning job."""
    del artifact_id, db
    raise HTTPException(status_code=409, detail="activation_is_job_owned")


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
async def get_taste_map(library: str = "movies"):
    """Return the last published taste-map artifact without recomputation."""
    from marquee.ml.taste_map import load_map  # noqa: PLC0415

    ns = _validate_library(library)
    try:
        return load_map(False, namespace=ns)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/map/rebuild", status_code=202)
async def rebuild_map(
    limiter: Annotated[RateLimiter, Depends(get_rate_limiter)],
    db: Annotated[AsyncSession, Depends(get_db)],
    library: str = "movies",
) -> JobSubmissionResponse:
    """Submit an immutable taste-map publication job."""
    _validate_library(library)
    enforce_rate_limit(limiter, "taste_map_rebuild", settings.RATE_TASTE_MAP_REBUILD_SECONDS)
    limiter.record("taste_map_rebuild")
    return await _submit_ml_publication(
        db,
        job_type="taste_map",
        family="taste_map",
        library=library,
        request={"library": library},
        idempotency_key=(
            f"taste_map:{library}:"
            f"{int(time.time() // settings.RATE_TASTE_MAP_REBUILD_SECONDS)}"
        ),
        priority=50,
    )


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


@router.post("/enrich", status_code=202)
async def enrich_profile(
    limiter: Annotated[RateLimiter, Depends(get_rate_limiter)],
    db: Annotated[AsyncSession, Depends(get_db)],
    library: str = "movies",
) -> JobSubmissionResponse:
    """Submit immutable taste-profile enrichment publication."""
    _validate_library(library)
    enforce_rate_limit(limiter, "taste_enrich", settings.RATE_TASTE_ENRICH_SECONDS)
    limiter.record("taste_enrich")
    return await _submit_ml_publication(
        db,
        job_type="taste_enrich",
        family="taste_enrichment",
        library=library,
        request={"library": library},
        idempotency_key=(
            f"taste_enrich:{library}:"
            f"{int(time.time() // settings.RATE_TASTE_ENRICH_SECONDS)}"
        ),
        priority=50,
    )


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
    from marquee.core.filesystem import boundary_for_roots  # noqa: PLC0415

    boundary = boundary_for_roots({"exemplar": path.parent}, purpose="taste-exemplar")
    return boundary.response(boundary.classify(path, require_file=True))


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
