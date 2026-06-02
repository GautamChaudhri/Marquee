"""Filesystem path utilities with traversal hardening.

Every path that originates from an external source (Sonarr/Radarr API,
user config, webhook payload) must pass through the guard in this module
before any filesystem operation.

Defense layers:
  1. Null-byte rejection — null bytes in paths are always malicious.
  2. Path translation — map *arr mount namespace to local filesystem.
  3. Canonical resolution — ``Path.resolve()`` collapses ``..`` and symlinks.
  4. Root validation — resolved path must be within a configured media root.
"""

from __future__ import annotations

from pathlib import Path

from marquee.config import settings


class PathValidationError(ValueError):
    """Raised when a path fails validation."""


def safe_translate_and_validate(arr_path: str) -> Path:
    """Translate a path from *arr's namespace and validate it's safe.

    This is the single entry point for all external paths entering the
    system. Call it once at the boundary (e.g. during sync) and all
    downstream code works with already-validated paths.

    Args:
        arr_path: A filesystem path as reported by Sonarr or Radarr.

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
    local = settings.translate_arr_path(arr_path)

    # ── Layer 3: resolve to canonical path (collapses .. and symlinks)
    try:
        resolved = Path(local).resolve()
    except (OSError, RuntimeError) as exc:
        raise PathValidationError(
            f"Could not resolve path {local!r}: {exc}"
        ) from exc

    # ── Layer 4: validate against allowed media roots ────────────────
    resolved_str = str(resolved)
    if not any(resolved_str.startswith(root) for root in settings.MEDIA_ROOTS):
        raise PathValidationError(
            f"Path {resolved_str!r} is not within any allowed media root. "
            f"Allowed roots: {settings.MEDIA_ROOTS}"
        )

    return resolved
