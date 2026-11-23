"""API-key authentication dependency.

A single static API key guards every route except the health probes. The key may be
presented through either header form:

  * ``Authorization: Bearer <key>``
  * ``X-Api-Key: <key>``  (the convention Radarr/Sonarr use)

The legacy ``?apikey=<key>`` form is restricted to Radarr/Sonarr webhook paths
during the compatibility window because query strings can be retained by access logs.

Enforcement is wired as a global FastAPI dependency in :mod:`marquee.main`.
Behaviour (evaluated in order):

  * ``DEBUG=true``                     → bypass entirely (local development).
  * path is a health probe             → bypass (probes can't send a key).
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
import time
from collections import defaultdict

from fastapi import HTTPException, Request

from marquee.core.runtime_settings import effective_app_settings

# Routes reachable without the global key:
#   /health              — probes / load balancers can't send a key.
_EXEMPT_PATHS = frozenset({"/health", "/health/live", "/health/ready"})

# Hosts treated as same-machine for the AUTH_ALLOW_LOCAL bypass. Tailscale
# (100.64.0.0/10) and LAN addresses are deliberately NOT here.
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})

# ---------------------------------------------------------------------------
# Brute-force throttle (in-memory, per-IP, skipped in DEBUG and for loopback)
# ---------------------------------------------------------------------------
_failure_times: dict[str, list[float]] = defaultdict(list)
_lockout_until: dict[str, float] = {}


def _check_brute_force(ip: str) -> None:
    """Raise 429 if the IP is currently locked out."""
    if time.monotonic() < _lockout_until.get(ip, 0):
        raise HTTPException(status_code=429, detail="Too many failed authentication attempts.")


def _record_failure(ip: str) -> None:
    """Record one failed attempt; apply lockout when the threshold is crossed."""
    now = time.monotonic()
    app_settings = effective_app_settings()
    window = app_settings.AUTH_BRUTE_WINDOW_SECONDS
    times = [t for t in _failure_times[ip] if now - t <= window]
    times.append(now)
    _failure_times[ip] = times
    if len(times) >= app_settings.AUTH_BRUTE_LOCKOUT_ATTEMPTS:
        _lockout_until[ip] = now + app_settings.AUTH_BRUTE_LOCKOUT_SECONDS
        _failure_times[ip] = []


def _clear_failure(ip: str) -> None:
    """Reset the failure counter after a successful authentication."""
    _failure_times.pop(ip, None)
    _lockout_until.pop(ip, None)


def _presented_key(request: Request) -> str | None:
    """Pull the API key from headers or the webhook-only compatibility query."""
    auth = request.headers.get("Authorization")
    if auth and auth.startswith("Bearer "):
        return auth[len("Bearer ") :].strip() or None
    header_key = request.headers.get("X-Api-Key")
    if header_key:
        return header_key.strip() or None
    if request.url.path.startswith("/api/webhooks/"):
        query_key = request.query_params.get("apikey")
        if query_key:
            return query_key.strip() or None
    return None


def _is_loopback(request: Request) -> bool:
    client = request.client  # None under some ASGI transports (e.g. tests)
    return client is not None and client.host in _LOOPBACK_HOSTS


async def require_api_key(request: Request) -> None:
    """Global dependency enforcing the static API key.

    Raises ``401`` (missing/wrong key), ``429`` (brute-force lockout),
    or ``503`` (no key configured).
    """
    # 1. Local development: auth disabled wholesale.
    app_settings = effective_app_settings()
    if app_settings.DEBUG:
        return

    # 2. Always-open routes (health probes).
    if request.url.path in _EXEMPT_PATHS:
        return

    # 3. Same-host tooling may be exempt; tailnet/LAN never is.
    is_loopback = _is_loopback(request)
    if app_settings.AUTH_ALLOW_LOCAL and is_loopback:
        return

    # 4. Brute-force check (skip for loopback so same-host tooling can't self-lockout).
    ip = request.client.host if request.client else None
    if ip and not is_loopback:
        _check_brute_force(ip)

    # 5. Frontend phase will accept a valid httpOnly session cookie here,
    #    before falling through to the API-key check.

    # 6. Fail closed when the operator hasn't configured a key.
    if not app_settings.API_KEY:
        raise HTTPException(
            status_code=503,
            detail="API key not configured — set API_KEY (or run with DEBUG=true).",
        )

    # 7. Validate the presented credential (constant-time).
    presented = _presented_key(request)
    if presented is None or not secrets.compare_digest(presented, app_settings.API_KEY):
        if ip and not is_loopback:
            _record_failure(ip)
        raise HTTPException(
            status_code=401,
            detail="Missing or invalid API key.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Valid key — reset any failure counter for this IP.
    if ip and not is_loopback:
        _clear_failure(ip)
