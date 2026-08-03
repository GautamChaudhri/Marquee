"""Tests for the global API-key authentication dependency (marquee.api.auth)."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from marquee.config import settings
from marquee.main import app

# A protected, side-effect-free GET — the auth probe. Tests that expect the handler
# to actually run take the ``db`` fixture, which seeds the configuration provider the
# endpoint reads; auth-rejection tests never reach the handler and don't need it.
PROTECTED = "/api/config/pipeline"
KEY = "test-secret-key"


def _client(host: str = "10.10.10.10") -> AsyncClient:
    """Client whose requests appear to come from *host*.

    Defaults to a non-loopback address so the AUTH_ALLOW_LOCAL bypass doesn't
    mask enforcement; pass ``host="127.0.0.1"`` to exercise the loopback path.
    """
    transport = ASGITransport(app=app, client=(host, 12345))
    return AsyncClient(transport=transport, base_url="http://test")


@pytest.fixture
def enforce(monkeypatch):
    """Turn the gate fully ON: DEBUG off, a key set, loopback bypass off."""
    monkeypatch.setattr(settings, "DEBUG", False)
    monkeypatch.setattr(settings, "API_KEY", KEY)
    monkeypatch.setattr(settings, "AUTH_ALLOW_LOCAL", False)


@pytest.mark.asyncio
async def test_health_open_without_key(enforce):
    async with _client() as c:
        resp = await c.get("/health/live")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_protected_requires_key(enforce):
    async with _client() as c:
        resp = await c.get(PROTECTED)
    assert resp.status_code == 401


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("headers", "params"),
    [
        ({"Authorization": f"Bearer {KEY}"}, {}),
        ({"X-Api-Key": KEY}, {}),
    ],
)
async def test_valid_key_accepted(db, enforce, headers, params):
    async with _client() as c:
        resp = await c.get(PROTECTED, headers=headers, params=params)
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_query_string_key_is_rejected_outside_webhook_compatibility(enforce):
    async with _client() as c:
        resp = await c.get(PROTECTED, params={"apikey": KEY})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_wrong_key_rejected(enforce):
    async with _client() as c:
        resp = await c.get(PROTECTED, headers={"X-Api-Key": "not-the-key"})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_loopback_bypass_when_allowed(db, monkeypatch):
    monkeypatch.setattr(settings, "DEBUG", False)
    monkeypatch.setattr(settings, "API_KEY", KEY)
    monkeypatch.setattr(settings, "AUTH_ALLOW_LOCAL", True)
    async with _client(host="127.0.0.1") as c:
        resp = await c.get(PROTECTED)
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_no_key_configured_fails_closed(monkeypatch):
    monkeypatch.setattr(settings, "DEBUG", False)
    monkeypatch.setattr(settings, "API_KEY", None)
    monkeypatch.setattr(settings, "AUTH_ALLOW_LOCAL", False)
    async with _client() as c:
        resp = await c.get(PROTECTED)
    assert resp.status_code == 503


@pytest.mark.asyncio
async def test_debug_bypasses_auth(db, monkeypatch):
    monkeypatch.setattr(settings, "DEBUG", True)
    monkeypatch.setattr(settings, "API_KEY", KEY)
    monkeypatch.setattr(settings, "AUTH_ALLOW_LOCAL", False)
    async with _client() as c:
        resp = await c.get(PROTECTED)
    assert resp.status_code == 200
