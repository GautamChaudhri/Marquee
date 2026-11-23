"""*arr API clients — Radarr and Sonarr."""

from marquee.core.arr_clients.base_client import ArrClient
from marquee.core.arr_clients.exceptions import (
    ArrAuthenticationError,
    ArrClientError,
    ArrConnectionError,
    ArrNotFoundError,
    ArrResponseError,
)
from marquee.core.arr_clients.radarr_client import RadarrClient
from marquee.core.arr_clients.sonarr_client import SonarrClient

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
