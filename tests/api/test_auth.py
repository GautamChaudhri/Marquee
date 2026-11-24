"""The API key. Lifted from the old auth tests; the debug bypass, the loopback bypass, and the
query-string key went with those features."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from marquee.config import settings

PROTECTED = "/api/movies/1"


async def test_health_needs_no_key(offline_client: AsyncClient) -> None:
    response = await offline_client.get("/health/live")
    assert response.status_code == 200


async def test_a_missing_key_is_refused(offline_client: AsyncClient) -> None:
    response = await offline_client.get(PROTECTED)
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


@pytest.mark.parametrize(
    "headers",
    [
        {"X-Api-Key": "not-the-key"},
        {"Authorization": "Bearer not-the-key"},
        {"Authorization": "Basic dGVzdA=="},
        {"Authorization": "Bearer "},
        {"X-Api-Key": ""},
    ],
)
async def test_a_wrong_key_is_refused(offline_client: AsyncClient, headers: dict) -> None:
    response = await offline_client.get(PROTECTED, headers=headers)
    assert response.status_code == 401


async def test_a_key_in_the_query_string_is_refused(
    offline_client: AsyncClient, api_key: str
) -> None:
    for name in ("apikey", "api_key", "key"):
        response = await offline_client.get(PROTECTED, params={name: api_key})
        assert response.status_code == 401


async def test_no_configured_key_answers_503(
    offline_client: AsyncClient, monkeypatch: pytest.MonkeyPatch, api_key: str
) -> None:
    monkeypatch.setattr(settings, "API_KEY", None)
    response = await offline_client.get(PROTECTED, headers={"X-Api-Key": api_key})
    assert response.status_code == 503


async def test_debug_mode_bypasses_nothing(
    offline_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "DEBUG", True)
    assert (await offline_client.get(PROTECTED)).status_code == 401


@pytest.mark.db
@pytest.mark.parametrize("form", ["bearer", "header"])
async def test_both_header_forms_are_accepted(client: AsyncClient, api_key: str, form: str) -> None:
    headers = {"Authorization": f"Bearer {api_key}"} if form == "bearer" else {"X-Api-Key": api_key}
    response = await client.get("/api/movies", headers=headers)
    assert response.status_code == 200
