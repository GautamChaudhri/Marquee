"""Smoke tests for Phase 0 — verify the app boots and models work."""

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
async def test_health_check(client: AsyncClient):
    """The /health endpoint should return 200 with project metadata."""
    response = await client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["project"] == "Marquee"
    assert "version" in data
