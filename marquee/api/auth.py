"""The API key every ``/api`` route requires.

The key is presented as ``Authorization: Bearer <key>`` or ``X-Api-Key: <key>`` and compared
in constant time. Missing or wrong: 401. No key configured: 503, so a fresh install without
``API_KEY`` serves nothing. There is no bypass of any kind: not for debug mode, not for
loopback clients, and never a key in a query string (query strings end up in logs).
"""

from __future__ import annotations

import secrets
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import APIKeyHeader, HTTPAuthorizationCredentials, HTTPBearer

from marquee.config import settings

# Declared as security schemes so the OpenAPI schema documents both header forms.
_bearer = HTTPBearer(auto_error=False, description="Authorization: Bearer <API_KEY>")
_api_key_header = APIKeyHeader(name="X-Api-Key", auto_error=False, description="The API key")


def _presented(bearer: HTTPAuthorizationCredentials | None, header: str | None) -> str | None:
    if bearer is not None and bearer.credentials.strip():
        return bearer.credentials.strip()
    if header is not None and header.strip():
        return header.strip()
    return None


async def require_api_key(
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
    if presented is None or not secrets.compare_digest(
        presented.encode(), configured.get_secret_value().encode()
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing or invalid API key",
            headers={"WWW-Authenticate": "Bearer"},
        )
