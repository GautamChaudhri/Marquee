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
from fastapi.responses import FileResponse
from sqlalchemy import exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from marquee.api.library_serializers import (
    enrich_movie,
    hdr_filter,
    poster_status_filter,
)
from marquee.database import get_db
from marquee.models import (
    Episode,
    EpisodeMediaFile,
    LetterboxState,
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
    q: str | None = Query(None, description="Case-insensitive title search"),
    poster_status: str | None = Query(
        None, description="Filter: missing | review | approved | deployed"
    ),
    hdr: str | None = Query(None, description="Filter: dovi | hdr10 | sdr | unknown"),
    letterbox_status: str | None = Query(
        None, description="Filter on LetterboxState.status, or 'none' for unanalyzed"
    ),
    include_unavailable: bool = Query(
        False, description="Include Radarr movies that do not have a downloaded file yet."
    ),
    sort: str = Query("title", description="Sort: title | year"),
):
    """List movies with media-file id, subtitle coverage, and derived display fields.

    Optional filters map to real columns. ``subtitle_status`` is derived from
    JSON coverage and is intentionally *not* a server-side filter — the frontend
    filters subtitle gaps within the returned page.
    """
    base = select(Movie, LetterboxState).outerjoin(
        LetterboxState, LetterboxState.movie_id == Movie.id
    )

    conditions = []
    if not include_unavailable:
        has_active_media = exists(
            select(MediaFile.id).where(
                MediaFile.movie_id == Movie.id,
                MediaFile.is_active.is_(True),
            )
        )
        conditions.append(or_(Movie.movie_file_path.is_not(None), has_active_media))
    if q:
        conditions.append(Movie.title.ilike(f"%{q}%"))
    if poster_status and (pred := poster_status_filter(poster_status)) is not None:
        conditions.append(pred)
    if hdr and (pred := hdr_filter(hdr)) is not None:
        conditions.append(pred)
    if letterbox_status == "none":
        conditions.append(LetterboxState.id.is_(None))
    elif letterbox_status:
        conditions.append(LetterboxState.status == letterbox_status)
    if conditions:
        base = base.where(*conditions)

    total = (
        await db.execute(select(func.count()).select_from(base.subquery()))
    ).scalar_one()

    if sort == "year":
        order_col = Movie.year.desc()
    elif sort == "added":
        order_col = Movie.created_at.desc()
    else:
        order_col = Movie.title
    rows = (
        await db.execute(
            base.order_by(order_col).limit(page_size).offset((page - 1) * page_size)
        )
    ).all()

    movies = [m for m, _ in rows]
    lb_by_movie = {m.id: lb for m, lb in rows}
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
        lb = lb_by_movie.get(movie.id)
        items.append(
            enrich_movie(
                movie,
                mf,
                coverage.get(mf.id) if mf else None,
                lb.status if lb else None,
            )
        )
    return {"total": total, "page": page, "page_size": page_size, "items": items}


@router.get("/movies/{movie_id}/poster")
async def get_movie_poster(movie_id: int, db: Annotated[AsyncSession, Depends(get_db)]):
    movie = (await db.execute(select(Movie).where(Movie.id == movie_id))).scalar_one_or_none()
    if movie is None or not movie.poster_path:
        raise HTTPException(status_code=404, detail="No poster available")
    import os
    if not os.path.isfile(movie.poster_path):
        raise HTTPException(status_code=404, detail="Poster file not found on disk")
    return FileResponse(movie.poster_path, media_type="image/jpeg")


@router.get("/movies/{movie_id}")
async def get_movie(movie_id: int, db: Annotated[AsyncSession, Depends(get_db)]):
    row = (
        await db.execute(
            select(Movie, LetterboxState)
            .outerjoin(LetterboxState, LetterboxState.movie_id == Movie.id)
            .where(Movie.id == movie_id)
        )
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail=f"Movie id={movie_id} not found")
    movie, lb = row
    mf = (
        await db.execute(
            select(MediaFile).where(
                MediaFile.movie_id == movie_id, MediaFile.is_active.is_(True)
            )
        )
    ).scalar_one_or_none()
    coverage = await _coverage_by_media_file(db, [mf.id] if mf else [])
    item = enrich_movie(
        movie, mf, coverage.get(mf.id) if mf else None, lb.status if lb else None
    )
    item["media_file_path"] = mf.path if mf else None
    return item


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
