"""Shared validation for remote poster downloads (TMDB CDN).

Every path that writes downloaded bytes to disk funnels through
``ensure_image_response`` first: the body must declare an image
content-type and fit under a hard size cap, so a misbehaving or
poisoned CDN can't plant HTML/binary junk or multi-GB files in the
staging dirs, cache, or media folders.
"""

from __future__ import annotations

import httpx

# Far above any real TMDB original (typically < 2 MB) but low enough to
# bound disk usage across concurrent batch downloads.
MAX_POSTER_BYTES = 20 * 1024 * 1024


class DownloadRejectedError(ValueError):
    """The response is not a poster we are willing to persist."""


def ensure_image_response(response: httpx.Response) -> None:
    """Raise DownloadRejectedError unless the response looks like a sane image."""
    content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if not content_type.startswith("image/"):
        raise DownloadRejectedError(
            f"expected image/* content-type, got {content_type or 'unknown'!r}"
        )
    declared = response.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > MAX_POSTER_BYTES:
        raise DownloadRejectedError(
            f"poster too large: {declared} bytes (cap {MAX_POSTER_BYTES})"
        )
    if len(response.content) > MAX_POSTER_BYTES:
        raise DownloadRejectedError(
            f"poster too large: {len(response.content)} bytes (cap {MAX_POSTER_BYTES})"
        )
