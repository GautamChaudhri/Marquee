"""*arr API clients: Radarr (TV and Sonarr come later, written against the structure then)."""

from marquee.integrations.arr.base_client import ArrClient
from marquee.integrations.arr.exceptions import (
    ArrAuthenticationError,
    ArrClientError,
    ArrConnectionError,
    ArrNotFoundError,
    ArrResponseError,
)
from marquee.integrations.arr.radarr_client import RadarrClient

__all__ = [
    "ArrClient",
    "ArrClientError",
    "ArrConnectionError",
    "ArrAuthenticationError",
    "ArrNotFoundError",
    "ArrResponseError",
    "RadarrClient",
]
