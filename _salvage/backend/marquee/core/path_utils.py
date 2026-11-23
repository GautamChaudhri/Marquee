"""Filesystem path utilities with traversal hardening.

Every path that originates from an external source (Sonarr/Radarr API,
user config, webhook payload) must pass through the guard in this module
before any filesystem operation.

Defense layers:
  1. Null-byte rejection — null bytes in paths are always malicious.
  2. Path translation — map *arr mount namespace to local filesystem,
     using the correct per-source mapping (radarr or sonarr).
  3. Canonical resolution — ``Path.resolve()`` collapses ``..`` and symlinks.
  4. Logical-root validation — resolved paths must be under the active revision's
     media roots.
  5. Deployment ceiling — editable logical roots must themselves remain under a
     bootstrap-owned ceiling. Empty authorities fail closed.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import cast

from marquee.config import settings

logger = logging.getLogger(__name__)
_BOOTSTRAP_SETTINGS = settings
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_DATA_PATH_KEYS = frozenset(
    {
        "DATA_DIR",
        "METRICS_DISK_PATH",
        "BACKUP_DIR",
        "POSTER_CACHE_DIR",
        "POSTER_STAGING_DIR",
        "POSTER_BACKUP_DIR",
    }
)


def _effective_path_settings():
    # Tests and embedders may deliberately replace this module-level object.
    if settings is not _BOOTSTRAP_SETTINGS:
        return settings
    from marquee.core.runtime_settings import effective_app_settings

    return effective_app_settings()


class PathValidationError(ValueError):
    """Raised when a path fails validation."""


def _canonical_root(raw: str, *, label: str, relative_base: Path | None = None) -> Path:
    if not isinstance(raw, str) or not raw.strip() or "\0" in raw:
        raise PathValidationError(f"{label} must be a non-empty path without null bytes")
    path = Path(raw.strip())
    if not path.is_absolute():
        if relative_base is None:
            raise PathValidationError(f"{label} must be an absolute path")
        path = relative_base / path
    try:
        resolved = path.resolve()
    except (OSError, RuntimeError) as exc:
        raise PathValidationError(f"Could not resolve {label}: {exc}") from exc
    if resolved == Path(resolved.anchor):
        raise PathValidationError(f"{label} cannot grant an entire filesystem root")
    return resolved


def _mapping_targets(config: object, source: str) -> list[str]:
    """Read every Marquee path from a source's new or legacy mapping shape."""
    mappings = getattr(config, f"{source}_path_mappings", None)
    if mappings is None:
        target = getattr(config, f"{source.upper()}_MEDIA_PATH", None)
        return [str(target)] if target else []

    targets: list[str] = []
    for mapping in mappings:
        target = (
            mapping.get("marquee_path")
            if isinstance(mapping, dict)
            else getattr(mapping, "marquee_path", None)
        )
        if target:
            targets.append(str(target))
    return targets


def deployment_media_ceilings() -> tuple[Path, ...]:
    """Return bootstrap-owned media ceilings, including the migration fallback."""

    bootstrap = settings
    configured = list(getattr(bootstrap, "MEDIA_PATH_CEILINGS", None) or [])
    if not configured:
        configured = [
            *(getattr(bootstrap, "MEDIA_ROOTS", None) or []),
            *_mapping_targets(bootstrap, "radarr"),
            *_mapping_targets(bootstrap, "sonarr"),
        ]
    roots = {
        _canonical_root(str(raw), label="deployment media ceiling") for raw in configured if raw
    }
    return tuple(sorted(roots))


def deployment_data_ceiling() -> Path:
    """Return the immutable data ceiling, falling back to bootstrap DATA_DIR."""

    bootstrap = settings
    raw = getattr(bootstrap, "DATA_PATH_CEILING", None) or getattr(bootstrap, "DATA_DIR", None)
    if not raw:
        raise PathValidationError("No deployment data path ceiling is configured")
    return _canonical_root(
        str(raw),
        label="deployment data ceiling",
        relative_base=_PROJECT_ROOT,
    )


def validate_media_path_candidate(raw: str, *, label: str = "media path") -> Path:
    """Validate one editable media path against deployment authority."""

    candidate = _canonical_root(raw, label=label)
    ceilings = deployment_media_ceilings()
    if not ceilings:
        raise PathValidationError(
            "No deployment media path ceiling is configured; media access fails closed"
        )
    if not any(candidate.is_relative_to(ceiling) for ceiling in ceilings):
        raise PathValidationError(f"{label} is outside every deployment media ceiling")
    return candidate


