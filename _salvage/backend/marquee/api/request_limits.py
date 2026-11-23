"""Streaming request-body limits enforced at the ASGI ingress boundary.

A pure-ASGI wrapper around ``receive`` counts the actual ``http.request`` body
bytes; ``Content-Length`` is only an early validation *hint* and never the
byte-count authority (C12). Limits come from a code-owned catalog keyed by the
normalized HTTP method plus path (C15); a client can never request a larger cap,
and an unmatched route falls back to the conservative global default rather than
a larger value (C14). Bodyless methods use a zero cap.

Enforcement never buffers the body: once the running count would exceed the cap
the wrapper sends one canonical 413 and returns ``http.disconnect`` to the inner
application so the route commits no work, mirroring Starlette's streaming
request contract (https://www.starlette.io/requests/). Response streaming (SSE
and bounded downloads) is untouched because those responses carry no request
body, not through any generic path bypass (C16).
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any

from marquee.core.runtime_settings import effective_settings as settings

Message = dict[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
ASGIApp = Callable[[Message, Receive, Send], Awaitable[None]]

# Methods that never carry a request body get a zero cap: any body is rejected.
_BODYLESS_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})

# Code-owned per-endpoint caps keyed by (normalized method, path). Values are
# authoritative and code-owned; no client input is ever consulted. There is no
# multipart/upload endpoint in the product API today, so the table is empty and
# every body-bearing route uses the conservative global default. Any future
# entry stays bounded here — it is the only place a non-default cap may live.
_ENDPOINT_LIMITS: dict[tuple[str, str], int] = {}


def request_limit(method: str, path: str) -> int:
    """Return the code-owned byte cap for a normalized request identity.

    Unmatched routes fall back to the global default, never a larger value.
    """
    method = method.upper()
    if method in _BODYLESS_METHODS:
        return 0
    key = (method, path.rstrip("/") or "/")
    override = _ENDPOINT_LIMITS.get(key)
    if override is not None:
        return override
    return settings.MAX_REQUEST_BODY_BYTES


_INVALID_LENGTH = object()


def _declared_length(scope: Message) -> int | None | object:
    """Parse the declared Content-Length hint.

    Returns ``None`` when absent, the sentinel ``_INVALID_LENGTH`` when malformed
    or conflicting, otherwise the non-negative integer value.
    """
    values = [
        value.decode("latin-1").strip()
        for name, value in scope.get("headers", [])
        if name == b"content-length"
    ]
    if not values:
        return None
    if len(set(values)) != 1:
        return _INVALID_LENGTH
    text = values[0]
    # ``str.isdigit`` rejects empty, negative, signed, and non-decimal values.
    if not text.isdigit():
        return _INVALID_LENGTH
    return int(text)


def _error_messages(status: int, code: str, detail: str) -> list[Message]:
    body = json.dumps({"detail": detail, "code": code}).encode()
    return [
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
            ],
        },
        {"type": "http.response.body", "body": body, "more_body": False},
    ]


async def _send_error(send: Send, status: int, code: str, detail: str) -> None:
    for message in _error_messages(status, code, detail):
        await send(message)


_TOO_LARGE = (413, "request_body_too_large", "Request body exceeds the maximum allowed size.")
_BAD_LENGTH = (400, "invalid_content_length", "Malformed or conflicting Content-Length header.")


class RequestBodyLimitMiddleware:
    """Pure-ASGI request-body limiter.

    Validates the declared length as an early hint, counts actual body bytes, and
    rejects an over-limit body with a canonical 413 without buffering, invoking a
    handler, or touching response streaming.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Message, receive: Receive, send: Send) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        limit = request_limit(scope["method"], scope.get("path", ""))

        declared = _declared_length(scope)
        if declared is _INVALID_LENGTH:
            await _send_error(send, *_BAD_LENGTH)
            return
        if isinstance(declared, int) and declared > limit:
            await _send_error(send, *_TOO_LARGE)
            return

        state = {"received": 0, "app_started": False, "answered": False, "over": False}

        async def wrapped_receive() -> Message:
            # Once over-limit, stop forwarding body bytes; the inner app sees a
            # clean disconnect and unwinds without committing work.
            if state["over"]:
                return {"type": "http.disconnect"}
            message = await receive()
            if message.get("type") == "http.request":
                state["received"] += len(message.get("body", b""))
                if state["received"] > limit:
                    state["over"] = True
                    if not state["app_started"] and not state["answered"]:
                        await _send_error(send, *_TOO_LARGE)
                        state["answered"] = True
                    return {"type": "http.disconnect"}
            return message

        async def wrapped_send(message: Message) -> None:
            # Our 413 is authoritative; swallow anything the unwinding app emits.
            if state["answered"]:
                return
            if message.get("type") == "http.response.start":
                state["app_started"] = True
            await send(message)

        try:
            await self.app(scope, wrapped_receive, wrapped_send)
        except Exception:
            # A disconnect-driven unwind (e.g. Starlette ClientDisconnect) after
            # we already answered is expected and owned here; anything else
            # propagates to the normal error handling.
            if state["answered"]:
                return
            raise
