"""Poster source clients — TMDB (more sources to be added in Phase N)."""

from marquee.integrations.tmdb.client import (
    IMAGE_BASE_URL,
    PosterCandidate,
    TMDBClient,
)
from marquee.integrations.tmdb.exceptions import (
    TMDBAuthenticationError,
    TMDBConnectionError,
    TMDBError,
    TMDBNotFoundError,
    TMDBResponseError,
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
