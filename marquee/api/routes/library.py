"""Library routes — browse media with media-file linkage + subtitle coverage.

Implements the design-03 stubs: each movie/episode resolves to its physical
``media_file_id`` and carries a server-computed subtitle coverage summary (from
the cached inventory when present) so the frontend filters from persisted data
rather than scanning.
"""

from __future__ import annotations

import json
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from marquee.database import get_db
from marquee.models import (
    Episode,
    EpisodeMediaFile,
    MediaFile,
    Movie,
    Season,
    Series,
    SubtitleInventory,
)

router = APIRouter(prefix="/api/library", tags=["library"])


async def _coverage_by_media_file(db: AsyncSession, media_file_ids: list[int]) -> dict[int, dict]:
    if not media_file_ids:
        return {}
    rows = (
        await db.execute(
            select(SubtitleInventory.media_file_id, SubtitleInventory.coverage_json).where(
                SubtitleInventory.media_file_id.in_(media_file_ids)
            )
        )
    ).all()
    return {mid: json.loads(cov) if cov else {} for mid, cov in rows}


@router.get("/movies")
async def list_movies(
    db: Annotated[AsyncSession, Depends(get_db)],
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
):
    """List movies with their active media-file id + subtitle coverage."""
    total = (await db.execute(select(func.count()).select_from(Movie))).scalar_one()
    movies = (
        await db.execute(
            select(Movie).order_by(Movie.title).limit(page_size).offset((page - 1) * page_size)
        )
    ).scalars().all()

    movie_ids = [m.id for m in movies]
    media_rows = (
        await db.execute(
            select(MediaFile).where(
                MediaFile.movie_id.in_(movie_ids), MediaFile.is_active.is_(True)
            )
        )
    ).scalars().all() if movie_ids else []
    mf_by_movie = {mf.movie_id: mf for mf in media_rows}
    coverage = await _coverage_by_media_file(db, [mf.id for mf in media_rows])

    items = []
    for movie in movies:
        mf = mf_by_movie.get(movie.id)
        items.append(
            {
                "id": movie.id,
                "title": movie.title,
                "year": movie.year,
                "tmdb_id": movie.tmdb_id,
                "container": movie.container,
                "media_file_id": mf.id if mf else None,
                "subtitle_coverage": coverage.get(mf.id) if mf else None,
            }
        )
    return {"total": total, "page": page, "page_size": page_size, "items": items}


@router.get("/movies/{movie_id}")
async def get_movie(movie_id: int, db: Annotated[AsyncSession, Depends(get_db)]):
    movie = (await db.execute(select(Movie).where(Movie.id == movie_id))).scalar_one_or_none()
    if movie is None:
        raise HTTPException(status_code=404, detail=f"Movie id={movie_id} not found")
    mf = (
        await db.execute(
            select(MediaFile).where(
                MediaFile.movie_id == movie_id, MediaFile.is_active.is_(True)
            )
        )
    ).scalar_one_or_none()
    coverage = await _coverage_by_media_file(db, [mf.id] if mf else [])
    return {
        "id": movie.id,
        "title": movie.title,
        "year": movie.year,
        "tmdb_id": movie.tmdb_id,
        "genres": movie.genres,
        "container": movie.container,
        "media_file_id": mf.id if mf else None,
        "media_file_path": mf.path if mf else None,
        "subtitle_coverage": coverage.get(mf.id) if mf else None,
    }


@router.get("/series")
async def list_series(
    db: Annotated[AsyncSession, Depends(get_db)],
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
):
    total = (await db.execute(select(func.count()).select_from(Series))).scalar_one()
    rows = (
        await db.execute(
            select(Series).order_by(Series.title).limit(page_size).offset((page - 1) * page_size)
        )
    ).scalars().all()
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": [
            {"id": s.id, "title": s.title, "year": s.year, "season_count": s.season_count}
            for s in rows
        ],
    }


@router.get("/series/{series_id}")
async def get_series(series_id: int, db: Annotated[AsyncSession, Depends(get_db)]):
    series = (await db.execute(select(Series).where(Series.id == series_id))).scalar_one_or_none()
    if series is None:
        raise HTTPException(status_code=404, detail=f"Series id={series_id} not found")
    return {
        "id": series.id,
        "title": series.title,
        "year": series.year,
        "tvdb_id": series.tvdb_id,
        "season_count": series.season_count,
    }


@router.get("/series/{series_id}/seasons")
async def list_seasons(series_id: int, db: Annotated[AsyncSession, Depends(get_db)]):
    rows = (
        await db.execute(
            select(Season).where(Season.series_id == series_id).order_by(Season.season_number)
        )
    ).scalars().all()
    return {
        "series_id": series_id,
        "seasons": [{"id": s.id, "season_number": s.season_number} for s in rows],
    }


@router.get("/episodes/{episode_id}")
async def get_episode(episode_id: int, db: Annotated[AsyncSession, Depends(get_db)]):
    """Episode detail + the shared media-file id (multi-episode files share one)."""
    episode = (
        await db.execute(select(Episode).where(Episode.id == episode_id))
    ).scalar_one_or_none()
    if episode is None:
        raise HTTPException(status_code=404, detail=f"Episode id={episode_id} not found")
    media_file_id = (
        await db.execute(
            select(EpisodeMediaFile.media_file_id).where(
                EpisodeMediaFile.episode_id == episode_id
            )
        )
    ).scalar_one_or_none()
    return {
        "id": episode.id,
        "series_id": episode.series_id,
        "season_number": episode.season_number,
        "episode_number": episode.episode_number,
        "title": episode.title,
        "media_file_id": media_file_id,
    }
