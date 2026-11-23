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


def poster_version(entity: Any) -> str | None:
    """Cache-validator token for a deployed poster (any ``PosterMixin`` entity).

    The sha prefix changes exactly when the deployed bytes change, which lets
    poster URLs carry ``?v=`` and be cached immutably. Sync-adopted posters may
    predate hashing — they fall back to the deploy timestamp, or to ``None``,
    which the serving route degrades to ETag revalidation instead.
    """
    if entity.poster_sha256:
        return entity.poster_sha256[:16]
    if entity.poster_deployed_at:
        return str(int(entity.poster_deployed_at.timestamp()))
    return None


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


def library_poster_url(kind: str, entity: Any) -> str | None:
    """Versioned poster URL for a library entity (``movies``/``series``/``seasons``).

    Carrying ``?v=`` lets the serving route mark the response immutable — the
    token changes with the deployed bytes, so a redeploy mints a new URL and
    the browser cache never goes stale.
    """
    if not entity.poster_path:
        return None
    version = poster_version(entity)
    suffix = f"?v={version}" if version else ""
    return f"/api/library/{kind}/{entity.id}/poster{suffix}"


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
        "poster_url": library_poster_url("movies", movie),
        "media_file_id": media_file.id if media_file else None,
    }
