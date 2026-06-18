"""HDR coverage read model for the frontend HDR page."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from marquee.api.library_serializers import enrich_movie, hdr_filter
from marquee.api.routes.library import _coverage_by_media_file
from marquee.database import get_db
from marquee.models import LetterboxState, MediaFile, Movie

router = APIRouter(prefix="/api/hdr", tags=["hdr"])


async def _distribution(db: AsyncSession) -> dict[str, int]:
    counts = {
        "dovi": (
            await db.execute(select(func.count()).where(Movie.has_dv.is_(True)))
        ).scalar_one(),
        "hdr10": (
            await db.execute(
                select(func.count()).where(
                    and_(Movie.has_hdr.is_(True), Movie.has_dv.isnot(True))
                )
            )
        ).scalar_one(),
        "sdr": (
            await db.execute(
                select(func.count()).where(
                    and_(Movie.has_hdr.is_(False), Movie.has_dv.isnot(True))
                )
            )
        ).scalar_one(),
        "unknown": (
            await db.execute(select(func.count()).where(Movie.has_hdr.is_(None)))
        ).scalar_one(),
    }
    return {"dovi": counts["dovi"], "hdr10p": 0, "hdr10": counts["hdr10"], "sdr": counts["sdr"], "unknown": counts["unknown"]}


@router.get("")
async def hdr_index(
    db: Annotated[AsyncSession, Depends(get_db)],
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    hdr: str | None = Query(None, description="Filter: dovi | hdr10 | sdr | unknown"),
):
    """HDR/Dolby Vision library coverage from synced movie media info."""
    base = select(Movie, LetterboxState).outerjoin(
        LetterboxState, LetterboxState.movie_id == Movie.id
    )
    if hdr and (pred := hdr_filter(hdr)) is not None:
        base = base.where(pred)

    total = (await db.execute(select(func.count()).select_from(base.subquery()))).scalar_one()
    rows = (
        await db.execute(
            base.order_by(Movie.title).limit(page_size).offset((page - 1) * page_size)
        )
    ).all()

    movies = [movie for movie, _ in rows]
    movie_ids = [movie.id for movie in movies]
    media_rows = (
        (
            await db.execute(
                select(MediaFile).where(
                    MediaFile.movie_id.in_(movie_ids),
                    MediaFile.is_active.is_(True),
                )
            )
        )
        .scalars()
        .all()
        if movie_ids
        else []
    )
    mf_by_movie = {mf.movie_id: mf for mf in media_rows}
    coverage = await _coverage_by_media_file(db, [mf.id for mf in media_rows])

    items = []
    for movie, lb in rows:
        mf = mf_by_movie.get(movie.id)
        items.append(
            enrich_movie(
                movie,
                mf,
                coverage.get(mf.id) if mf else None,
                lb.status if lb else None,
            )
        )

    return {
        "distribution": await _distribution(db),
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": items,
    }
