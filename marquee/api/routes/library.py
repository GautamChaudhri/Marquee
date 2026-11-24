"""The movie library: the list, one movie, and its poster. Every route needs the API key."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Query, status
from fastapi.responses import Response

from marquee.api.files import file_response
from marquee.api.schemas.library import MovieItem, MovieList
from marquee.api.sessions import Session
from marquee.config import settings
from marquee.fs.boundary import FilesystemBoundaryError, boundary_for_roots
from marquee.fs.paths import PathMapper, PathMappingError
from marquee.library import queries

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/movies", tags=["library"])

_NOT_FOUND = {404: {"description": "No such movie, or Radarr no longer lists it"}}


@router.get("")
async def list_movies(
    session: Session,
    page: Annotated[int, Query(ge=1, description="Page number, from 1")] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 50,
    q: Annotated[
        str | None, Query(max_length=200, description="Case-insensitive title search")
    ] = None,
    has_poster: Annotated[
        bool | None, Query(description="Only movies with (true) or without (false) a poster")
    ] = None,
    include_unavailable: Annotated[
        bool, Query(description="Also list movies Radarr wants but has no file for")
    ] = False,
    sort: Annotated[
        queries.MovieSort,
        Query(description="title (A to Z by sort title), year or added (newest first)"),
    ] = "title",
) -> MovieList:
    """Movies Radarr lists. Only movies with a file unless ``include_unavailable``."""
    result = await queries.list_movies(
        session,
        page=page,
        page_size=page_size,
        q=q,
        has_poster=has_poster,
        include_unavailable=include_unavailable,
        sort=sort,
    )
    return MovieList(
        total=result.total,
        page=page,
        page_size=page_size,
        items=[MovieItem.build(row) for row in result.items],
    )


@router.get("/{movie_id}", responses=_NOT_FOUND)
async def get_movie(session: Session, movie_id: int) -> MovieItem:
    row = await queries.get_movie(session, movie_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="movie not found")
    return MovieItem.build(row)


def _poster_response(
    radarr_path: str,
    file_name: str,
    sha256: str,
    size_bytes: int,
    if_none_match: str | None,
) -> Response:
    """Blocking: resolves and opens the file on what is usually a network share."""
    folder = PathMapper.from_settings(settings).to_local(radarr_path)
    boundary = boundary_for_roots(
        {f"movies{i}": Path(root) for i, root in enumerate(settings.MOVIE_ROOTS)}, access="read"
    )
    classified = boundary.classify(folder / file_name, require_file=True)
    return file_response(
        boundary,
        classified,
        media_type="image/jpeg",
        etag=sha256,
        if_none_match=if_none_match,
        cache_control="private, no-cache",
        expected_size=size_bytes,
    )


@router.get(
    "/{movie_id}/poster",
    response_class=Response,
    responses={
        200: {"content": {"image/jpeg": {}}, "description": "The poster file"},
        304: {"description": "Unchanged since the ETag the client holds"},
        404: {"description": "No such movie, no poster, or the file is gone"},
    },
)
async def get_poster(
    session: Session,
    movie_id: int,
    if_none_match: Annotated[str | None, Header()] = None,
) -> Response:
    """The movie's poster as the last sync recorded it."""
    row = await queries.get_movie(session, movie_id)
    if row is None or row.poster is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="no poster")
    poster = row.poster
    try:
        return await asyncio.to_thread(
            _poster_response,
            row.movie.radarr_path,
            poster.file_name,
            poster.sha256,
            poster.size_bytes,
            if_none_match,
        )
    except (PathMappingError, FilesystemBoundaryError, OSError) as exc:
        logger.warning("API | poster of movie %s unavailable: %s", movie_id, type(exc).__name__)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="the poster file is gone or changed since the last sync",
        ) from exc
