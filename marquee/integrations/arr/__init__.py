"""*arr API clients — Radarr and Sonarr."""

from marquee.integrations.arr.base_client import ArrClient
from marquee.integrations.arr.exceptions import (
    ArrAuthenticationError,
    ArrClientError,
    ArrConnectionError,
    ArrNotFoundError,
    ArrResponseError,
)
from marquee.integrations.arr.radarr_client import RadarrClient
from marquee.integrations.arr.sonarr_client import SonarrClient

__all__ = [
    "ArrClient",
    "ArrClientError",
    "ArrConnectionError",
    "ArrAuthenticationError",
    "ArrNotFoundError",
    "ArrResponseError",
    "RadarrClient",
    "SonarrClient",
]
