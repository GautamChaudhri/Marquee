"""Cold-start onboarding routes — the guided "Rank Test" (design 20).

Seeds the engine on a fresh install. Most heavy lifting reuses existing jobs:
``taste_rebuild`` (Layer A) and ``learned_head_train`` (Layer B). This router
adds onboarding state/progress, path selection, the stratified sample, and the
bundled taste test. The actual ranking reuses the v4 ``rank`` flow (design 30).
"""

from __future__ import annotations

import logging
import time
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from marquee.api.job_submission import submission_response
from marquee.api.routes.pipeline import _downloaded
from marquee.core.jobs.batches import BatchScope, create_fixed_batch
from marquee.core.jobs.contracts import TriggerKind
from marquee.core.jobs.submission import (
    Initiator,
    SubjectLocator,
    SubmissionIntent,
    submit_job,
)
from marquee.core.pipeline_config import pipeline_settings
from marquee.database import get_db
from marquee.models import Movie
from marquee.onboarding import service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/onboarding", tags=["onboarding"])


class StartRequest(BaseModel):
    path: str | None = None  # "library" | "taste_test" | None → auto-detect


class TasteTestRankRequest(BaseModel):
    movie_id: str
    order: list[str] | None = None
    hated: list[str] | None = None


async def _downloaded_movies(db: AsyncSession) -> list[tuple[int, list[str] | None]]:
    rows = (
        await db.execute(
            select(Movie.id, Movie.genres).where(Movie.tmdb_id.is_not(None), _downloaded())
        )
    ).all()
    return [(row[0], row[1]) for row in rows]


def _idem(tag: str) -> str:
    return f"onboard-{tag}:{int(time.time() // 30)}"


@router.get("/status")
async def onboarding_status():
    return service.status()


@router.post("/start")
async def onboarding_start(
    db: Annotated[AsyncSession, Depends(get_db)],
    body: StartRequest | None = None,
):
    body = body or StartRequest()
    service.ensure_starter_profile()
    downloaded = await _downloaded_movies(db)

    path = body.path
    if path is None:
        if downloaded:
            path = service.PATH_LIBRARY
        elif service.taste_test_available():
            path = service.PATH_TASTE_TEST
        else:
            raise HTTPException(
                status_code=400,
                detail="no downloaded library movies and no taste-test bundle available",
            )

    if path == service.PATH_LIBRARY:
        if not downloaded:
            raise HTTPException(status_code=400, detail="no downloaded movies for the library path")
        service.start(service.PATH_LIBRARY)
        initiator = Initiator(kind="system", identifier="onboarding-api")
        rebuild = await submit_job(
            db,
            job_type="taste_rebuild",
            request={"source": "library", "library": "movies"},
            subject=SubjectLocator(
                kind="model_profile_training", reference="taste_profile:movies"
            ),
            trigger=TriggerKind.MANUAL,
            initiator=initiator,
            idempotency_key=f"taste_rebuild:{_idem('taste-library')}",
            priority=90,
        )
        sample = service.stratified_sample(downloaded, pipeline_settings.ONBOARDING_RANK_TEST_MAX)
        movies = list(
            (
                await db.scalars(
                    select(Movie).where(Movie.id.in_(sample)).order_by(Movie.id)
                )
            ).all()
        )
        nonce = str(int(time.time() * 1_000_000))
        children = [
            SubmissionIntent(
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
                trigger=TriggerKind.BATCH,
                initiator=initiator,
                idempotency_key=f"poster_pipeline:onboard-{nonce}-{movie.id}",
                priority=80,
            )
            for movie in movies
        ]
        batch = await create_fixed_batch(
            db,
            parent_job_type="poster_pipeline_batch",
            parent_request={"scope": "selected", "selection_count": len(children)},
            scope=BatchScope(
                reference=f"onboarding-{nonce}",
                display_name="Onboarding poster analysis",
                summary=f"{len(children)} selected movies",
            ),
            trigger=TriggerKind.BATCH,
            initiator=initiator,
            idempotency_key=f"poster_pipeline_batch:onboard-{nonce}",
            children=children,
            priority=80,
        )
        await db.commit()
        return {
            "path": "library",
            "sample_movie_ids": sample,
            "rebuild_job": submission_response(rebuild),
            "batch_job": submission_response(batch.parent),
            "status": service.status(),
        }

    if path == service.PATH_TASTE_TEST:
        if not service.taste_test_available():
            raise HTTPException(status_code=400, detail="no taste-test bundle is available")
        service.start(service.PATH_TASTE_TEST)
        return {
            "path": "taste_test",
            "movies": service.taste_test_movies(),
            "status": service.status(),
        }

    raise HTTPException(status_code=400, detail=f"unknown onboarding path {path!r}")


@router.get("/taste-test/movies")
async def taste_test_movies():
    if not service.taste_test_available():
        raise HTTPException(status_code=404, detail="no taste-test bundle is available")
    return {"movies": service.taste_test_movies()}


@router.get("/taste-test/posters/{file}")
async def taste_test_poster(file: str):
    from marquee.core.filesystem import boundary_for_roots  # noqa: PLC0415

    path = service.taste_test_image_path(file)
    if path is None:
        raise HTTPException(status_code=404, detail="taste-test poster not found")
    boundary = boundary_for_roots({"taste_test": path.parent}, purpose="taste-test")
    return boundary.response(boundary.classify(path, require_file=True))


@router.post("/taste-test/rank")
async def taste_test_rank(body: TasteTestRankRequest):
    try:
        result = service.taste_test_rank(body.movie_id, body.order or [], body.hated or [])
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {**result, "status": service.status()}


@router.post("/complete")
async def onboarding_complete(db: Annotated[AsyncSession, Depends(get_db)]):
    prog = service.progress()
    if not prog["can_complete"]:
        raise HTTPException(
            status_code=400,
            detail=f"need {prog['min']} ranked movies to complete; have {prog['ranked']}",
        )
    state = service.read_state()
    source = "library" if state.get("path") == service.PATH_LIBRARY else "training_dir"
    initiator = Initiator(kind="system", identifier="onboarding-api")
    rebuild = await submit_job(
        db,
        job_type="taste_rebuild",
        request={"source": source, "library": "movies"},
        subject=SubjectLocator(
            kind="model_profile_training", reference="taste_profile:movies"
        ),
        trigger=TriggerKind.MANUAL,
        initiator=initiator,
        idempotency_key=f"taste_rebuild:{_idem('complete-taste')}",
        priority=90,
    )
    head = await submit_job(
        db,
        job_type="learned_head_train",
        request={"library": "movies"},
        subject=SubjectLocator(
            kind="model_profile_training", reference="learned_head:movies"
        ),
        trigger=TriggerKind.MANUAL,
        initiator=initiator,
        idempotency_key=f"learned_head_train:{_idem('complete-head')}",
        priority=70,
    )
    await db.commit()
    service.mark_complete()
    return {
        "rebuild_job": submission_response(rebuild),
        "head_job": submission_response(head),
        "status": service.status(),
    }
