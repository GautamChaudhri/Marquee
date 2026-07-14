"""Filesystem path utilities with traversal hardening.

Every path that originates from an external source (Sonarr/Radarr API,
user config, webhook payload) must pass through the guard in this module
before any filesystem operation.

Defense layers:
  1. Null-byte rejection — null bytes in paths are always malicious.
  2. Path translation — map *arr mount namespace to local filesystem,
     using the correct per-source mapping (radarr or sonarr).
  3. Canonical resolution — ``Path.resolve()`` collapses ``..`` and symlinks.
  4. Root validation — resolved path must be within a configured media root
     (auto-derived from ``RADARR_MEDIA_PATH`` / ``SONARR_MEDIA_PATH`` or
     manually set via ``MEDIA_ROOTS``).

When no path mapping is configured and ``MEDIA_ROOTS`` is empty, root
validation is skipped entirely — all paths are allowed (dev/trusted mode).
"""

from __future__ import annotations

import logging
from pathlib import Path

from marquee.config import settings

logger = logging.getLogger(__name__)


class PathValidationError(ValueError):
    """Raised when a path fails validation."""


def safe_translate_and_validate(arr_path: str, *, source: str = "radarr") -> Path:
    """Translate an integration path, then classify it under a local media root."""
    if "\0" in arr_path:
        raise PathValidationError(f"Path contains null byte: {arr_path!r}")

    if source == "sonarr":
        translated = settings.translate_sonarr_path(arr_path)
        configured = settings.sonarr_path_configured
    else:
        translated = settings.translate_radarr_path(arr_path)
        configured = settings.radarr_path_configured

    if configured and translated == arr_path and arr_path:
        logger.warning(
            "Path '%s' did not match configured %s path prefix — not translated",
            arr_path,
            source,
        )

    try:
        resolved = Path(translated).resolve()
    except (OSError, RuntimeError) as exc:
        raise PathValidationError(f"Could not resolve path {translated!r}: {exc}") from exc

    media_roots = settings.effective_media_roots
    if media_roots:
        allowed = []
        for root in media_roots:
            try:
                allowed.append(Path(root).resolve())
            except (OSError, RuntimeError):
                continue
        if not any(resolved.is_relative_to(root) for root in allowed):
            raise PathValidationError(
                f"Path {str(resolved)!r} is not within any allowed media root. "
                f"Allowed roots: {[str(root) for root in allowed]}"
            )

    return resolved
