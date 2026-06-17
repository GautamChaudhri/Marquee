"""Inspectable movie-only endpoint for the revised poster pipeline.

This is a thin wrapper over ``marquee.pipeline.runner`` kept for curl-based
workflows. The production path is ``POST /api/pipeline/movie/{id}/run`` (live
SSE progress + run history). The actual stage logic lives in the runner; this
route builds a one-off ``FeatureExtractor`` and runs the stages inline so it
stays independent of the ``RunManager`` singleton.

``_EXPERIMENTS_DATA`` and ``_clear_generated_outputs`` are re-exported here
because the test suite imports them from this module.
"""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from marquee.api.deps import enforce_rate_limit, get_rate_limiter, get_tmdb
from marquee.config import settings
from marquee.core.poster_sources.tmdb import TMDBClient
from marquee.core.rate_limit import RateLimiter
from marquee.database import get_db
from marquee.models import Movie
from marquee.pipeline.features import FeatureExtractor
from marquee.pipeline.runner import (  # noqa: F401 — re-exported for tests
    _EXPERIMENTS_DATA,
    _add_run_file_handler,
    _clear_generated_outputs,
    _remove_run_file_handler,
    _sanitise_filename,
    build_run_payload,
    fetch_and_download,
    place_outputs,
    run_sync_stages,
    write_run_json,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/test", tags=["test"])


@router.post("/pipeline/movie/{movie_id}")
async def test_pipeline_movie(
    movie_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    tmdb: Annotated[TMDBClient, Depends(get_tmdb)],
    limiter: Annotated[RateLimiter, Depends(get_rate_limiter)],
):
    """Run Fetch -> SHA -> Gate(res) -> Style -> Gate(style) -> OCR -> pHash -> Detail -> Gate -> Rank -> Output."""
    movie = (
        await db.execute(select(Movie).where(Movie.id == movie_id))
    ).scalar_one_or_none()
    if movie is None:
        movies = (await db.execute(select(Movie).order_by(Movie.id))).scalars().all()
        if not movies:
            raise HTTPException(
                status_code=400,
                detail="No movies in database - run POST /api/sync/all first",
            )
        raise HTTPException(
            status_code=404,
            detail=(
                f"Movie id={movie_id} not found. Database has {len(movies)} movies "
                f"(id range: {movies[0].id}-{movies[-1].id})."
            ),
        )
    if movie.tmdb_id is None:
        raise HTTPException(
            status_code=400,
            detail=f"Movie {movie.title!r} has no TMDB ID - run sync first",
        )

    # Refuse to add a second GPU job while a run or taste rebuild is resident
    # (this legacy endpoint builds its own extractor, so guard it explicitly).
    from marquee.pipeline.run_manager import run_manager  # noqa: PLC0415

    busy = run_manager.gpu_busy()
    if busy is not None:
        raise HTTPException(
            status_code=409, detail=f"GPU is busy ({busy}) — try again when it's idle"
        )

    enforce_rate_limit(limiter, f"pipeline:{movie_id}", settings.RATE_PIPELINE_RUN_SECONDS)
    limiter.record(f"pipeline:{movie_id}")

    run_started = time.perf_counter()
    started_at = datetime.now(UTC).isoformat()
    out_dir = _EXPERIMENTS_DATA / _sanitise_filename(movie.title)
    out_dir.mkdir(parents=True, exist_ok=True)
    _clear_generated_outputs(out_dir)
    originals_dir = out_dir / "0-originals"
    originals_dir.mkdir(parents=True, exist_ok=True)
    log_path = out_dir / "pipeline.log"
    json_path = out_dir / "pipeline_run.json"
    file_handler = _add_run_file_handler(log_path)

    timings: dict[str, float] = {}

    try:
        logger.info("=" * 80)
        logger.info("RUN START | movie=%s | movie_id=%d", movie.title, movie.id)

        fetch = await fetch_and_download(
            tmdb=tmdb, movie=movie, originals_dir=originals_dir, timings=timings
        )
        records = fetch.records

        import asyncio  # noqa: PLC0415

        extractor = FeatureExtractor()
        extractor.preflight()
        outcome = await asyncio.to_thread(
            run_sync_stages,
            movie_title=movie.title,
            out_dir=out_dir,
            records=records,
            candidate_map=fetch.candidate_map,
            all_files=fetch.all_files,
            resolution_by_name=fetch.resolution_by_name,
            timings=timings,
            feature_extractor=extractor,
            primary_name=fetch.primary_name,
        )
        status = outcome.status
        ranked = outcome.ranked

        output_result = None
        if ranked:
            output_result = await place_outputs(
                ranked,
                candidate_map=fetch.candidate_map,
                out_dir=out_dir,
                timings=timings,
            )

        total_duration = time.perf_counter() - run_started
        write_run_json(
            json_path,
            build_run_payload(
                movie=movie,
                started_at=started_at,
                status=status,
                timings=timings,
                records=records,
                total_duration=total_duration,
            ),
        )
        logger.info("RUN END | output=%s", out_dir)
        logger.info("=" * 80)

        return {
            "movie_id": movie.id,
            "title": movie.title,
            "tmdb_id": movie.tmdb_id,
            "status": status,
            "output_dir": str(out_dir),
            "pipeline_log": str(log_path),
            "pipeline_run_json": str(json_path),
            "total_duration_s": round(total_duration, 3),
            "stage_timings_s": timings,
            "fetch": fetch.counts,
            "counts": {**fetch.counts, **outcome.counts},
            "top5": [
                {
                    "rank": record.rank,
                    "score": record.final_score,
                    "original_file": record.orig_filename,
                    "output_file": record.image_path.name,
                    "original_download": record.original_download,
                }
                for record in ranked[:5]
            ],
            "original_download_errors": (
                output_result.download_errors if output_result else []
            ),
        }
    except HTTPException:
        raise
    except Exception as exc:
        total_duration = time.perf_counter() - run_started
        logger.exception("RUN FAILED | systemic_error=%s", exc)
        write_run_json(
            json_path,
            build_run_payload(
                movie=movie,
                started_at=started_at,
                status="failed",
                timings=timings,
                records={},
                total_duration=total_duration,
                error=str(exc),
            ),
        )
        raise HTTPException(
            status_code=500,
            detail=f"Pipeline infrastructure failure: {exc}",
        ) from exc
    finally:
        _remove_run_file_handler(file_handler)
