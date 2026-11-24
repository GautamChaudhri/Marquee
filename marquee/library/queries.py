"""Read queries over the movie library, for the API and later the jobs.

Retired movies (ones Radarr no longer lists) are never returned. The movie list shows only
movies with a file unless asked to include the ones Radarr still wants.
"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import Select, and_, exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased
from sqlalchemy.sql.elements import ColumnElement

from marquee.db.models import ARTWORK_POSTER, Artwork, Movie, PosterRun

MovieSort = Literal["title", "year", "added"]
PosterOrigin = Literal["library", "marquee"]


@dataclass(frozen=True)
class MovieWithPoster:
    movie: Movie
    poster: Artwork | None
    latest_run_id: int | None = None


@dataclass(frozen=True)
class MoviePage:
    total: int
    items: list[MovieWithPoster]


def escape_like(text: str) -> str:
    """``text`` as a literal inside a LIKE pattern (escape character ``\\``)."""
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _poster_join() -> ColumnElement[bool]:
    return and_(Artwork.movie_id == Movie.id, Artwork.artwork_kind == ARTWORK_POSTER)


def _has_poster(origin: PosterOrigin | None = None) -> ColumnElement[bool]:
    # Its own alias, so the list query's join on artwork cannot correlate it away.
    poster = aliased(Artwork)
    conditions = [poster.movie_id == Movie.id, poster.artwork_kind == ARTWORK_POSTER]
    if origin is not None:
        conditions.append(poster.origin == origin)
    return exists(select(poster.id).where(*conditions))


def _has_run() -> ColumnElement[bool]:
    return exists(select(PosterRun.id).where(PosterRun.movie_id == Movie.id))


def _order(sort: MovieSort) -> tuple[ColumnElement, ...]:
    if sort == "year":
        return (Movie.year.desc().nulls_last(), Movie.sort_title, Movie.id)
    if sort == "added":
        return (Movie.radarr_added_at.desc().nulls_last(), Movie.sort_title, Movie.id)
    return (Movie.sort_title, Movie.id)


def _with_poster() -> Select[tuple[Movie, Artwork, int | None]]:
    latest_run = (
        select(func.max(PosterRun.id)).where(PosterRun.movie_id == Movie.id).scalar_subquery()
    )
    return select(Movie, Artwork, latest_run.label("latest_run_id")).outerjoin(
        Artwork, _poster_join()
    )


async def list_movies(
    session: AsyncSession,
    *,
    page: int = 1,
    page_size: int = 50,
    q: str | None = None,
    has_poster: bool | None = None,
    origin: PosterOrigin | None = None,
    has_run: bool | None = None,
    include_unavailable: bool = False,
    sort: MovieSort = "title",
) -> MoviePage:
    """One page of movies, with the total that match. ``origin`` keeps movies whose poster
    has that origin; ``has_run`` those with (or without) any run record."""
    conditions: list[ColumnElement[bool]] = [Movie.is_present.is_(True)]
    if not include_unavailable:
        conditions.append(Movie.has_file.is_(True))
    if q:
        conditions.append(Movie.title.ilike(f"%{escape_like(q)}%", escape="\\"))
    if has_poster is not None:
        conditions.append(_has_poster() if has_poster else ~_has_poster())
    if origin is not None:
        conditions.append(_has_poster(origin))
    if has_run is not None:
        conditions.append(_has_run() if has_run else ~_has_run())

    total = await session.scalar(select(func.count()).select_from(Movie).where(*conditions))
    rows = await session.execute(
        _with_poster()
        .where(*conditions)
        .order_by(*_order(sort))
        .limit(page_size)
        .offset((page - 1) * page_size)
    )
    return MoviePage(
        total=total or 0,
        items=[MovieWithPoster(movie, poster, latest) for movie, poster, latest in rows],
    )


async def get_movie(session: AsyncSession, movie_id: int) -> MovieWithPoster | None:
    """One movie Radarr still lists, with or without a file."""
    row = (
        await session.execute(
            _with_poster().where(Movie.id == movie_id, Movie.is_present.is_(True))
        )
    ).first()
    return MovieWithPoster(row[0], row[1], row[2]) if row else None


async def posters_of(session: AsyncSession, movie_ids: Collection[int]) -> dict[int, Artwork]:
    """Each movie's poster row, by movie id, in one query; movies with none are left out."""
    if not movie_ids:
        return {}
    rows = await session.scalars(
        select(Artwork).where(
            Artwork.movie_id.in_(set(movie_ids)), Artwork.artwork_kind == ARTWORK_POSTER
        )
    )
    return {row.movie_id: row for row in rows}


async def movie_names(
    session: AsyncSession, movie_ids: Collection[int]
) -> dict[int, tuple[str, int | None]]:
    """Each movie's title and year, by movie id, in one query."""
    if not movie_ids:
        return {}
    rows = await session.execute(
        select(Movie.id, Movie.title, Movie.year).where(Movie.id.in_(set(movie_ids)))
    )
    return {row.id: (row.title, row.year) for row in rows}
