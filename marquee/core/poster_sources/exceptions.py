"""Exceptions raised by the TMDB API client."""

from __future__ import annotations


class TMDBError(Exception):
    """Base exception for all TMDB client errors."""


class TMDBConnectionError(TMDBError):
    """Cannot reach the TMDB API (connection refused or timeout)."""


class TMDBAuthenticationError(TMDBError):
    """API token rejected (HTTP 401)."""


class TMDBNotFoundError(TMDBError):
    """Resource not found (HTTP 404)."""


class TMDBResponseError(TMDBError):
    """Unexpected HTTP status or other API-level error."""

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code
