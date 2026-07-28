"""Opaque, contract-bound cursor pagination for the canonical job APIs.

A cursor is a base64url JSON envelope binding the keyset position to a
fingerprint of the exact view/filter/sort contract that produced it.  A cursor
presented against a different query fails closed instead of being silently
reinterpreted.  Cursors carry no secrets and no raw transport identifiers.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
from collections.abc import Mapping, Sequence

_CURSOR_VERSION = 1
_MAX_TOKEN_LENGTH = 512
_ALLOWED_KEY_TYPES = (str, int, float, type(None))


class InvalidCursorError(ValueError):
    """The cursor is malformed, oversized, or bound to a different query."""


def cursor_contract(*, view: str, filters: Mapping[str, object], sort: str) -> str:
    """Deterministic fingerprint of the list contract a cursor is valid for."""
    normalized = {
        "view": view,
        "filters": {key: filters[key] for key in sorted(filters) if filters[key] is not None},
        "sort": sort,
    }
    canonical = json.dumps(normalized, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def encode_cursor(*, contract: str, key: Sequence[str | int | float | None]) -> str:
    if any(not isinstance(item, _ALLOWED_KEY_TYPES) for item in key):
        raise InvalidCursorError("cursor keys must be JSON scalars")
    payload = {"v": _CURSOR_VERSION, "c": contract, "k": list(key)}
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    token = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
    if len(token) > _MAX_TOKEN_LENGTH:
        raise InvalidCursorError("cursor key is too large")
    return token


def decode_cursor(token: str, *, contract: str) -> tuple[str | int | float | None, ...]:
    if not token or len(token) > _MAX_TOKEN_LENGTH:
        raise InvalidCursorError("cursor token is empty or too large")
    padding = "=" * (-len(token) % 4)
    try:
        raw = base64.urlsafe_b64decode(token + padding)
        payload = json.loads(raw.decode("utf-8"))
    except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InvalidCursorError("cursor token is malformed") from exc
    if not isinstance(payload, dict) or payload.get("v") != _CURSOR_VERSION:
        raise InvalidCursorError("cursor version is not supported")
    if payload.get("c") != contract:
        raise InvalidCursorError("cursor was issued for a different query")
    key = payload.get("k")
    if not isinstance(key, list) or any(not isinstance(item, _ALLOWED_KEY_TYPES) for item in key):
        raise InvalidCursorError("cursor key is malformed")
    return tuple(key)
