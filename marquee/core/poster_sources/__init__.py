"""Poster source clients — TMDB (more sources to be added in Phase N)."""

from marquee.core.poster_sources.exceptions import (
    TMDBAuthenticationError,
    TMDBConnectionError,
    TMDBError,
    TMDBNotFoundError,
    TMDBResponseError,
)
from marquee.core.poster_sources.tmdb import (
    IMAGE_BASE_URL,
    PosterCandidate,
    TMDBClient,
)

__all__ = [
    "IMAGE_BASE_URL",
    "PosterCandidate",
    "TMDBClient",
    "TMDBError",
    "TMDBConnectionError",
    "TMDBAuthenticationError",
    "TMDBNotFoundError",
    "TMDBResponseError",
]
