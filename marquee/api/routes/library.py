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
from sqlalchemy import exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from marquee.api.library_serializers import (
    enrich_movie,
    hdr_filter,
    poster_status_filter,
)
from marquee.core.sort_title import title_sort_expr
from marquee.core.subtitles import coverage as subtitle_coverage
from marquee.core.tv_queries import season_downloaded, series_visible
from marquee.database import get_db
from marquee.models import (
    ArtworkEvent,
    Episode,
    EpisodeMediaFile,
    LetterboxState,
    MediaFile,
    Movie,
    PipelineRun,
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
            select(
                SubtitleInventory.media_file_id,
                SubtitleInventory.coverage_json,
                SubtitleInventory.audio_streams_json,
            ).where(SubtitleInventory.media_file_id.in_(media_file_ids))
        )
    ).all()
    result = {}
    for mid, cov, audio_json in rows:
        summary = (cov if isinstance(cov, dict) else json.loads(cov)) if cov else {}
        audio_streams = (
            (audio_json if isinstance(audio_json, list) else json.loads(audio_json))
            if audio_json
            else []
        )
        if summary and "audio_channels_by_language" not in summary:
            by_language: dict[str, list[str]] = {}
            for stream in audio_streams:
                lang = stream.get("language_tag") or "und"
                label = stream.get("channel_label") or subtitle_coverage.channel_label(stream)
                if label:
                    by_language.setdefault(lang, [])
                    if label not in by_language[lang]:
                        by_language[lang].append(label)
            summary["audio_channels_by_language"] = {
                lang: sorted(labels) for lang, labels in by_language.items()
            }
        result[mid] = summary
    return result


def _poster_summary(entity) -> dict:
    return {
        "has_poster": entity.poster_path is not None,
        "ai_selected": bool(entity.poster_ai_selected),
        "user_approved": bool(entity.poster_user_approved),
        "deployed_at": entity.poster_deployed_at.isoformat() if entity.poster_deployed_at else None,
    }


def _reset_poster_columns(entity) -> None:
    entity.poster_path = None
    entity.poster_source = None
    entity.poster_source_url = None
    entity.poster_ai_selected = False
    entity.poster_embedding = None
    entity.poster_sha256 = None
    entity.poster_phash = None
    entity.poster_user_approved = False
    entity.poster_deployed_filename = None
    entity.poster_deployed_at = None
    entity.poster_local_backup_path = None


def _season_poster_status(downloaded_seasons: int, seasons_with_poster: int) -> str:
    if downloaded_seasons <= 0 or seasons_with_poster <= 0:
        return "missing"
    if seasons_with_poster >= downloaded_seasons:
        return "complete"
    return "partial"


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