def validate_data_path_candidate(
    raw: str,
    *,
    label: str = "data path",
    relative_base: Path = _PROJECT_ROOT,
    legacy_data_prefix: bool = False,
) -> Path:
    """Validate one editable application-data path against deployment authority."""

    candidate_value = Path(raw.strip())
    if (
        legacy_data_prefix
        and not candidate_value.is_absolute()
        and candidate_value.parts
        and candidate_value.parts[0] == "data"
    ):
        candidate_value = Path(*candidate_value.parts[1:])
    candidate = _canonical_root(str(candidate_value), label=label, relative_base=relative_base)
    if not candidate.is_relative_to(deployment_data_ceiling()):
        raise PathValidationError(f"{label} is outside the deployment data path ceiling")
    return candidate


def _validate_path_mappings(values: dict[str, object], *, key: str, provider: str) -> None:
    mappings = values.get(key)
    if mappings is None:
        return
    if not isinstance(mappings, list):
        raise PathValidationError(f"{key} must be a list of path mappings")

    seen_prefixes: set[Path] = set()
    for index, mapping in enumerate(cast(list[object], mappings)):
        if not isinstance(mapping, dict):
            raise PathValidationError(f"{key}[{index}] must be a path mapping")
        arr_path = mapping.get("arr_path")
        marquee_path = mapping.get("marquee_path")
        if not isinstance(arr_path, str) or not isinstance(marquee_path, str):
            raise PathValidationError(
                f"{provider.title()} mapping {index + 1} needs an Arr path and a Marquee path"
            )
        prefix = _canonical_root(arr_path, label=f"{provider.title()} mapping {index + 1} Arr path")
        if prefix in seen_prefixes:
            raise PathValidationError(f"{provider.title()} mapping prefixes must be unique")
        seen_prefixes.add(prefix)
        validate_media_path_candidate(
            marquee_path,
            label=f"{provider.title()} mapping {index + 1} Marquee path",
        )


def validate_path_configuration(values: dict[str, object]) -> None:
    """Validate the complete effective path document before revision persistence."""

    media_roots = values.get("MEDIA_ROOTS")
    if media_roots is not None and not isinstance(media_roots, list):
        raise PathValidationError("MEDIA_ROOTS must be a list of paths")
    root_values: list[object] = cast(list[object], media_roots) if media_roots else []
    for index, raw in enumerate(root_values):
        validate_media_path_candidate(str(raw), label=f"MEDIA_ROOTS[{index}]")
    for key in ("RADARR_MEDIA_PATH", "SONARR_MEDIA_PATH"):
        raw = values.get(key)
        if raw:
            validate_media_path_candidate(str(raw), label=key)
    for key in ("RADARR_PATH_PREFIX", "SONARR_PATH_PREFIX"):
        raw = values.get(key)
        if raw:
            _canonical_root(str(raw), label=key)
    _validate_path_mappings(values, key="RADARR_PATH_MAPPINGS", provider="radarr")
    _validate_path_mappings(values, key="SONARR_PATH_MAPPINGS", provider="sonarr")
    data_root = validate_data_path_candidate(
        str(values.get("DATA_DIR") or "data"), label="DATA_DIR"
    )
    for key in _DATA_PATH_KEYS - {"DATA_DIR"}:
        raw = values.get(key)
        if raw:
            validate_data_path_candidate(
                str(raw),
                label=key,
                relative_base=data_root,
                legacy_data_prefix=True,
            )


def safe_translate_and_validate(arr_path: str, *, source: str = "radarr") -> Path:
    """Translate an integration path, then classify it under a local media root."""
    if "\0" in arr_path:
        raise PathValidationError(f"Path contains null byte: {arr_path!r}")

    app_settings = _effective_path_settings()
    if source == "sonarr":
        translated = app_settings.translate_sonarr_path(arr_path)
        configured = app_settings.sonarr_path_configured
    else:
        translated = app_settings.translate_radarr_path(arr_path)
        configured = app_settings.radarr_path_configured

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

    ceilings = deployment_media_ceilings()
    if not ceilings:
        raise PathValidationError(
            "No deployment media path ceiling is configured; media access fails closed"
        )
    logical_roots = tuple(
        validate_media_path_candidate(str(root), label="configured logical media root")
        for root in app_settings.effective_media_roots
    )
    if not logical_roots:
        raise PathValidationError("No logical media root is configured; media access fails closed")
    if not any(resolved.is_relative_to(root) for root in logical_roots):
        raise PathValidationError(
            f"Path {str(resolved)!r} is not within any allowed logical media root"
        )
    if not any(resolved.is_relative_to(root) for root in ceilings):
        raise PathValidationError(f"Path {str(resolved)!r} is outside deployment authority")

    return resolved
