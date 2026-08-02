"""Taste routes — profile status and full rebuild (design 09 §17.4).

The taste-map endpoints (design 11) are added to this same router later.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import Counter
from typing import Annotated, Literal

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
from marquee.core.taste_preferences import (
    TastePreferenceError,
    derive_readiness,
    schedule_profile_builds,
)
from marquee.database import get_db
from marquee.ml import publication_catalog
from marquee.ml.namespaces import TasteNamespace, get_namespace
from marquee.models import Job, MlActivePublication, Movie, PosterPreferenceEvent

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


async def _residual_status(db: AsyncSession, *, library: str, active: bool) -> dict:
    """Derive bounded residual eligibility from canonical database events."""
    from marquee.ml.residual import build_residual_pairs  # noqa: PLC0415
    from marquee.models import PosterPreferenceEvent  # noqa: PLC0415

    events = list(
        (
            await db.scalars(
                select(PosterPreferenceEvent)
                .where(PosterPreferenceEvent.namespace == library)
                .order_by(PosterPreferenceEvent.created_at, PosterPreferenceEvent.id)
                .limit(100_000)
            )
        ).all()
    )
    pairs = build_residual_pairs(events)
    subjects = {pair.subject for pair in pairs}
    # The thresholds come from the same settings the trainer gates on, so the gauges
    # cannot drift from the gate when an operator retunes either knob.
    return {
        "active": active,
        "mode": "bounded_residual",
        "subjects": len(subjects),
        "pairs": len(pairs),
        "activation": {
            "subjects": {
                "have": len(subjects),
                "need": pipeline_settings.RESIDUAL_MIN_SUBJECTS,
            },
            "pairs": {"have": len(pairs), "need": pipeline_settings.RESIDUAL_MIN_PAIRS},
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
    readiness = (await derive_readiness(db)).to_dict()
    status = readiness["libraries"][library]
    build = status["build"]
    job_id = build["job_id"]
    return {
        "status": build["state"] or "idle",
        "running": build["state"] in {"queued", "running"},
        "job_id": job_id,
        "snapshot_url": f"/api/jobs/{job_id}/snapshot" if job_id is not None else None,
        "detail_url": f"/projection-room/jobs/{job_id}" if job_id is not None else None,
        "active": status["active"],
        "desired_revision": status["desired_revision"],
        "desired_generation": status["desired_generation"],
        "reload_state": status["reload_state"],
        "rebuild_due": status["rebuild_due"],
        "update_attention": status["update_attention"],
    }


async def _subject_resolver(db: AsyncSession, *, kind: str, library: str):
    """Exemplar-id → title index, built once per request and only where it can help.

    Only taste profiles carry per-poster filenames, and only frozen-evidence builds
    name them after exemplar ids, so a residual read never pays for this.
    """
    if kind != "taste_profile":
        return None
    return await publication_catalog.subject_name_index(db, library=library)


async def _publication_summaries(
    db: AsyncSession, *, kind: str, library: str
) -> list[dict[str, object]]:
    entries = await publication_catalog.list_entries(db, kind=kind, library=library)
    resolver = await _subject_resolver(db, kind=kind, library=library)
    return [
        await publication_catalog.artifact_summary(entry, resolver=resolver) for entry in entries
    ]


@router.get("/status")
async def taste_status(
    db: Annotated[AsyncSession, Depends(get_db)],
    library: str = "movies",
):
    _validate_library(library)
    events = list(
        (
            await db.scalars(
                select(PosterPreferenceEvent)
                .where(PosterPreferenceEvent.namespace == library)
                .order_by(PosterPreferenceEvent.created_at, PosterPreferenceEvent.id)
                .limit(100_000)
            )
        ).all()
    )
    revoked = {event.revoked_event_id for event in events if event.revoked_event_id}
    active_events = [
        event for event in events if event.id not in revoked and event.action != "undo"
    ]
    subject_references = {event.subject_reference for event in active_events}
    positives = sum(
        event.action in {"approval", "selection", "override", "rank"} for event in active_events
    )
    negatives = sum(event.action in {"hate", "reject", "reject_all"} for event in active_events)

    # Genre spread over labeled movies (v2 rows carry movie_id).
    #
    # Movies only, deliberately. A TV subject_reference is a series id, and `series`
    # has no genres column — joining it against `movies` here would silently match
    # series ids to unrelated films and report their genres as the operator's taste.
    # Re-adding the join is not the fix; a genre source for series would be.
    genres: Counter[str] = Counter()
    genres_available = library == "movies"
    if genres_available:
        movie_ids = [int(key) for key in subject_references if key.lstrip("-").isdigit()]
        if movie_ids:
            rows = (
                (await db.execute(select(Movie.genres).where(Movie.id.in_(movie_ids))))
                .scalars()
                .all()
            )
            for movie_genres in rows:
                for genre in movie_genres or []:
                    genres[genre] += 1

    profiles = await _publication_summaries(db, kind="taste_profile", library=library)
    residuals = await _publication_summaries(db, kind="ranking_residual", library=library)
    active_profile = next((item for item in profiles if item["status"] == "active"), None)
    active_residual = next((item for item in residuals if item["status"] == "active"), None)
    exemplars = _exemplar_stats(active_profile)

    return {
        "library": library,
        "labels": {
            "total": positives + negatives,
            # `movies` is the legacy name for this count; it has always been distinct
            # subjects, which for the TV namespace are series. `subjects` is the same
            # number under the name it earned.
            "movies": len(subject_references),
            "subjects": len(subject_references),
            "positives": positives,
            "negatives": negatives,
            "genres": dict(genres.most_common()),
            "genres_available": genres_available,
        },
        "exemplars": exemplars,
        "ranking_residual": await _residual_status(
            db, library=library, active=active_residual is not None
        ),
        "active_profile": active_profile,
        "active_residual": active_residual,
        "publication_authority": publication_catalog.CATALOG_STATUS,
        "gate_alerts": [],
        "rebuild": await _canonical_rebuild_status(db, library),
    }


class TasteRetrainRequest(BaseModel):
    """Manually request a profile rebuild for one library.

    The training source normally follows from the library rather than the caller:
    movies rebuild from frozen preference evidence through the coordinator, TV
    rebuilds by scanning artwork already deployed in the library. The seeding
    bundle is the one temporary exception.
    """

    library: Literal["movies", "tv"] = "movies"
    # TEMPORARY (seeding bundle): the one case where the caller does pick the source —
    # bootstrapping the movies profile from curated posters before enough canonical
    # evidence exists. Remove with the staging branch in handlers_ml.
    source: Literal["canonical", "seeding_bundle"] = "canonical"


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
    """Submit a taste profile rebuild: coordinated for movies, on demand for TV."""
    library = body.library if body else "movies"
    _validate_library(library)
    enforce_rate_limit(limiter, "taste_retrain", settings.RATE_TASTE_RETRAIN_SECONDS)
    limiter.record("taste_retrain")
    if body is not None and body.source == "seeding_bundle":  # TEMPORARY (seeding bundle)
        if library != "movies":
            raise HTTPException(
                status_code=400,
                detail="the seeding bundle only builds the movies profile",
            )
        return await _submit_ml_publication(
            db,
            job_type="taste_rebuild",
            family="taste_profile",
            library="movies",
            request={"source": "seeding_bundle", "library": "movies"},
            idempotency_key=(
                f"taste_rebuild:movies:seeding:"
                f"{int(time.time() // settings.RATE_TASTE_RETRAIN_SECONDS)}"
            ),
            priority=90,
        )
    if library == "tv":
        # TV trains on artwork already deployed in the library rather than on
        # recorded preference evidence, so there is no revision to coalesce against
        # and no coordinator to ask. Pressing the button is the whole trigger.
        return await _submit_ml_publication(
            db,
            job_type="taste_rebuild",
            family="taste_profile",
            library=library,
            request={"source": "library", "library": library},
            idempotency_key=(
                f"taste_rebuild:tv:library:"
                f"{int(time.time() // settings.RATE_TASTE_RETRAIN_SECONDS)}"
            ),
            priority=90,
        )
    try:
        submissions = await schedule_profile_builds(
            db,
            namespaces=(library,),
            initiator_identifier="taste-api-manual",
            force=True,
            coalesce_threshold=1,
        )
        await db.commit()
    except TastePreferenceError as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if not submissions:
        raise HTTPException(
            status_code=409,
            detail="canonical taste evidence is not due for a new profile build",
        )
    return submission_response(submissions[0])


@router.post("/residual/retrain", status_code=202)
async def retrain_ranking_residual(
    limiter: Annotated[RateLimiter, Depends(get_rate_limiter)],
    db: Annotated[AsyncSession, Depends(get_db)],
    library: str = "movies",
) -> JobSubmissionResponse:
    """Submit held-out evaluation of an immutable bounded residual candidate."""
    _validate_library(library)
    enforce_rate_limit(limiter, "residual_retrain", settings.RATE_TASTE_RETRAIN_SECONDS)
    limiter.record("residual_retrain")
    from marquee.ml.residual import freeze_residual_evidence  # noqa: PLC0415

    events = list(
        (
            await db.scalars(
                select(PosterPreferenceEvent)
                .where(PosterPreferenceEvent.namespace == library)
                .order_by(PosterPreferenceEvent.created_at, PosterPreferenceEvent.id)
                .limit(100_000)
            )
        ).all()
    )
    frozen_evidence = freeze_residual_evidence(events)
    return await _submit_ml_publication(
        db,
        job_type="ranking_residual_train",
        family="ranking_residual",
        library=library,
        request={
            "library": library,
            "evidence_revision": frozen_evidence.digest,
            "mutation": "manual",
        },
        idempotency_key=(
            f"ranking_residual_train:{library}:"
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
        resolver = await _subject_resolver(db, kind="taste_profile", library=library)
        return await publication_catalog.artifact_detail(entry, resolver=resolver)
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
        resolver = await _subject_resolver(db, kind="taste_profile", library=library)
        return {"exemplars": await publication_catalog.profile_exemplars(entry, resolver=resolver)}
    except Exception as exc:  # noqa: BLE001
        raise _artifact_error(exc) from exc


@router.get("/residuals")
async def list_ranking_residuals(
    db: Annotated[AsyncSession, Depends(get_db)],
    library: str = "movies",
):
    _validate_library(library)
    return {
        "residuals": await _publication_summaries(db, kind="ranking_residual", library=library),
        "publication_authority": publication_catalog.CATALOG_STATUS,
    }


@router.get("/residuals/{artifact_id}")
async def get_ranking_residual(
    artifact_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    library: str = "movies",
):
    try:
        _validate_library(library)
        entry = await publication_catalog.get_entry(
            db, kind="ranking_residual", library=library, artifact_id=artifact_id
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