async def _delete_subject_poster(db: AsyncSession, subject, *, detail_prefix: str) -> dict:
    from marquee.core.filesystem import (  # noqa: PLC0415
        FilesystemBoundaryError,
        boundary_for_roots,
    )
    from marquee.core.path_utils import safe_translate_and_validate  # noqa: PLC0415

    entity = subject.entity
    if not entity.poster_path:
        return {"ok": True, "detail": f"{detail_prefix} does not have a deployed poster"}

    stored_path = str(entity.poster_path)
    try:
        folder = safe_translate_and_validate(subject.folder_raw, source=subject.path_source)
        boundary = boundary_for_roots(
            {"subject": folder}, access="read_write", purpose="poster-delete"
        )
        classified = boundary.classify(stored_path, require_exists=False, write=True)
        boundary.delete_file(classified, missing_ok=True)
    except (FilesystemBoundaryError, OSError, ValueError) as exc:
        error_msg = str(exc)
        db.add(
            ArtworkEvent(
                **subject.event_fk_kwargs(),
                action="deploy_reset_error",
                source="maintenance",
                detail=json.dumps({"deleted_path": stored_path, "error": error_msg}),
            )
        )
        await db.commit()
        return {"ok": False, "deleted": False, "error": error_msg}

    _reset_poster_columns(entity)

    db.add(
        ArtworkEvent(
            **subject.event_fk_kwargs(),
            action="deploy_reset",
            source="maintenance",
            detail=json.dumps({"deleted_path": stored_path}),
        )
    )
    await db.commit()
    return {"ok": True, "deleted": True, "error": None}


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
    exclude_in_review: bool = Query(
        False,
        description="Exclude movies whose latest unreviewed pipeline result is already in review.",
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
        LetterboxState,
        (LetterboxState.movie_id == Movie.id) & (LetterboxState.media_type == "movie"),
    )

    conditions = [Movie.is_present.is_(True)]
    if not include_unavailable:
        has_active_media = exists(
            select(MediaFile.id).where(
                MediaFile.movie_id == Movie.id,
                MediaFile.is_active.is_(True),
                MediaFile.is_present.is_(True),
            )
        )
        conditions.append(or_(Movie.movie_file_path.is_not(None), has_active_media))
    if q:
        conditions.append(Movie.title.ilike(f"%{q}%"))
    if poster_status and (pred := poster_status_filter(poster_status)) is not None:
        conditions.append(pred)
    if exclude_in_review:
        latest_review = (
            select(PipelineRun.movie_id.label("movie_id"))
            .where(
                PipelineRun.feedback_event_id.is_(None),
                PipelineRun.status.in_(("completed", "flagged_manual")),
            )
            .group_by(PipelineRun.movie_id)
            .subquery()
        )
        conditions.append(Movie.id.not_in(select(latest_review.c.movie_id)))
    if hdr and (pred := hdr_filter(hdr)) is not None:
        conditions.append(pred)
    if letterbox_status == "none":
        conditions.append(LetterboxState.id.is_(None))
    elif letterbox_status:
        conditions.append(LetterboxState.status == letterbox_status)
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

    movies = [m for m, _ in rows]
    lb_by_movie = {m.id: lb for m, lb in rows}
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
    from marquee.core.poster_subjects import PosterSubject  # noqa: PLC0415

    movie = (
        await db.execute(
            select(Movie).where(Movie.id == movie_id, Movie.is_present.is_(True))
        )
    ).scalar_one_or_none()
    if movie is None or not movie.poster_path:
        raise HTTPException(status_code=404, detail="No poster available")
    return _serve_subject_poster(PosterSubject.from_movie(movie), media_type="image/jpeg")


@router.get("/movies/{movie_id}")
async def get_movie(movie_id: int, db: Annotated[AsyncSession, Depends(get_db)]):
    row = (
        await db.execute(
            select(Movie, LetterboxState)
            .outerjoin(
                LetterboxState,
                (LetterboxState.movie_id == Movie.id) & (LetterboxState.media_type == "movie"),
            )
            .where(Movie.id == movie_id, Movie.is_present.is_(True))
        )
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail=f"Movie id={movie_id} not found")
    movie, lb = row
    mf = (
        await db.execute(
            select(MediaFile).where(
                MediaFile.movie_id == movie_id,
                MediaFile.is_active.is_(True),
                MediaFile.is_present.is_(True),
            )
        )
    ).scalar_one_or_none()
    coverage = await _coverage_by_media_file(db, [mf.id] if mf else [])
    item = enrich_movie(movie, mf, coverage.get(mf.id) if mf else None, lb.status if lb else None)
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
                base
                .order_by(title_sort_expr(Series.title))
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
                select(Season).where(Season.series_id.in_(series_ids), season_downloaded())
            )
        )
        .scalars()
        .all()
        if series_ids
        else []
    )
    downloaded_counts: dict[int, int] = dict.fromkeys(series_ids, 0)
    poster_counts: dict[int, int] = dict.fromkeys(series_ids, 0)
    for season in season_rows:
        downloaded_counts[season.series_id] = downloaded_counts.get(season.series_id, 0) + 1
        if season.poster_path is not None:
            poster_counts[season.series_id] = poster_counts.get(season.series_id, 0) + 1
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
                "poster": _poster_summary(s),
                "downloaded_seasons": downloaded_counts.get(s.id, 0),
                "seasons_with_poster": poster_counts.get(s.id, 0),
                "season_poster_status": _season_poster_status(
                    downloaded_counts.get(s.id, 0), poster_counts.get(s.id, 0)
                ),
                "season_count": s.season_count,
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
        "poster": _poster_summary(series),
        "downloaded_seasons": downloaded_seasons,
        "seasons_with_poster": seasons_with_poster,
        "season_poster_status": _season_poster_status(downloaded_seasons, seasons_with_poster),
        "season_count": series.season_count,
        "show_text_profile_id": series.show_text_profile_id,
        "season_text_profile_id": series.season_text_profile_id,
        "seasons": [
            {
                "id": season.id,
                "season_number": season.season_number,
                "episode_count": season.episode_count,
                "episode_file_count": season.episode_file_count,
                "poster": _poster_summary(season),
            }
            for season in seasons
        ],
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
    rows = (
        (
            await db.execute(
                query.order_by(Season.season_number)
            )
        )
        .scalars()
        .all()
    )
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


