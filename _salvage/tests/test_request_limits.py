"""Streaming request-body limit enforcement at the ASGI ingress.

Unit cases drive the pure-ASGI middleware with scripted receive/send messages; integration
cases mount a real FastAPI app through httpx ASGITransport to prove that rejection invokes
no handler, that actual bytes rather than Content-Length are authoritative, and that
response streaming is untouched."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse
from httpx import ASGITransport, AsyncClient

from marquee.api.request_limits import (
    RequestBodyLimitMiddleware,
    request_limit,
)
from marquee.config import settings

Message = dict[str, Any]


# ---------------------------------------------------------------------------
# Unit harness — scripted ASGI messages
# ---------------------------------------------------------------------------


def _record_app(record: dict[str, int]):
    """An inner ASGI app that fully reads the body before it 'commits' work."""

    async def app(scope: Message, receive, send) -> None:
        record["invoked"] += 1
        body = b""
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                # Mirror Starlette's ClientDisconnect on a truncated stream.
                raise RuntimeError("client disconnected")
            if message["type"] == "http.request":
                body += message.get("body", b"")
                if not message.get("more_body", False):
                    break
        record["committed"] += 1
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": body, "more_body": False})

    return app


async def _drive(scope: Message, incoming: list[Message]) -> tuple[list[Message], dict[str, int]]:
    record = {"invoked": 0, "committed": 0}
    middleware = RequestBodyLimitMiddleware(_record_app(record))
    queue = list(incoming)
    sent: list[Message] = []

    async def receive() -> Message:
        if queue:
            return queue.pop(0)
        return {"type": "http.disconnect"}

    async def send(message: Message) -> None:
        sent.append(message)

    await middleware(scope, receive, send)
    return sent, record


def _http_scope(method: str = "POST", path: str = "/api/x", **headers: str) -> Message:
    header_pairs = [(k.encode().lower(), v.encode()) for k, v in headers.items()]
    return {"type": "http", "method": method, "path": path, "headers": header_pairs}


def _body(chunk: bytes, more: bool = False) -> Message:
    return {"type": "http.request", "body": chunk, "more_body": more}


def _status_of(sent: list[Message]) -> int:
    return next(m["status"] for m in sent if m["type"] == "http.response.start")


def _payload_of(sent: list[Message]) -> dict[str, Any]:
    raw = next(m["body"] for m in sent if m["type"] == "http.response.body")
    return json.loads(raw)


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------


def test_bodyless_methods_get_zero_cap() -> None:
    for method in ("GET", "HEAD", "OPTIONS", "TRACE", "get"):
        assert request_limit(method, "/api/anything") == 0


def test_unmatched_route_uses_global_default() -> None:
    assert request_limit("POST", "/api/whatever") == settings.MAX_REQUEST_BODY_BYTES
    # Trailing slash is normalized to the same key.
    assert request_limit("POST", "/api/whatever/") == settings.MAX_REQUEST_BODY_BYTES


# ---------------------------------------------------------------------------
# Declared-length validation (early hint only)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_declared_oversize_rejects_before_handler(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "MAX_REQUEST_BODY_BYTES", 3)
    sent, record = await _drive(_http_scope(**{"content-length": "4"}), [_body(b"")])
    assert _status_of(sent) == 413
    assert _payload_of(sent)["code"] == "request_body_too_large"
    assert record["invoked"] == 0 and record["committed"] == 0


@pytest.mark.asyncio
async def test_conflicting_content_length_returns_400() -> None:
    # Two differing declared lengths cannot both be authoritative.
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/x",
        "headers": [(b"content-length", b"3"), (b"content-length", b"9")],
    }
    sent, record = await _drive(scope, [_body(b"")])
    assert _status_of(sent) == 400
    assert _payload_of(sent)["code"] == "invalid_content_length"
    assert record["invoked"] == 0


@pytest.mark.asyncio
async def test_negative_content_length_returns_400() -> None:
    sent, record = await _drive(_http_scope(**{"content-length": "-1"}), [_body(b"")])
    assert _status_of(sent) == 400
    assert record["invoked"] == 0


@pytest.mark.asyncio
async def test_non_decimal_content_length_returns_400() -> None:
    sent, _ = await _drive(_http_scope(**{"content-length": "12x"}), [_body(b"")])
    assert _status_of(sent) == 400


@pytest.mark.asyncio
async def test_identical_duplicate_content_length_is_accepted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "MAX_REQUEST_BODY_BYTES", 10)
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/x",
        "headers": [(b"content-length", b"2"), (b"content-length", b"2")],
    }
    sent, record = await _drive(scope, [_body(b"ok")])
    assert _status_of(sent) == 200
    assert record["committed"] == 1


# ---------------------------------------------------------------------------
# Actual-byte enforcement (the authority)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_exactly_at_limit_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "MAX_REQUEST_BODY_BYTES", 3)
    sent, record = await _drive(_http_scope(), [_body(b"abc")])
    assert _status_of(sent) == 200
    assert record["committed"] == 1


@pytest.mark.asyncio
async def test_one_byte_over_rejects(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "MAX_REQUEST_BODY_BYTES", 3)
    sent, record = await _drive(_http_scope(), [_body(b"abcd")])
    assert _status_of(sent) == 413
    assert record["committed"] == 0


@pytest.mark.asyncio
async def test_chunked_omitted_length_enforced_by_actual_bytes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "MAX_REQUEST_BODY_BYTES", 3)
    # No Content-Length header at all; over-limit only detectable by counting.
    sent, record = await _drive(_http_scope(), [_body(b"ab", more=True), _body(b"cd", more=False)])
    assert _status_of(sent) == 413
    assert record["invoked"] == 1  # entered, but never committed
    assert record["committed"] == 0


@pytest.mark.asyncio
async def test_lying_small_content_length_is_not_trusted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "MAX_REQUEST_BODY_BYTES", 3)
    # Declared length passes the early hint but the real body is over-limit.
    sent, record = await _drive(_http_scope(**{"content-length": "2"}), [_body(b"abcdef")])
    assert _status_of(sent) == 413
    assert record["committed"] == 0


@pytest.mark.asyncio
async def test_within_limit_streams_to_handler(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "MAX_REQUEST_BODY_BYTES", 10)
    sent, record = await _drive(_http_scope(), [_body(b"ab", more=True), _body(b"cd", more=False)])
    assert _status_of(sent) == 200
    assert record["committed"] == 1


@pytest.mark.asyncio
async def test_non_http_scope_passes_through() -> None:
    seen: list[str] = []

    async def inner(scope: Message, receive, send) -> None:
        seen.append(scope["type"])

    mw = RequestBodyLimitMiddleware(inner)
    await mw({"type": "lifespan"}, None, None)  # type: ignore[arg-type]
    assert seen == ["lifespan"]


# ---------------------------------------------------------------------------
# Integration — real FastAPI app through ASGITransport
# ---------------------------------------------------------------------------


def _build_app() -> tuple[FastAPI, dict[str, int]]:
    app = FastAPI()
    calls = {"count": 0}

    @app.post("/echo")
    async def echo(request: Request) -> dict[str, int]:
        data = await request.body()
        calls["count"] += 1
        return {"len": len(data)}

    @app.get("/stream")
    async def stream() -> StreamingResponse:
        async def gen() -> AsyncIterator[bytes]:
            for _ in range(4):
                yield b"chunk"

        return StreamingResponse(gen(), media_type="text/plain")

    # Header middleware sits OUTSIDE the limiter, mirroring marquee.main ordering.
    app.add_middleware(RequestBodyLimitMiddleware)

    @app.middleware("http")
    async def mark(request: Request, call_next):  # type: ignore[no-untyped-def]
        response = await call_next(request)
        response.headers["X-Ingress"] = "1"
        return response

    return app, calls


def _client(app: FastAPI) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.mark.asyncio
async def test_integration_at_limit_reaches_handler(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "MAX_REQUEST_BODY_BYTES", 8)
    app, calls = _build_app()
    async with _client(app) as ac:
        resp = await ac.post("/echo", content=b"12345678")
    assert resp.status_code == 200
    assert resp.json() == {"len": 8}
    assert calls["count"] == 1


@pytest.mark.asyncio
async def test_integration_over_limit_invokes_no_handler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "MAX_REQUEST_BODY_BYTES", 8)
    app, calls = _build_app()
    async with _client(app) as ac:
        resp = await ac.post("/echo", content=b"123456789")
    assert resp.status_code == 413
    assert resp.json()["code"] == "request_body_too_large"
    # The header middleware still wrapped the early response.
    assert resp.headers.get("X-Ingress") == "1"
    assert calls["count"] == 0  # handler never committed


@pytest.mark.asyncio
async def test_integration_streaming_request_over_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "MAX_REQUEST_BODY_BYTES", 8)
    app, calls = _build_app()

    async def gen() -> AsyncIterator[bytes]:
        yield b"aaaa"
        yield b"bbbb"
        yield b"cccc"  # pushes over the 8-byte cap; no Content-Length

    async with _client(app) as ac:
        resp = await ac.post("/echo", content=gen())
    assert resp.status_code == 413
    assert calls["count"] == 0


@pytest.mark.asyncio
async def test_integration_response_streaming_untouched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "MAX_REQUEST_BODY_BYTES", 8)
    app, _ = _build_app()
    async with _client(app) as ac:
        resp = await ac.get("/stream")
    assert resp.status_code == 200
    assert resp.text == "chunk" * 4


@pytest.mark.asyncio
async def test_integration_concurrent_uploads_stay_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "MAX_REQUEST_BODY_BYTES", 8)
    app, calls = _build_app()
    async with _client(app) as ac:
        results = await asyncio.gather(*(ac.post("/echo", content=b"x" * 32) for _ in range(6)))
    assert all(r.status_code == 413 for r in results)
    assert calls["count"] == 0
