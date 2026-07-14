"""Smoke tests for Phase 0/1 — verify the app boots and health check works."""

import pytest
from httpx import ASGITransport, AsyncClient

from marquee.main import app


@pytest.fixture
async def client():
    """Async test client for the FastAPI app."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.mark.asyncio
async def test_health_check_ok(client: AsyncClient, monkeypatch):
    """The compatibility health route aliases readiness."""
    from marquee.core.jobs import readiness

    async def healthy():
        return {
            "status": "ready",
            "components": {
                "configuration": {"status": "ok"},
                "package": {"status": "ok"},
                "database": {"status": "ok"},
                "migration_lock": {"status": "ok"},
                "schema": {"status": "ok"},
            },
        }

    monkeypatch.setattr(readiness, "check_readiness", healthy)
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ready"


@pytest.mark.asyncio
async def test_liveness_is_database_free(client: AsyncClient, monkeypatch):
    import marquee.database as database

    monkeypatch.setattr(
        database,
        "_get_engine",
        lambda: (_ for _ in ()).throw(AssertionError("liveness touched database")),
    )
    response = await client.get("/health/live")
    assert response.status_code == 200
    assert response.json()["status"] == "live"


@pytest.mark.asyncio
async def test_readiness_returns_503_with_sanitized_components(client: AsyncClient, monkeypatch):
    from marquee.core.jobs import readiness

    async def unhealthy():
        return {
            "status": "not_ready",
            "components": {
                "configuration": {"status": "ok"},
                "package": {"status": "ok"},
                "database": {"status": "unavailable"},
                "migration_lock": {"status": "unavailable"},
                "schema": {"status": "unavailable"},
            },
        }

    monkeypatch.setattr(readiness, "check_readiness", unhealthy)
    response = await client.get("/health/ready")
    assert response.status_code == 503
    assert response.json()["status"] == "not_ready"
    assert "dsn" not in response.text.lower()
