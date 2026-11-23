"""Cooperative cancellation primitives independent of any job runtime registry."""

from __future__ import annotations

from typing import Protocol


class CancellationSignal(Protocol):
    def is_set(self) -> bool: ...


class JobCancelledError(Exception):
    """Raised by synchronous domain code when cooperative cancellation is requested."""


def raise_if_cancelled(
    signal: CancellationSignal | None, message: str = "operation cancelled"
) -> None:
    if signal is not None and signal.is_set():
        raise JobCancelledError(message)
