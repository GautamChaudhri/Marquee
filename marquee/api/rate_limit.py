"""Requests with a missing or wrong API key, counted per client address.

After ``MAX_FAILURES`` such requests within ``WINDOW_SECONDS`` from one address, that address
gets 429 until the oldest of them is a window old. A request with the right key is never
slowed and clears nothing, and a refused request does not count again, so a client that keeps
trying waits no longer than the window. Held in memory for at most ``MAX_CLIENTS`` addresses;
a restart forgets it. It works as the web app's sign-in limiter does
(``frontend/src/lib/server/rate-limit.ts``).
"""

from __future__ import annotations

import time
from collections.abc import Callable

MAX_FAILURES = 10
WINDOW_SECONDS = 60.0
MAX_CLIENTS = 10_000


class WrongKeyLimiter:
    """Recent wrong-key requests per client address."""

    def __init__(
        self,
        max_failures: int = MAX_FAILURES,
        window_seconds: float = WINDOW_SECONDS,
        max_clients: int = MAX_CLIENTS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.max_failures = max_failures
        self.window_seconds = window_seconds
        self.max_clients = max_clients
        self._clock = clock
        self._failures: dict[str, list[float]] = {}

    def _recent(self, client: str, now: float) -> list[float]:
        kept = [t for t in self._failures.get(client, ()) if now - t < self.window_seconds]
        if kept:
            self._failures[client] = kept
        else:
            self._failures.pop(client, None)
        return kept

    def retry_after(self, client: str) -> float:
        """Seconds until ``client`` may try again; 0 when it may try now."""
        now = self._clock()
        recent = self._recent(client, now)
        if len(recent) < self.max_failures:
            return 0.0
        return recent[-self.max_failures] + self.window_seconds - now

    def fail(self, client: str) -> bool:
        """Count one wrong-key request; True when it is the one that reaches the limit."""
        now = self._clock()
        recent = self._recent(client, now)
        if client not in self._failures and len(self._failures) >= self.max_clients:
            # Forget the oldest address rather than grow without bound.
            del self._failures[next(iter(self._failures))]
        self._failures[client] = [*recent, now]
        return len(recent) + 1 == self.max_failures

    def __len__(self) -> int:
        return len(self._failures)
