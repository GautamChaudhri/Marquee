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
    """Translate a path from *arr's namespace and validate it's safe.

    This is the single entry point for all external paths entering the
    system. Call it once at the boundary (e.g. during sync) and all
    downstream code works with already-validated paths.

    Args:
        arr_path: A filesystem path as reported by Sonarr or Radarr.
        source: Which *arr this path came from — ``"radarr"`` or
            ``"sonarr"``.  Determines which path mapping to apply.

    Returns:
        Resolved ``Path`` object, guaranteed safe to use.

    Raises:
        PathValidationError: If the path contains null bytes, resolves
            outside configured media roots, or is otherwise invalid.
    """
    # ── Layer 1: reject null bytes ───────────────────────────────────
    if "\0" in arr_path:
        raise PathValidationError(f"Path contains null byte: {arr_path!r}")

    # ── Layer 2: translate *arr mount prefix → local mount prefix ────
    if source == "sonarr":
        translated = settings.translate_sonarr_path(arr_path)
        configured = settings.sonarr_path_configured
    else:
        translated = settings.translate_radarr_path(arr_path)
        configured = settings.radarr_path_configured

    # If mapping was configured but the path didn't start with the
    # expected prefix, log a warning and let it pass through —
    # the root check (Layer 4) will still gate it.
    if configured and translated == arr_path and arr_path:
        logger.warning(
            "Path '%s' did not match configured %s path prefix — not translated",
            arr_path,
            source,
        )

    # ── Layer 3: resolve to canonical path (collapses .. and symlinks)
    try:
        resolved = Path(translated).resolve()
    except (OSError, RuntimeError) as exc:
        raise PathValidationError(
            f"Could not resolve path {translated!r}: {exc}"
        ) from exc

    # ── Layer 4: validate against effective media roots ───────────────
    media_roots = settings.effective_media_roots
    if media_roots:  # only enforce if roots are configured
        resolved_str = str(resolved)
        if not any(
            resolved_str.startswith(str(root)) for root in media_roots
        ):
            raise PathValidationError(
                f"Path {resolved_str!r} is not within any allowed media root. "
                f"Allowed roots: {[str(r) for r in media_roots]}"
            )

    return resolved
