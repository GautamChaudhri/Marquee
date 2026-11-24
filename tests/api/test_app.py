from __future__ import annotations

import logging

import pytest
from httpx import ASGITransport, AsyncClient

from marquee.api.app import create_app
from marquee.config import settings


async def test_documentation_only_in_development(monkeypatch: pytest.MonkeyPatch) -> None:
    for environment, expected in (("development", 200), ("production", 404)):
        monkeypatch.setattr(settings, "MARQUEE_ENVIRONMENT", environment)
        app = create_app(database_url="postgresql+psycopg://u:p@127.0.0.1:1/x")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            assert (await c.get("/openapi.json")).status_code == expected
            assert (await c.get("/docs")).status_code == expected
            assert (await c.get("/redoc")).status_code == 404


async def test_every_api_route_requires_the_key(
    offline_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "MARQUEE_ENVIRONMENT", "development")
    schema = create_app(database_url="postgresql+psycopg://u:p@127.0.0.1:1/x").openapi()
    api_paths = [path for path in schema["paths"] if path.startswith("/api")]
    assert len(api_paths) == 26
    operations = [(method, path) for path in api_paths for method in schema["paths"][path]]
    assert len(operations) == 27
    for method, path in operations:
        operation = schema["paths"][path][method]
        assert {name for req in operation["security"] for name in req} == {
            "HTTPBearer",
            "APIKeyHeader",
        }, path
        url = path
        for name in (
            "movie_id",
            "job_id",
            "run_id",
            "candidate_id",
            "deployment_id",
            "event_id",
            "exemplar_id",
        ):
            url = url.replace("{" + name + "}", "1")
        response = await offline_client.request(method.upper(), url)
        assert response.status_code == 401, (method, path)


async def test_requests_are_logged_without_headers_or_query(
    offline_client: AsyncClient, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO, logger="marquee.api.requests"):
        await offline_client.get(
            "/api/movies/1",
            params={"q": "secret-query"},
            headers={"X-Api-Key": "a-presented-key-value"},
        )
    (record,) = [r for r in caplog.records if r.name == "marquee.api.requests"]
    line = record.getMessage()
    assert line.startswith("GET /api/movies/1 401 ")
    assert line.endswith(" ms")
    assert "secret-query" not in caplog.text
    assert "a-presented-key-value" not in caplog.text
