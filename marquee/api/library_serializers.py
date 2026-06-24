"""Display derivations for the library API (frontend G2 enrichment).

The Films list and Film-detail views need a handful of derived fields that the
``movies`` row carries the raw inputs for but doesn't store directly:
resolution label, poster status, HDR badge, and subtitle ok/gap. Keeping the
derivations here (rather than inline in the route) lets the list endpoint, the
detail endpoint, and the server-side filters share one source of truth.

Each ``*_status`` helper has a matching ``*_filter`` predicate builder so that
server-side filtering stays consistent with the displayed value.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import and_
from sqlalchemy.sql.elements import ColumnElement

from marquee.core.radarr_overlay import legacy_hdr_label, legacy_hdr_tags
from marquee.models import Movie

# Poster status values, in precedence order (first match wins).
POSTER_STATUSES = ("missing", "approved", "review", "deployed")
# HDR badge values (None = not yet probed).
HDR_VALUES = ("dovi", "hdr10p", "hdr10", "hdr", "sdr")


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


def hdr_label(has_hdr: bool | None, has_dv: bool | None) -> str | None:
    """Map the ``has_hdr`` / ``has_dv`` booleans to a badge.

    Returns ``None`` when the dynamic range hasn't been probed yet (both NULL).
    Booleans can't distinguish HDR10+ from HDR10 — that needs a raw
    dynamic-range string column (future follow-up).
    """
    return legacy_hdr_label(None, has_hdr, has_dv)


def movie_hdr_label(movie: Movie) -> str | None:
    """Single badge label using raw HDR data when available."""
    return legacy_hdr_label(movie.hdr_type_raw, movie.has_hdr, movie.has_dv)


def movie_hdr_tags(movie: Movie) -> list[str]:
    """Multi-tag HDR display list for movie surfaces."""
    return legacy_hdr_tags(movie.hdr_type_raw, movie.has_hdr, movie.has_dv)


def hdr_filter(value: str) -> ColumnElement[bool] | None:
    """SQL predicate matching :func:`hdr_label` for server-side filtering."""
    if value == "dovi":
        return Movie.has_dv.is_(True)
    if value == "hdr10p":
        return Movie.hdr_type_raw.ilike("%HDR10PLUS%") | Movie.hdr_type_raw.ilike("%HDR10+%")
    if value == "hdr10":
        return and_(
            Movie.has_hdr.is_(True),
            Movie.has_dv.isnot(True),
            Movie.hdr_type_raw.isnot(None),
            Movie.hdr_type_raw.not_ilike("%HDR10PLUS%"),
            Movie.hdr_type_raw.not_ilike("%HDR10+%"),
            Movie.hdr_type_raw.ilike("%HDR10%"),
        )
    if value == "hdr":
        return and_(
            Movie.has_hdr.is_(True),
            Movie.has_dv.isnot(True),
            Movie.hdr_type_raw.isnot(None),
            Movie.hdr_type_raw.not_ilike("%HDR10%"),
            Movie.hdr_type_raw.not_ilike("%HDR10+%"),
            Movie.hdr_type_raw.not_ilike("%HDR10PLUS%"),
        )
    if value == "sdr":
        return and_(Movie.has_hdr.is_(False), Movie.has_dv.isnot(True))
    if value == "unknown":
        return and_(Movie.has_hdr.is_(None), Movie.hdr_type_raw.is_(None))
    return None


def subtitle_status(coverage: dict | None) -> str | None:
    """``ok`` / ``gap`` from a persisted coverage summary, or ``None`` if unscanned.

    Uses ``missing_preferred_languages`` from
    :func:`marquee.core.subtitles.coverage.compute_coverage`.
    """
    if not coverage:
        return None
    return "gap" if coverage.get("missing_preferred_languages") else "ok"


def enrich_movie(
    movie: Movie,
    media_file: Any | None,
    coverage: dict | None,
    lb_status: str | None,
) -> dict:
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
        "poster_url": f"/api/library/movies/{movie.id}/poster" if movie.poster_path else None,
        "hdr": movie_hdr_label(movie),
        "hdr_tags": movie_hdr_tags(movie),
        "letterbox_status": lb_status or "none",
        "subtitle_status": subtitle_status(coverage),
        "media_file_id": media_file.id if media_file else None,
        "subtitle_coverage": coverage,
    }
