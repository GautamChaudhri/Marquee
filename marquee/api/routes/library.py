"""The movie library: the list, one movie, and its poster. Every route needs the API key."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
from enum import IntEnum
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Query, status
from fastapi.responses import Response

from marquee.api.files import etag_matches, file_response
from marquee.api.schemas.library import MovieItem, MovieList
from marquee.api.sessions import Session
from marquee.artwork import thumbnails
from marquee.config import settings
from marquee.fs.boundary import (
    ClassifiedPath,
    FilesystemBoundary,
    FilesystemBoundaryError,
    boundary_for_roots,
)
from marquee.fs.paths import PathMapper, PathMappingError
from marquee.library import queries

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/movies", tags=["library"])

_NOT_FOUND = {404: {"description": "No such movie, or Radarr no longer lists it"}}
VERSION_LENGTH = 12


class ThumbnailWidth(IntEnum):
    """The widths a thumbnail is made at (:data:`marquee.artwork.thumbnails.WIDTHS`)."""

    SMALL = 160
    MEDIUM = 320
    LARGE = 640


CACHE_FOR_A_YEAR = "private, max-age=31536000, immutable"
REVALIDATE = "private, no-cache"


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


class PosterChangedError(OSError):
    """The file in the folder is not the bytes the last sync recorded."""


def _boundary_and_path(
    radarr_path: str, file_name: str
) -> tuple[FilesystemBoundary, ClassifiedPath]:
    folder = PathMapper.from_settings(settings).to_local(radarr_path)
    boundary = boundary_for_roots(
        {f"movies{i}": Path(root) for i, root in enumerate(settings.MOVIE_ROOTS)}, access="read"
    )
    return boundary, boundary.classify(folder / file_name, require_file=True)


def _poster_response(
    radarr_path: str,
    file_name: str,
    sha256: str,
    size_bytes: int,
    if_none_match: str | None,
    cache_control: str,
) -> Response:
    """Blocking: resolves and opens the file on what is usually a network share."""
    boundary, classified = _boundary_and_path(radarr_path, file_name)
    return file_response(
        boundary,
        classified,
        media_type="image/jpeg",
        etag=sha256,
        if_none_match=if_none_match,
        cache_control=cache_control,
        expected_size=size_bytes,
    )


def _poster_bytes(radarr_path: str, file_name: str, sha256: str, size_bytes: int) -> bytes:
    """Blocking: the poster's bytes, read the way the full-size route reads them, and proven
    to be the bytes the last sync recorded (a thumbnail is kept under that hash)."""
    boundary, classified = _boundary_and_path(radarr_path, file_name)
    fd = boundary.open_read(classified)
    with os.fdopen(fd, "rb") as handle:
        if os.fstat(handle.fileno()).st_size != size_bytes:
            raise PosterChangedError("the poster's size does not match its record")
        data = handle.read()
    if hashlib.sha256(data).hexdigest() != sha256:
        raise PosterChangedError("the poster's bytes do not match its record")
    return data


def _thumbnail(radarr_path: str, file_name: str, sha256: str, size_bytes: int, width: int) -> bytes:
    """Blocking: the kept thumbnail, or a new one made from the library file and kept."""
    data = thumbnails.cached(sha256, width)
    if data is None:
        data = thumbnails.make(_poster_bytes(radarr_path, file_name, sha256, size_bytes), width)
        thumbnails.store(sha256, width, data)
    return data


@router.get(
    "/{movie_id}/poster",
    response_class=Response,
    responses={
        200: {"content": {"image/jpeg": {}}, "description": "The poster file, or a thumbnail"},
        304: {"description": "Unchanged since the ETag the client holds"},
        404: {"description": "No such movie, no poster, or the file is gone or changed"},
        409: {"description": "A thumbnail was asked for and the poster does not decode"},
    },
)
async def get_poster(
    session: Session,
    movie_id: int,
    width: Annotated[
        ThumbnailWidth | None,
        Query(description="A thumbnail this wide (never enlarged); the file itself without"),
    ] = None,
    v: Annotated[
        str | None,
        Query(
            max_length=64,
            description="The first 12 hex of the poster's sha256: while it is current, the "
            "answer may be cached for a year",
        ),
    ] = None,
    if_none_match: Annotated[str | None, Header()] = None,
) -> Response:
    """The movie's poster as the last sync recorded it, or a JPEG thumbnail of it."""
    row = await queries.get_movie(session, movie_id)
    if row is None or row.poster is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="no poster")
    poster = row.poster
    current = v is not None and v == poster.sha256[:VERSION_LENGTH]
    cache_control = CACHE_FOR_A_YEAR if current else REVALIDATE
    try:
        if width is None:
            return await asyncio.to_thread(
                _poster_response,
                row.movie.radarr_path,
                poster.file_name,
                poster.sha256,
                poster.size_bytes,
                if_none_match,
                cache_control,
            )
        size = int(width)
        headers = {"etag": f'"{poster.sha256}-{size}"', "cache-control": cache_control}
        if if_none_match is not None and etag_matches(if_none_match, headers["etag"]):
            return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers=headers)
        data = await asyncio.to_thread(
            _thumbnail,
            row.movie.radarr_path,
            poster.file_name,
            poster.sha256,
            poster.size_bytes,
            size,
        )
        return Response(content=data, media_type="image/jpeg", headers=headers)
    except thumbnails.ThumbnailError as exc:
        logger.warning("API | thumbnail of movie %s: the poster does not decode", movie_id)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="the poster file does not decode"
        ) from exc
    except (PathMappingError, FilesystemBoundaryError, OSError) as exc:
        logger.warning("API | poster of movie %s unavailable: %s", movie_id, type(exc).__name__)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="the poster file is gone or changed since the last sync",
        ) from exc
