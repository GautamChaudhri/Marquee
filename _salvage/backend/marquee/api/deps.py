"""FastAPI dependency helpers.

Inject external API clients (Radarr, Sonarr, TMDB) into route handlers.
Each dependency checks that the client is configured and raises 503 if not.
"""

from __future__ import annotations

from fastapi import HTTPException, Request

from marquee.core.arr_clients.radarr_client import RadarrClient
from marquee.core.arr_clients.sonarr_client import SonarrClient
from marquee.core.poster_sources.tmdb import TMDBClient
from marquee.core.rate_limit import RateLimiter
from marquee.core.runtime_settings import effective_settings as settings


def get_radarr(request: Request) -> RadarrClient:
    """Return the Radarr client, or 503 if not configured."""
    client = request.app.state.radarr_client
    if client is None:
        raise HTTPException(
            status_code=503,
            detail="Radarr is not configured — set RADARR_URL and RADARR_API_KEY",
        )
    return client


def get_sonarr(request: Request) -> SonarrClient:
    """Return the Sonarr client, or 503 if not configured."""
    client = request.app.state.sonarr_client
    if client is None:
        raise HTTPException(
            status_code=503,
            detail="Sonarr is not configured — set SONARR_URL and SONARR_API_KEY",
        )
    return client


def get_tmdb(request: Request) -> TMDBClient:
    """Return the TMDB client, or 503 if not configured."""
    client = request.app.state.tmdb_client
    if client is None:
        raise HTTPException(
            status_code=503,
            detail="TMDB is not configured — set TMDB_READ_ACCESS_TOKEN",
        )
    return client


def get_rate_limiter(request: Request) -> RateLimiter:
    """Shared limiter for expensive endpoints (created in the app lifespan)."""
    return request.app.state.op_rate_limiter


def enforce_rate_limit(limiter: RateLimiter, key: str, cooldown: float) -> None:
    """Raise 429 if *key* is still within *cooldown*. No-op when DEBUG is on.

    Call ``limiter.record(key)`` only after the work succeeds, so a failed
    attempt doesn't start the cooldown.
    """
    if settings.DEBUG:
        return
    if not limiter.check(key, cooldown):
        retry = limiter.remaining(key, cooldown)
        raise HTTPException(
            status_code=429,
            detail=f"Rate limited — retry in {retry:.0f}s.",
            headers={"Retry-After": str(int(retry) + 1)},
        )
