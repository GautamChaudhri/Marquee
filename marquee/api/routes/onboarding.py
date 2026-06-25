"""Cold-start onboarding routes — the guided "Rank Test" (design 20).

Seeds the engine on a fresh install. Most heavy lifting reuses existing jobs:
``taste_rebuild`` (Layer A) and ``learned_head_train`` (Layer B). This router
adds onboarding state/progress, path selection, the stratified sample, and the
bundled taste test. The actual ranking reuses the v3 ``rank`` flow.
"""

from __future__ import annotations

import logging
import time
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from marquee.api.routes.jobs import job_summary
from marquee.api.routes.pipeline import _downloaded
from marquee.core.jobs import job_manager
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
    favorites: list[list[str]] | None = None
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
        rebuild = await job_manager.create(
            db,
            job_type="taste_rebuild",
            payload={"source": "library"},
            priority=90,
            resources={"gpu": 1},
            subject_type="taste_profile",
            subject_id="default",
            max_attempts=1,
            idempotency_key=_idem("taste-library"),
        )
        sample = service.stratified_sample(downloaded, pipeline_settings.ONBOARDING_RANK_TEST_MAX)
        batch = await job_manager.create(
            db,
            job_type="poster_pipeline_batch",
            payload={"movie_ids": sample, "scope": "selected"},
            priority=80,
            resources={"gpu": 1, "network_external": 1},
            subject_type="pipeline_batch",
            subject_id="onboarding",
            max_attempts=1,
            idempotency_key=_idem("batch"),
        )
        return {
            "path": "library",
            "sample_movie_ids": sample,
            "rebuild_job": job_summary(rebuild),
            "batch_job": job_summary(batch),
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
    path = service.taste_test_image_path(file)
    if path is None:
        raise HTTPException(status_code=404, detail="taste-test poster not found")
    return FileResponse(path)


@router.post("/taste-test/rank")
async def taste_test_rank(body: TasteTestRankRequest):
    try:
        result = service.taste_test_rank(body.movie_id, body.favorites or [], body.hated or [])
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
    rebuild = await job_manager.create(
        db,
        job_type="taste_rebuild",
        payload={"source": source},
        priority=90,
        resources={"gpu": 1},
        subject_type="taste_profile",
        subject_id="default",
        max_attempts=1,
        idempotency_key=_idem("complete-taste"),
    )
    head = await job_manager.create(
        db,
        job_type="learned_head_train",
        priority=70,
        subject_type="learned_head",
        subject_id="default",
        max_attempts=1,
        idempotency_key=_idem("complete-head"),
    )
    service.mark_complete()
    return {
        "rebuild_job": job_summary(rebuild),
        "head_job": job_summary(head),
        "status": service.status(),
    }
