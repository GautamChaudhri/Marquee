"""Clients for the API, run in-process with its lifespan."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr

from marquee.api.app import create_app
from marquee.config import settings

API_KEY = "test-api-key-value"
# Nothing listens on port 1: the engine fails at once, without a timeout.
UNREACHABLE_DATABASE = "postgresql+psycopg://marquee:unused@127.0.0.1:1/marquee"


@pytest.fixture
def api_key(monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setattr(settings, "API_KEY", SecretStr(API_KEY))
    return API_KEY


async def _client(database_url: str) -> AsyncIterator[AsyncClient]:
    app = create_app(database_url=database_url)
    # httpx's ASGI transport does not run the lifespan; run it around the client.
    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client,
    ):
        yield client


@pytest.fixture
async def offline_client(api_key: str) -> AsyncIterator[AsyncClient]:
    """The API with a database that does not answer: for what needs no rows."""
    async for client in _client(UNREACHABLE_DATABASE):
        yield client


@pytest.fixture
async def client(api_key: str, database_url: str, db_engine: object) -> AsyncIterator[AsyncClient]:
    """The API on the test database; ``db_engine`` empties the tables afterwards."""
    async for client in _client(database_url):
        yield client
