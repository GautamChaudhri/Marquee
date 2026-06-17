"""API-key authentication dependency.

A single static API key guards every route except ``/health``. The key may be
presented three ways so any caller works:

  * ``Authorization: Bearer <key>``
  * ``X-Api-Key: <key>``  (the convention Radarr/Sonarr use)
  * ``?apikey=<key>``     (lets webhook URLs and the browser carry it)

Enforcement is wired as a global FastAPI dependency in :mod:`marquee.main`.
Behaviour (evaluated in order):

  * ``DEBUG=true``                     → bypass entirely (local development).
  * path is ``/health``                → bypass (probes can't send a key).
  * loopback + ``AUTH_ALLOW_LOCAL``    → bypass (same-host tooling).
  * ``API_KEY`` unset (and not DEBUG)  → 503, fail closed.
  * valid key                          → allow.
  * missing / wrong key                → 401.

Matching uses :func:`secrets.compare_digest` (constant-time) so the key can't be
recovered through response-timing differences. ``settings`` is read at request
time, so toggling ``DEBUG``/``API_KEY`` (e.g. in tests) takes effect immediately.
"""

from __future__ import annotations

import secrets

from fastapi import HTTPException, Request

from marquee.config import settings

# Routes reachable without the global key:
#   /health              — probes / load balancers can't send a key.
#   /api/webhooks/subgen — authenticated by its own SUBGEN_CALLBACK_TOKEN instead
#                          (audit-only, off by default; set that token when enabling Subgen).
_EXEMPT_PATHS = frozenset({"/health", "/api/webhooks/subgen"})

# Hosts treated as same-machine for the AUTH_ALLOW_LOCAL bypass. Tailscale
# (100.64.0.0/10) and LAN addresses are deliberately NOT here.
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})


def _presented_key(request: Request) -> str | None:
    """Pull the API key from the Authorization / X-Api-Key headers or query."""
    auth = request.headers.get("Authorization")
    if auth and auth.startswith("Bearer "):
        return auth[len("Bearer ") :].strip() or None
    header_key = request.headers.get("X-Api-Key")
    if header_key:
        return header_key.strip() or None
    query_key = request.query_params.get("apikey")
    if query_key:
        return query_key.strip() or None
    return None


def _is_loopback(request: Request) -> bool:
    client = request.client  # None under some ASGI transports (e.g. tests)
    return client is not None and client.host in _LOOPBACK_HOSTS


async def require_api_key(request: Request) -> None:
    """Global dependency enforcing the static API key.

    Raises ``401`` (missing/wrong key) or ``503`` (no key configured).
    """
    # 1. Local development: auth disabled wholesale.
    if settings.DEBUG:
        return

    # 2. Always-open routes (health probes).
    if request.url.path in _EXEMPT_PATHS:
        return

    # 3. Same-host tooling may be exempt; tailnet/LAN never is.
    if settings.AUTH_ALLOW_LOCAL and _is_loopback(request):
        return

    # 4. Frontend phase will accept a valid httpOnly session cookie here,
    #    before falling through to the API-key check.

    # 5. Fail closed when the operator hasn't configured a key.
    if not settings.API_KEY:
        raise HTTPException(
            status_code=503,
            detail="API key not configured — set API_KEY (or run with DEBUG=true).",
        )

    # 6. Validate the presented credential (constant-time).
    presented = _presented_key(request)
    if presented is None or not secrets.compare_digest(presented, settings.API_KEY):
        raise HTTPException(
            status_code=401,
            detail="Missing or invalid API key.",
            headers={"WWW-Authenticate": "Bearer"},
        )
