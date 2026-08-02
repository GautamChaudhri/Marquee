"""Library routes — browse media with media-file linkage.

Each movie/episode resolves to its physical ``media_file_id`` so the frontend
filters and poster surfaces work from persisted data rather than scanning.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from marquee.api.job_submission import submission_response
from marquee.api.library_serializers import (
    enrich_movie,
    poster_status_filter,
)
from marquee.core.jobs.poster_submission import submit_poster_leaf
from marquee.core.jobs.submission import IdempotencyConflictError, SubmissionError
from marquee.core.movie_queries import movie_downloaded, movie_review_pending
from marquee.core.poster_subjects import PosterSubject
from marquee.core.sort_title import title_sort_expr
from marquee.core.tv_queries import season_downloaded, series_visible
from marquee.database import get_db
from marquee.models import (
    Episode,
    EpisodeMediaFile,
    MediaFile,
    Movie,
    Season,
    Series,
)

router = APIRouter(prefix="/api/library", tags=["library"])


def _poster_summary(entity) -> dict:
    return {
        "has_poster": entity.poster_path is not None,
        "ai_selected": bool(entity.poster_ai_selected),
        "user_approved": bool(entity.poster_user_approved),
        "deployed_at": entity.poster_deployed_at.isoformat() if entity.poster_deployed_at else None,
    }


def _season_poster_status(downloaded_seasons: int, seasons_with_poster: int) -> str:
    if downloaded_seasons <= 0 or seasons_with_poster <= 0:
        return "missing"
    if seasons_with_poster >= downloaded_seasons:
        return "complete"
    return "partial"


def _season_summary(season: Season) -> dict:
    return {
        "id": season.id,
        "season_number": season.season_number,
        "episode_count": season.episode_count,
        "episode_file_count": season.episode_file_count,
        "poster": _poster_summary(season),
    }


def _serve_subject_poster(subject, *, media_type: str):
    from marquee.core.filesystem import (  # noqa: PLC0415
        FilesystemBoundaryError,
        boundary_for_roots,
    )
    from marquee.core.path_utils import safe_translate_and_validate  # noqa: PLC0415

    try:
        folder = safe_translate_and_validate(subject.folder_raw, source=subject.path_source)
        boundary = boundary_for_roots({"subject": folder}, purpose="poster-serve")
        classified = boundary.classify(subject.entity.poster_path, require_file=True)
        return boundary.response(classified, media_type=media_type)
    except (FilesystemBoundaryError, ValueError) as exc:
        raise HTTPException(status_code=404, detail="Poster file not found on disk") from exc


async def _submit_poster_reset(
    db: AsyncSession, *, kind: str, subject_id: int, idempotency_key: str
):
    try:
        result = await submit_poster_leaf(
            db,
            job_type="poster_reset",
            target_kind=kind,
            target_id=subject_id,
            request={
                "target_kind": kind,
                "target_id": subject_id,
                "preserve_cache": True,
            },
            idempotency_key=idempotency_key,
            initiator=f"library-{kind}-poster-reset",
        )
        await db.commit()
    except IdempotencyConflictError as exc:
        raise HTTPException(status_code=409, detail=exc.api_detail) from exc
    except SubmissionError as exc:
        raise HTTPException(status_code=422, detail=exc.code) from exc
    return submission_response(result)


@router.get("/movies")
async def list_movies(
    db: Annotated[AsyncSession, Depends(get_db)],
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    q: str | None = Query(None, description="Case-insensitive title search"),
    poster_status: str | None = Query(
        None, description="Filter: missing | review | approved | deployed"
    ),
    exclude_in_review: bool = Query(
        False,
        description="Exclude movies whose latest unreviewed pipeline result is already in review.",
    ),
    include_unavailable: bool = Query(
        False, description="Include Radarr movies that do not have a downloaded file yet."
    ),
    sort: str = Query("title", description="Sort: title | year"),
):
    """List movies with media-file id and derived display fields.

    Optional filters map to real columns.
    """
    base = select(Movie)

    conditions = [Movie.is_present.is_(True)]
    if not include_unavailable:
        conditions.append(movie_downloaded())
    if q:
        conditions.append(Movie.title.ilike(f"%{q}%"))
    if poster_status and (pred := poster_status_filter(poster_status)) is not None:
        conditions.append(pred)
    if exclude_in_review:
        conditions.append(~movie_review_pending())
    if conditions:
        base = base.where(*conditions)

    total = (await db.execute(select(func.count()).select_from(base.subquery()))).scalar_one()

    if sort == "year":
        order_col = Movie.year.desc()
    elif sort == "added":
        order_col = Movie.created_at.desc()
    else:
        order_col = title_sort_expr()
    rows = (
        await db.execute(base.order_by(order_col).limit(page_size).offset((page - 1) * page_size))
    ).all()

    movies = [m for (m,) in rows]
    movie_ids = [m.id for m in movies]
    media_rows = (
        (
            await db.execute(
                select(MediaFile).where(
                    MediaFile.movie_id.in_(movie_ids),
                    MediaFile.is_active.is_(True),
                    MediaFile.is_present.is_(True),
                )
            )
        )
        .scalars()
        .all()
        if movie_ids
        else []
    )
    mf_by_movie = {mf.movie_id: mf for mf in media_rows}

    items = [enrich_movie(movie, mf_by_movie.get(movie.id)) for movie in movies]
    return {"total": total, "page": page, "page_size": page_size, "items": items}


@router.get("/movies/{movie_id}/poster")
async def get_movie_poster(movie_id: int, db: Annotated[AsyncSession, Depends(get_db)]):
    movie = (
        await db.execute(select(Movie).where(Movie.id == movie_id, Movie.is_present.is_(True)))
    ).scalar_one_or_none()
    if movie is None or not movie.poster_path:
        raise HTTPException(status_code=404, detail="No poster available")
    return _serve_subject_poster(PosterSubject.from_movie(movie), media_type="image/jpeg")


@router.get("/movies/{movie_id}")
async def get_movie(movie_id: int, db: Annotated[AsyncSession, Depends(get_db)]):
    movie = (
        await db.execute(select(Movie).where(Movie.id == movie_id, Movie.is_present.is_(True)))
    ).scalar_one_or_none()
    if movie is None:
        raise HTTPException(status_code=404, detail=f"Movie id={movie_id} not found")
    mf = (
        await db.execute(
            select(MediaFile).where(
                MediaFile.movie_id == movie_id,
                MediaFile.is_active.is_(True),
                MediaFile.is_present.is_(True),
            )
        )
    ).scalar_one_or_none()
    item = enrich_movie(movie, mf)
    item["media_file_path"] = mf.path if mf else None
    return item


@router.get("/series")
async def list_series(
    db: Annotated[AsyncSession, Depends(get_db)],
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
):
    base = select(Series).where(series_visible())
    total = (await db.execute(select(func.count()).select_from(base.subquery()))).scalar_one()
    rows = (
        (
            await db.execute(
                base.order_by(title_sort_expr(Series.title))
                .limit(page_size)
                .offset((page - 1) * page_size)
            )
        )
        .scalars()
        .all()
    )
    series_ids = [series.id for series in rows]
    season_rows = (
        (
            await db.execute(
                select(Season)
                .where(Season.series_id.in_(series_ids), season_downloaded())
                .order_by(Season.series_id, Season.season_number)
            )
        )
        .scalars()
        .all()
        if series_ids
        else []
    )
    downloaded_counts: dict[int, int] = dict.fromkeys(series_ids, 0)
    poster_counts: dict[int, int] = dict.fromkeys(series_ids, 0)
    seasons_by_series: dict[int, list[dict]] = {series_id: [] for series_id in series_ids}
    for season in season_rows:
        downloaded_counts[season.series_id] = downloaded_counts.get(season.series_id, 0) + 1
        if season.poster_path is not None:
            poster_counts[season.series_id] = poster_counts.get(season.series_id, 0) + 1
        seasons_by_series.setdefault(season.series_id, []).append(_season_summary(season))
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": [
            {
                "id": s.id,
                "title": s.title,
                "year": s.year,
                "tmdb_id": s.tmdb_id,
                "genres": s.genres,
                "poster": _poster_summary(s),
                "downloaded_seasons": downloaded_counts.get(s.id, 0),
                "seasons_with_poster": poster_counts.get(s.id, 0),
                "season_poster_status": _season_poster_status(
                    downloaded_counts.get(s.id, 0), poster_counts.get(s.id, 0)
                ),
                "season_count": s.season_count,
                "seasons": seasons_by_series.get(s.id, []),
            }
            for s in rows
        ],
    }


@router.get("/series/{series_id}")
async def get_series(series_id: int, db: Annotated[AsyncSession, Depends(get_db)]):
    series = (
        await db.execute(select(Series).where(Series.id == series_id, series_visible()))
    ).scalar_one_or_none()
    if series is None:
        raise HTTPException(status_code=404, detail=f"Series id={series_id} not found")
    seasons = (
        (
            await db.execute(
                select(Season)
                .where(Season.series_id == series_id, season_downloaded())
                .order_by(Season.season_number)
            )
        )
        .scalars()
        .all()
    )
    downloaded_seasons = len(seasons)
    seasons_with_poster = sum(1 for season in seasons if season.poster_path is not None)
    return {
        "id": series.id,
        "title": series.title,
        "year": series.year,
        "tmdb_id": series.tmdb_id,
        "tvdb_id": series.tvdb_id,
        "genres": series.genres,
        "poster": _poster_summary(series),
        "downloaded_seasons": downloaded_seasons,
        "seasons_with_poster": seasons_with_poster,
        "season_poster_status": _season_poster_status(downloaded_seasons, seasons_with_poster),
        "season_count": series.season_count,
        "show_text_profile_id": series.show_text_profile_id,
        "season_text_profile_id": series.season_text_profile_id,
        "seasons": [_season_summary(season) for season in seasons],
    }


@router.get("/series/{series_id}/seasons")
async def list_seasons(
    series_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    downloaded_only: bool = True,
):
    series = (
        await db.execute(select(Series).where(Series.id == series_id, series_visible()))
    ).scalar_one_or_none()
    if series is None:
        raise HTTPException(status_code=404, detail=f"Series id={series_id} not found")
    query = select(Season).where(
        Season.series_id == series_id,
        Season.is_present.is_(True),
    )
    if downloaded_only:
        query = query.where(season_downloaded())
    rows = (await db.execute(query.order_by(Season.season_number))).scalars().all()
    return {
        "series_id": series_id,
        "seasons": [
            {
                "id": s.id,
                "season_number": s.season_number,
                "episode_count": s.episode_count,
                "episode_file_count": s.episode_file_count,
                "poster": _poster_summary(s),
            }
            for s in rows
        ],
    }


@router.get("/episodes/{episode_id}")
async def get_episode(episode_id: int, db: Annotated[AsyncSession, Depends(get_db)]):
    """Episode detail + the shared media-file id (multi-episode files share one)."""
    episode = (
        await db.execute(
            select(Episode).where(Episode.id == episode_id, Episode.is_present.is_(True))
        )
    ).scalar_one_or_none()
    if episode is None:
        raise HTTPException(status_code=404, detail=f"Episode id={episode_id} not found")
    media_file_id = (
        await db.execute(
            select(EpisodeMediaFile.media_file_id).where(EpisodeMediaFile.episode_id == episode_id)
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


@router.delete("/movies/{movie_id}/poster", status_code=202)
async def delete_movie_poster(
    movie_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
):
    """Delete a movie's deployed poster and reset all its poster_* columns.

    This is the single-movie equivalent of the global poster_deploy_reset job.
    """
    movie = (
        await db.execute(select(Movie).where(Movie.id == movie_id, Movie.is_present.is_(True)))
    ).scalar_one_or_none()
    if movie is None:
        raise HTTPException(status_code=404, detail=f"Movie id={movie_id} not found")

    return await _submit_poster_reset(
        db, kind="movie", subject_id=movie_id, idempotency_key=idempotency_key
    )


@router.get("/series/{series_id}/poster")
async def get_series_poster(series_id: int, db: Annotated[AsyncSession, Depends(get_db)]):
    series = (
        await db.execute(select(Series).where(Series.id == series_id, series_visible()))
    ).scalar_one_or_none()
    if series is None or not series.poster_path:
        raise HTTPException(status_code=404, detail="No poster available")
    return _serve_subject_poster(PosterSubject.from_series(series), media_type="image/jpeg")


@router.get("/seasons/{season_id}/poster")
async def get_season_poster(season_id: int, db: Annotated[AsyncSession, Depends(get_db)]):
    row = (
        await db.execute(
            select(Season, Series)
            .join(Series, Series.id == Season.series_id)
            .where(Season.id == season_id, season_downloaded())
        )
    ).one_or_none()
    if row is None or not row.Season.poster_path:
        raise HTTPException(status_code=404, detail="No poster available")
    return _serve_subject_poster(
        PosterSubject.from_season(row.Season, row.Series), media_type="image/jpeg"
    )


@router.delete("/series/{series_id}/poster", status_code=202)
async def delete_series_poster(
    series_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
):
    series = (
        await db.execute(select(Series).where(Series.id == series_id, series_visible()))
    ).scalar_one_or_none()
    if series is None:
        raise HTTPException(status_code=404, detail=f"Series id={series_id} not found")
    return await _submit_poster_reset(
        db, kind="series", subject_id=series_id, idempotency_key=idempotency_key
    )


@router.delete("/seasons/{season_id}/poster", status_code=202)
async def delete_season_poster(
    season_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
):
    season = (
        await db.execute(select(Season).where(Season.id == season_id, season_downloaded()))
    ).scalar_one_or_none()
    if season is None:
        raise HTTPException(status_code=404, detail=f"Season id={season_id} not found")
    return await _submit_poster_reset(
        db, kind="season", subject_id=season_id, idempotency_key=idempotency_key
    )
