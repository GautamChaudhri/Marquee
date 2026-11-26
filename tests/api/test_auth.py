"""The API key. Lifted from the old auth tests; the debug bypass, the loopback bypass, and the
query-string key went with those features."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient

from marquee.api.app import create_app
from marquee.api.rate_limit import WrongKeyLimiter
from marquee.config import settings

PROTECTED = "/api/movies/1"
# Nothing listens on port 1 (as in conftest): for what needs no rows.
UNREACHABLE_DATABASE = "postgresql+psycopg://marquee:unused@127.0.0.1:1/marquee"


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


# ── Wrong keys are slowed ───────────────────────────────────────────────────


class _Clock:
    def __init__(self) -> None:
        self.now = 5000.0

    def __call__(self) -> float:
        return self.now


async def _client_with(
    database_url: str, limiter: WrongKeyLimiter, address: str = "203.0.113.7"
) -> AsyncIterator[AsyncClient]:
    app = create_app(database_url=database_url, wrong_keys=limiter)
    transport = ASGITransport(app=app, client=(address, 40000))
    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=transport, base_url="http://test") as client,
    ):
        yield client


async def test_the_eleventh_wrong_key_in_a_minute_waits_until_the_minute_has_passed(
    api_key: str, caplog: pytest.LogCaptureFixture
) -> None:
    clock = _Clock()
    limiter = WrongKeyLimiter(clock=clock)
    async for client in _client_with(UNREACHABLE_DATABASE, limiter):
        with caplog.at_level(logging.WARNING, logger="marquee.api.auth"):
            for attempt in range(10):
                headers = {} if attempt % 2 else {"X-Api-Key": "not-the-key"}
                assert (await client.get(PROTECTED, headers=headers)).status_code == 401
                clock.now += 2
        assert "10 requests with a missing or wrong key from 203.0.113.7" in caplog.text
        for headers in ({"X-Api-Key": "not-the-key"}, {}, {"Authorization": "Bearer x"}):
            refused = await client.get(PROTECTED, headers=headers)
            assert refused.status_code == 429
            # The first wrong key was 20 s ago: 40 s to go.
            assert refused.headers["retry-after"] == "40"
        # Refused requests do not count again: the wait ends when the minute has passed.
        clock.now += 39.5
        assert (await client.get(PROTECTED)).headers["retry-after"] == "1"
        clock.now += 0.5
        # The oldest failure is a minute old: one more try is allowed, then it waits again.
        assert (await client.get(PROTECTED)).status_code == 401
        assert (await client.get(PROTECTED)).status_code == 429
        # The health probes are not counted and not refused.
        assert (await client.get("/health/live")).status_code == 200


async def test_another_address_is_not_slowed(api_key: str) -> None:
    limiter = WrongKeyLimiter(clock=_Clock())
    async for client in _client_with(UNREACHABLE_DATABASE, limiter, "198.51.100.1"):
        for _ in range(11):
            await client.get(PROTECTED)
        assert (await client.get(PROTECTED)).status_code == 429
    async for client in _client_with(UNREACHABLE_DATABASE, limiter, "198.51.100.2"):
        assert (await client.get(PROTECTED)).status_code == 401


async def test_no_configured_key_is_not_counted(
    api_key: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    limiter = WrongKeyLimiter(clock=_Clock())
    monkeypatch.setattr(settings, "API_KEY", None)
    async for client in _client_with(UNREACHABLE_DATABASE, limiter):
        for _ in range(12):
            assert (await client.get(PROTECTED)).status_code == 503
    assert len(limiter) == 0


@pytest.mark.db
async def test_the_right_key_is_never_slowed_and_clears_nothing(
    api_key: str, database_url: str, db_engine: object
) -> None:
    clock = _Clock()
    limiter = WrongKeyLimiter(clock=clock)
    async for client in _client_with(database_url, limiter):
        for _ in range(10):
            assert (await client.get("/api/movies")).status_code == 401
        assert (await client.get("/api/movies")).status_code == 429
        for _ in range(3):
            right = await client.get("/api/movies", headers={"X-Api-Key": api_key})
            assert right.status_code == 200
        assert (await client.get("/api/movies")).status_code == 429
