"""Taste routes — profile status and full rebuild (design 09 §17.4).

The taste-map endpoints (design 11) are added to this same router later.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import Counter
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
from marquee.core.jobs.ml_publication import MlPublicationError, resolve_active_publication
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
from marquee.ml import feedback_store, publication_catalog
from marquee.ml.namespaces import TasteNamespace, get_namespace
from marquee.models import Job, MlActivePublication, Movie

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/taste", tags=["taste"])


def _artifact_error(exc: Exception) -> HTTPException:
    if isinstance(exc, FileNotFoundError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, RuntimeError):
        return HTTPException(status_code=409, detail=str(exc))
    return HTTPException(status_code=400, detail=str(exc))


def _exemplar_stats(active_profile: dict[str, object] | None) -> dict[str, object]:
    summary = active_profile.get("summary", {}) if isinstance(active_profile, dict) else {}
    if not isinstance(summary, dict):
        summary = {}
    return {
        "count": int(summary.get("exemplars", 0)),
        "negatives": int(summary.get("negative_exemplars", 0)),
        "last_rebuild": (
            active_profile.get("activated_at") if isinstance(active_profile, dict) else None
        ),
        "profile_present": active_profile is not None,
        "unique_movies": int(summary.get("unique_movies", 0)),
        "duplicate_groups": int(summary.get("duplicate_groups", 0)),
        "by_kind": summary.get("by_kind", {}),
    }


def _head_status(ns: TasteNamespace, *, active: bool) -> dict:
    """Activation progress for the learned head, in whichever training mode is
    active. Keeps a stable shape (``n_samples`` + ``activation.{movies,labels}``)
    so the UI is mode-agnostic; ``mode`` tells it whether the unit is pairs or
    labels. In pairwise mode ``n_samples``/``labels`` carry the derived
    within-movie preference-pair counts."""
    from marquee.ml.head_trainer import (  # noqa: PLC0415
        build_inversion_training_data,
        build_training_data,
    )

    rows = feedback_store.read_all(ns)

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

    _x, targets, _names, n_movies = build_training_data(rows)
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

        raise HTTPException(
            status_code=400, detail=f"Unknown library {library!r}; must be 'movies' or 'tv'"
        ) from exc


async def _canonical_rebuild_status(db: AsyncSession, library: str) -> dict[str, object]:
    job = await db.scalar(
        select(Job)
        .where(
            Job.type.in_(("taste_rebuild", "taste_map", "taste_enrich")),
            Job.subject_reference.in_(
                (
                    f"taste_profile:{library}",
                    f"taste_map:{library}",
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


async def _publication_summaries(
    db: AsyncSession, *, kind: str, library: str
) -> list[dict[str, object]]:
    entries = await publication_catalog.list_entries(db, kind=kind, library=library)
    return [await publication_catalog.artifact_summary(entry) for entry in entries]


@router.get("/status")
async def taste_status(
    db: Annotated[AsyncSession, Depends(get_db)],
    library: str = "movies",
):
    ns = _validate_library(library)
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

    profiles = await _publication_summaries(db, kind="taste_profile", library=library)
    heads = await _publication_summaries(db, kind="learned_head", library=library)
    active_profile = next((item for item in profiles if item["status"] == "active"), None)
    active_head = next((item for item in heads if item["status"] == "active"), None)
    exemplars = _exemplar_stats(active_profile)

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
        "learned_head": _head_status(ns, active=active_head is not None),
        "active_profile": active_profile,
        "active_head": active_head,
        "publication_authority": publication_catalog.CATALOG_STATUS,
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
    _validate_library(library)
    return {
        "library": library,
        "profiles": await _publication_summaries(db, kind="taste_profile", library=library),
        "publication_authority": publication_catalog.CATALOG_STATUS,
    }


@router.get("/profiles/{artifact_id}")
async def get_taste_profile(
    artifact_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    library: str = "movies",
):
    _validate_library(library)
    try:
        entry = await publication_catalog.get_entry(
            db, kind="taste_profile", library=library, artifact_id=artifact_id
        )
        return await publication_catalog.artifact_detail(entry)
    except Exception as exc:  # noqa: BLE001
        raise _artifact_error(exc) from exc


@router.get("/profiles/{artifact_id}/exemplars")
async def list_taste_profile_exemplars(
    artifact_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    library: str = "movies",
):
    try:
        _validate_library(library)
        entry = await publication_catalog.get_entry(
            db, kind="taste_profile", library=library, artifact_id=artifact_id
        )
        return {"exemplars": await publication_catalog.profile_exemplars(entry)}
    except Exception as exc:  # noqa: BLE001
        raise _artifact_error(exc) from exc


@router.get("/heads")
async def list_learned_heads(
    db: Annotated[AsyncSession, Depends(get_db)],
    library: str = "movies",
):
    _validate_library(library)
    return {
        "heads": await _publication_summaries(db, kind="learned_head", library=library),
        "publication_authority": publication_catalog.CATALOG_STATUS,
    }


@router.get("/heads/{artifact_id}")
async def get_learned_head(
    artifact_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    library: str = "movies",
):
    try:
        _validate_library(library)
        entry = await publication_catalog.get_entry(
            db, kind="learned_head", library=library, artifact_id=artifact_id
        )
        return await publication_catalog.artifact_detail(entry)
    except Exception as exc:  # noqa: BLE001
        raise _artifact_error(exc) from exc


# ---------------------------------------------------------------------------
# Taste map (design 11)
# ---------------------------------------------------------------------------


@router.get("/map")
async def get_taste_map(
    db: Annotated[AsyncSession, Depends(get_db)],
    library: str = "movies",
):
    """Return the last published taste-map artifact without recomputation."""
    from marquee.ml.taste_map import load_map  # noqa: PLC0415

    ns = _validate_library(library)
    try:
        active = await resolve_active_publication(db, family=f"taste_map:{library}")
        return load_map(False, namespace=ns, path=active.path)
    except (FileNotFoundError, MlPublicationError) as exc:
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
            f"taste_map:{library}:{int(time.time() // settings.RATE_TASTE_MAP_REBUILD_SECONDS)}"
        ),
        priority=50,
    )


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
        family="taste_profile",
        library=library,
        request={"library": library},
        idempotency_key=(
            f"taste_enrich:{library}:{int(time.time() // settings.RATE_TASTE_ENRICH_SECONDS)}"
        ),
        priority=50,
    )


def _safe_exemplar_name(name: str) -> str:
    if "/" in name or "\\" in name or ".." in name:
        raise HTTPException(status_code=400, detail="Invalid exemplar name")
    return name


@router.get("/exemplars/{name}/neighbors")
async def exemplar_neighbors(
    name: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    library: str = "movies",
):
    """The k nearest exemplars to this one (click-to-explore)."""
    from marquee.ml.taste_map import neighbors_of  # noqa: PLC0415

    ns = _validate_library(library)
    name = _safe_exemplar_name(name)
    try:
        active = await resolve_active_publication(db, family=f"taste_profile:{library}")
    except (FileNotFoundError, MlPublicationError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    result = await asyncio.to_thread(
        neighbors_of,
        name,
        namespace=ns,
        profile_path=active.path,
    )
    if result is None:
        raise HTTPException(status_code=404, detail=f"Exemplar {name!r} not in profile")
    return {"name": name, "neighbors": result}
