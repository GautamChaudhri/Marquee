"""The API key every ``/api`` route requires.

The key is presented as ``Authorization: Bearer <key>`` or ``X-Api-Key: <key>`` and compared
in constant time. Missing or wrong: 401. No key configured: 503, so a fresh install without
``API_KEY`` serves nothing. There is no bypass of any kind: not for debug mode, not for
loopback clients, and never a key in a query string (query strings end up in logs).

After ten requests with a missing or wrong key within a minute from one client address,
that address gets 429 with ``Retry-After`` until the minute has passed
(:mod:`marquee.api.rate_limit`). The right key is never slowed. The health probes are
outside ``/api`` and are not counted.
"""

from __future__ import annotations

import logging
import math
import secrets
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import APIKeyHeader, HTTPAuthorizationCredentials, HTTPBearer

from marquee.api.rate_limit import WrongKeyLimiter
from marquee.config import settings

logger = logging.getLogger(__name__)

# Declared as security schemes so the OpenAPI schema documents both header forms.
_bearer = HTTPBearer(auto_error=False, description="Authorization: Bearer <API_KEY>")
_api_key_header = APIKeyHeader(name="X-Api-Key", auto_error=False, description="The API key")


def _presented(bearer: HTTPAuthorizationCredentials | None, header: str | None) -> str | None:
    if bearer is not None and bearer.credentials.strip():
        return bearer.credentials.strip()
    if header is not None and header.strip():
        return header.strip()
    return None


def _client_address(request: Request) -> str:
    return request.client.host if request.client is not None else "unknown"


async def require_api_key(
    request: Request,
    bearer: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    header: Annotated[str | None, Depends(_api_key_header)],
) -> None:
    """Allow the request only with the configured key."""
    configured = settings.API_KEY
    if configured is None or not configured.get_secret_value():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="the API key is not configured; set API_KEY",
        )
    presented = _presented(bearer, header)
    if presented is not None and secrets.compare_digest(
        presented.encode(), configured.get_secret_value().encode()
    ):
        return
    limiter: WrongKeyLimiter = request.app.state.wrong_keys
    client = _client_address(request)
    wait = limiter.retry_after(client)
    if wait > 0:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="too many requests with a missing or wrong API key; try again later",
            headers={"Retry-After": str(max(1, math.ceil(wait)))},
        )
    if limiter.fail(client):
        logger.warning(
            "API | %d requests with a missing or wrong key from %s within %.0f s; "
            "it is refused with 429 until that time has passed",
            limiter.max_failures,
            client,
            limiter.window_seconds,
        )
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="missing or invalid API key",
        headers={"WWW-Authenticate": "Bearer"},
    )
