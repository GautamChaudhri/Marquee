"""Health probes: no key, no host name, no path, no version."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from marquee.api.app import create_app


async def test_live_answers_without_the_database(offline_client: AsyncClient) -> None:
    response = await offline_client.get("/health/live")
    assert (response.status_code, response.json()) == (200, {"status": "ok"})


async def test_ready_is_503_when_the_database_does_not_answer(
    offline_client: AsyncClient,
) -> None:
    response = await offline_client.get("/health/ready")
    assert response.status_code == 503
    assert response.json() == {"status": "not ready", "reason": "the database does not answer"}


@pytest.mark.db
async def test_ready_when_the_database_is_at_head(client: AsyncClient) -> None:
    response = await client.get("/health/ready")
    assert (response.status_code, response.json()) == (200, {"status": "ready", "reason": None})


@pytest.mark.db
async def test_ready_is_503_when_the_schema_is_behind(empty_database_url: str) -> None:
    app = create_app(database_url=empty_database_url)
    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client,
    ):
        response = await client.get("/health/ready")
    assert response.status_code == 503
    assert "marquee db upgrade" in response.json()["reason"]


async def test_health_reveals_no_host_path_or_version(offline_client: AsyncClient) -> None:
    for path in ("/health/live", "/health/ready"):
        body = (await offline_client.get(path)).text
        assert "127.0.0.1" not in body
        assert "/" not in body.replace("`marquee db upgrade`", "")
        assert "0001" not in body
