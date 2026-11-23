"""Exceptions raised by *arr API clients."""

from __future__ import annotations


class ArrClientError(Exception):
    """Base exception for all *arr client errors."""


class ArrConnectionError(ArrClientError):
    """Cannot reach the server (connection refused or timeout)."""


class ArrAuthenticationError(ArrClientError):
    """API key rejected (HTTP 401)."""


class ArrNotFoundError(ArrClientError):
    """Resource not found (HTTP 404)."""


class ArrResponseError(ArrClientError):
    """Unexpected HTTP status or other API-level error."""

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code
