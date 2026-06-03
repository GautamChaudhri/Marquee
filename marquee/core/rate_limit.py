"""In-memory rate limiter for expensive operations (sync, pipeline).

No external dependencies — just a dict of timestamps keyed by operation name.
"""

from __future__ import annotations

import time


class RateLimiter:
    """Prevent rapid re-triggering of expensive operations.

    Usage::

        limiter = RateLimiter(cooldown_seconds=300)
        if not limiter.check("sync_all"):
            raise HTTPException(429, "Sync already ran recently")
        ...
        limiter.record("sync_all")  # mark as done after successful run
    """

    def __init__(self, cooldown_seconds: int = 300):
        self._cooldown = cooldown_seconds
        self._last_call: dict[str, float] = {}

    def check(self, key: str) -> bool:
        """Return True if the operation is allowed to run.

        Does NOT record the call — call ``record()`` after a successful
        run so a failed attempt doesn't block retries.
        """
        last = self._last_call.get(key, 0)
        if last == 0:
            return True
        elapsed = time.monotonic() - last
        return elapsed >= self._cooldown

    def record(self, key: str) -> None:
        """Mark the operation as having just completed."""
        self._last_call[key] = time.monotonic()

    def remaining(self, key: str) -> float:
        """Seconds until the operation is allowed again. 0 = ready now."""
        last = self._last_call.get(key, 0)
        if last == 0:
            return 0.0
        elapsed = time.monotonic() - last
        return max(0.0, self._cooldown - elapsed)