@router.delete("/movies/{movie_id}/poster")
async def delete_movie_poster(
    movie_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Delete a movie's deployed poster and reset all its poster_* columns.

    This is the single-movie equivalent of the global poster_deploy_reset job.
    """
    from marquee.core.poster_service import cache_paths  # noqa: PLC0415
    from marquee.core.poster_subjects import PosterSubject  # noqa: PLC0415

    movie = (
        await db.execute(
            select(Movie).where(Movie.id == movie_id, Movie.is_present.is_(True))
        )
    ).scalar_one_or_none()
    if movie is None:
        raise HTTPException(status_code=404, detail=f"Movie id={movie_id} not found")

    response = await _delete_subject_poster(
        db, PosterSubject.from_movie(movie), detail_prefix="Movie"
    )
    if movie.tmdb_id:
        response["cache_kept"] = str(cache_paths(movie.tmdb_id)[0])
    return response


@router.get("/series/{series_id}/poster")
async def get_series_poster(series_id: int, db: Annotated[AsyncSession, Depends(get_db)]):
    from marquee.core.poster_subjects import PosterSubject  # noqa: PLC0415

    series = (
        await db.execute(select(Series).where(Series.id == series_id, series_visible()))
    ).scalar_one_or_none()
    if series is None or not series.poster_path:
        raise HTTPException(status_code=404, detail="No poster available")
    return _serve_subject_poster(PosterSubject.from_series(series), media_type="image/jpeg")


@router.get("/seasons/{season_id}/poster")
async def get_season_poster(season_id: int, db: Annotated[AsyncSession, Depends(get_db)]):
    from marquee.core.poster_subjects import PosterSubject  # noqa: PLC0415

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


@router.delete("/series/{series_id}/poster")
async def delete_series_poster(
    series_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    from marquee.core.poster_subjects import PosterSubject  # noqa: PLC0415

    series = (
        await db.execute(select(Series).where(Series.id == series_id, series_visible()))
    ).scalar_one_or_none()
    if series is None:
        raise HTTPException(status_code=404, detail=f"Series id={series_id} not found")
    return await _delete_subject_poster(db, PosterSubject.from_series(series), detail_prefix="Series")


@router.delete("/seasons/{season_id}/poster")
async def delete_season_poster(
    season_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    from marquee.core.poster_subjects import PosterSubject  # noqa: PLC0415

    season = (
        await db.execute(select(Season).where(Season.id == season_id, season_downloaded()))
    ).scalar_one_or_none()
    if season is None:
        raise HTTPException(status_code=404, detail=f"Season id={season_id} not found")
    series = (
        await db.execute(
            select(Series).where(
                Series.id == season.series_id,
                Series.is_present.is_(True),
            )
        )
    ).scalar_one()
    return await _delete_subject_poster(
        db, PosterSubject.from_season(season, series), detail_prefix="Season"
    )
