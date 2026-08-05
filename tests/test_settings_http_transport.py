from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

import marquee.api.routes.settings as settings_routes
from marquee.main import app


@pytest.mark.asyncio
async def test_internal_http_connection_test_reaches_provider(
    db, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Authenticated self-hosted HTTP is a supported transport for connection tests."""

    calls: list[tuple[str, str | None, str | None]] = []

    async def probe(provider: str, *, url: str | None, credential: str | None) -> dict[str, str]:
        calls.append((provider, url, credential))
        return {"appName": "TMDB"}

    monkeypatch.setattr(settings_routes, "_test_integration", probe)
    transport = ASGITransport(app=app, client=("192.168.4.25", 12345))
    async with AsyncClient(transport=transport, base_url="http://marquee.internal") as client:
        response = await client.post(
            "/api/settings/integrations/tmdb/test",
            json={"credential": "candidate-key"},
        )

    assert response.status_code == 200
    assert response.json()["status"] == {"appName": "TMDB"}
    assert calls == [("tmdb", None, "candidate-key")]
