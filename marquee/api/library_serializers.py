"""Display derivations for the library API (frontend G2 enrichment).

The Films list and Film-detail views need a handful of derived fields that the
``movies`` row carries the raw inputs for but doesn't store directly:
resolution label and poster status. Keeping the derivations here (rather than
inline in the route) lets the list endpoint, the detail endpoint, and the
server-side filters share one source of truth.

Each ``*_status`` helper has a matching ``*_filter`` predicate builder so that
server-side filtering stays consistent with the displayed value.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import and_
from sqlalchemy.sql.elements import ColumnElement

from marquee.models import Movie

# Poster status values, in precedence order (first match wins).
POSTER_STATUSES = ("missing", "approved", "review", "deployed")


def resolution_label(width: int | None, height: int | None) -> str | None:
    """Coarse label from the encoded width (Radarr mediaInfo).

    Width-based so DCI/UHD masters (4096/3840 wide) both read as 4K.
    """
    if not width:
        return None
    if width >= 3840:
        return "4K"
    if width >= 1920:
        return "1080p"
    if width >= 1280:
        return "720p"
    return "SD"


def poster_status(movie: Movie) -> str:
    """Derive the poster lifecycle state from the ``poster_*`` artwork columns.

    ``missing`` no poster · ``approved`` human-approved · ``review`` unreviewed
    AI pick · ``deployed`` poster present but neither AI-picked nor reviewed.

    NB: the mockup's separate ``override`` collapses into ``approved`` — there
    is no movie-level override column (override is a feedback-time action).
    """
    if movie.poster_path is None:
        return "missing"
    if movie.poster_user_approved:
        return "approved"
    if movie.poster_ai_selected:
        return "review"
    return "deployed"


def poster_status_filter(value: str) -> ColumnElement[bool] | None:
    """SQL predicate matching :func:`poster_status` for server-side filtering."""
    present = Movie.poster_path.isnot(None)
    if value == "missing":
        return Movie.poster_path.is_(None)
    if value == "approved":
        return and_(present, Movie.poster_user_approved.is_(True))
    if value == "review":
        return and_(
            present,
            Movie.poster_user_approved.is_(False),
            Movie.poster_ai_selected.is_(True),
        )
    if value == "deployed":
        return and_(
            present,
            Movie.poster_user_approved.is_(False),
            Movie.poster_ai_selected.is_(False),
        )
    return None


def enrich_movie(movie: Movie, media_file: Any | None, *, review_pending: bool = False) -> dict:
    """Assemble a list/detail item dict with derived display fields."""
    return {
        "id": movie.id,
        "title": movie.title,
        "year": movie.year,
        "tmdb_id": movie.tmdb_id,
        "genres": movie.genres,
        "container": movie.container,
        "video_width": movie.video_width,
        "video_height": movie.video_height,
        "resolution": resolution_label(movie.video_width, movie.video_height),
        "poster_status": poster_status(movie),
        "review_pending": review_pending,
        "poster_url": f"/api/library/movies/{movie.id}/poster" if movie.poster_path else None,
        "media_file_id": media_file.id if media_file else None,
    }
