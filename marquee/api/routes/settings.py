"""Unified, redacted Settings API over revisions and encrypted credentials."""

from __future__ import annotations

import ipaddress
import os
from pathlib import Path
from typing import Annotated, Any, Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from marquee import __version__
from marquee.config import Settings, settings
from marquee.core.configuration import (
    CONFIGURATION_CATALOG,
    ConfigurationError,
    ConfigurationVersionConflictError,
    update_configuration,
)
from marquee.core.configuration_cache import configuration_provider
from marquee.core.integration_settings import (
    IntegrationCapability,
    read_integration_settings_snapshot,
)
from marquee.core.integration_urls import (
    IntegrationURLValidationError,
    normalize_integration_url,
    same_integration_origin,
)
from marquee.core.managed_secrets import (
    ManagedSecretError,
    ManagedSecretGenerationConflictError,
    ManagedSecretUnavailableError,
    clear_managed_secret,
    managed_secret_provider,
    managed_secret_statuses,
    managed_secret_store_health,
    replace_managed_secret,
    require_managed_secret_generation,
)
from marquee.core.path_utils import (
    PathValidationError,
    validate_media_path_candidate,
)
from marquee.core.pipeline_config import pipeline_settings
from marquee.core.poster_naming import normalize_poster_template
from marquee.database import get_db

router = APIRouter(prefix="/api/settings", tags=["settings"])

_PROVIDER_SECRET_KEYS = {
    "tmdb": "TMDB_READ_ACCESS_TOKEN",
    "radarr": "RADARR_API_KEY",
    "sonarr": "SONARR_API_KEY",
}
_PROVIDER_URL_KEYS = {"radarr": "RADARR_URL", "sonarr": "SONARR_URL"}
_INTEGRATION_URL_KEYS = frozenset(_PROVIDER_URL_KEYS.values())


def _configured(value: object) -> bool:
    return value is not None and value != ""


def _serialize(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (tuple, set)):
        return [_serialize(item) for item in value]
    if isinstance(value, list):
        return [_serialize(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _serialize(item) for key, item in value.items()}
    return value


def _source_for(key: str, stored_values: dict[str, Any], secret_sources: dict[str, str]) -> str:
    entry = CONFIGURATION_CATALOG[key]
    if entry.storage == "secret_store":
        return secret_sources[key]
    if entry.storage == "deployment":
        return "deployment"
    if key in stored_values:
        return "custom"
    base = settings if entry.owner == "app" else pipeline_settings
    return "environment" if key in base.model_fields_set else "default"


def _catalog_payload(
    *,
    state_values: dict[str, Any],
    secret_sources: dict[str, str],
) -> tuple[dict[str, dict[str, Any]], dict[str, Any], dict[str, Any], dict[str, str]]:
    effective = {
        "app": configuration_provider.effective("app"),
        "pipeline": configuration_provider.effective("pipeline"),
    }
    catalog: dict[str, dict[str, Any]] = {}
    values: dict[str, Any] = {}
    defaults: dict[str, Any] = {}
    sources: dict[str, str] = {}
    for key, entry in CONFIGURATION_CATALOG.items():
        field = entry.validation_owner.model_fields[key]
        catalog[key] = {
            "key": key,
            "title": entry.title,
            "description": entry.description,
            "owner": entry.owner,
            "scope": entry.scope,
            "storage": entry.storage,
            "sensitivity": entry.sensitivity,
            "apply_mode": entry.apply_mode,
            "tab": entry.tab,
            "section": entry.section,
            "level": entry.level,
            "control": entry.control,
            "visible": entry.visible,
            "editable": entry.editable,
        }
        sources[key] = _source_for(key, state_values, secret_sources)
        if not entry.visible or entry.sensitivity == "secret":
            continue
        if entry.storage == "deployment" and entry.sensitivity == "private":
            continue
        values[key] = _serialize(effective[entry.owner][key])
        if entry.storage == "revision":
            defaults[key] = _serialize(field.get_default(call_default_factory=True))
    return catalog, values, defaults, sources


def _legacy_configuration_meta() -> dict[str, dict[str, Any]]:
    return {
        key: {
            "owner": "database" if entry.storage == "revision" else "environment",
            "storage": entry.storage,
            "apply_mode": entry.apply_mode,
            "sensitivity": entry.sensitivity,
            "scope": entry.scope,
        }
        for key, entry in CONFIGURATION_CATALOG.items()
        if entry.owner == "app"
    }


def _container_path_facts(app_settings: Settings) -> list[dict[str, Any]]:
    """Expose container-side path health without revealing host mount sources."""
    candidates = [
        app_settings.DATA_DIR,
        *app_settings.MEDIA_ROOTS,
        app_settings.POSTER_CACHE_DIR,
        app_settings.POSTER_STAGING_DIR,
        app_settings.POSTER_BACKUP_DIR,
    ]
    if app_settings.METRICS_DISK_PATH:
        candidates.append(app_settings.METRICS_DISK_PATH)
    facts: list[dict[str, Any]] = []
    seen: set[str] = set()
    for value in candidates:
        path = Path(value).expanduser()
        display = str(path)
        if display in seen:
            continue
        seen.add(display)
        facts.append(
            {
                "path": display,
                "exists": path.exists(),
                "readable": path.exists() and os.access(path, os.R_OK),
                "writable": path.exists() and os.access(path, os.W_OK),
            }
        )
    return facts


@router.get("")
async def get_settings(db: Annotated[AsyncSession, Depends(get_db)]):
    """Return one redacted Settings document for every frontend tab."""
    state = configuration_provider.state
    provider_health = configuration_provider.health()
    secret_status = await managed_secret_statuses(db)
    secret_sources = {key: value["source"] for key, value in secret_status.items()}
    catalog, values, defaults, sources = _catalog_payload(
        state_values=state.values,
        secret_sources=secret_sources,
    )
    app_values = configuration_provider.effective("app")
    # This read endpoint never needs plaintext credentials. Status rows below
    # are authoritative and keep GET /settings useful even during key rotation.
    app_values.update(dict.fromkeys(_PROVIDER_SECRET_KEYS.values()))
    app_settings = Settings(**app_values)
    tmdb_status = secret_status["TMDB_READ_ACCESS_TOKEN"]
    radarr_status = secret_status["RADARR_API_KEY"]
    sonarr_status = secret_status["SONARR_API_KEY"]

    return {
        "configuration_version": state.version,
        "etag": state.etag,
        "stale": provider_health["status"] != "valid",
        "health": provider_health,
        "catalog": catalog,
        "values": values,
        "defaults": defaults,
        "sources": sources,
        "secrets": secret_status,
        "secret_store": managed_secret_store_health(),
        "deployment": {
            "version": __version__,
            "environment": settings.MARQUEE_ENVIRONMENT,
            "process_role": settings.MARQUEE_PROCESS_ROLE,
            "host": settings.HOST,
            "port": settings.PORT,
            "debug": settings.DEBUG,
            "database_configured": bool(settings.DB_URL),
            "api_key_configured": bool(settings.API_KEY),
            "keyring_configured": settings.MARQUEE_SETTINGS_KEYRING_FILE is not None,
            "mounts": _container_path_facts(app_settings),
        },
        # Compatibility view consumed by the current dashboard during the UI cutover.
        "configuration_meta": _legacy_configuration_meta(),
        "app": {
            "name": app_settings.APP_NAME,
            "host": app_settings.HOST,
            "port": app_settings.PORT,
            "debug": app_settings.DEBUG,
            "log_level": app_settings.LOG_LEVEL,
            "log_format": app_settings.LOG_FORMAT,
            "cors_origins": app_settings.CORS_ORIGINS,
            "auth": {
                "api_key_configured": _configured(app_settings.API_KEY),
                "allow_local": app_settings.AUTH_ALLOW_LOCAL,
            },
        },
        "integrations": {
            "tmdb": {"configured": tmdb_status["configured"]},
            "radarr": {
                "configured": _configured(app_settings.RADARR_URL) and radarr_status["configured"],
                "url_configured": _configured(app_settings.RADARR_URL),
                "api_key_configured": radarr_status["configured"],
                "path_mapping_configured": app_settings.radarr_path_configured,
            },
            "sonarr": {
                "configured": _configured(app_settings.SONARR_URL) and sonarr_status["configured"],
                "url_configured": _configured(app_settings.SONARR_URL),
                "api_key_configured": sonarr_status["configured"],
                "path_mapping_configured": app_settings.sonarr_path_configured,
            },
        },
        "paths": {
            "data_dir": app_settings.DATA_DIR,
            "media_roots": app_settings.MEDIA_ROOTS,
            "radarr_path_prefix_configured": _configured(app_settings.RADARR_PATH_PREFIX),
            "radarr_media_path_configured": _configured(app_settings.RADARR_MEDIA_PATH),
            "sonarr_path_prefix_configured": _configured(app_settings.SONARR_PATH_PREFIX),
            "sonarr_media_path_configured": _configured(app_settings.SONARR_MEDIA_PATH),
            "metrics_disk_path_configured": _configured(app_settings.METRICS_DISK_PATH),
            "poster_cache_dir": app_settings.POSTER_CACHE_DIR,
            "poster_staging_dir": app_settings.POSTER_STAGING_DIR,
        },
        "sync": {
            "interval_minutes": app_settings.SYNC_INTERVAL_MINUTES,
            "cooldown_seconds": app_settings.SYNC_COOLDOWN_SECONDS,
            "heal_enabled": app_settings.HEAL_ENABLED,
            "heal_interval_minutes": app_settings.HEAL_INTERVAL_MINUTES,
            "webhook_dry_run": app_settings.WEBHOOK_DRY_RUN,
        },
        "posters": {
            "restore_method": app_settings.POSTER_RESTORE_METHOD,
            "backup_dir": app_settings.POSTER_BACKUP_DIR,
        },
        "poster_formats": {
            "movie": app_settings.MOVIE_POSTER_FORMAT,
            "series": app_settings.SERIES_POSTER_FORMAT,
            "season": app_settings.SEASON_POSTER_FORMAT,
        },
        "writable": provider_health["status"] == "valid",
    }


class PostersSettingsUpdate(BaseModel):
    movie_poster_format: str | None = None
    series_poster_format: str | None = None
    season_poster_format: str | None = None
    restore_method: Literal["download", "local"] | None = None


class HealSettingsUpdate(BaseModel):
    enabled: bool | None = None
    interval_minutes: int | None = Field(default=None, ge=5, le=10080)


class SettingsUpdatePayload(BaseModel):
    expected_version: int
    posters: PostersSettingsUpdate | None = None
    heal: HealSettingsUpdate | None = None


class ConfigurationUpdatePayload(BaseModel):
    expected_version: int
    values: dict[str, Any]


class IntegrationUpdatePayload(BaseModel):
    expected_version: int
    expected_secret_generation: int = Field(ge=0)
    url: str | None = None
    credential: str | None = Field(default=None, max_length=8192)


class IntegrationTestPayload(BaseModel):
    url: str | None = None
    credential: str | None = Field(default=None, max_length=8192)


class CredentialClearPayload(BaseModel):
    expected_generation: int = Field(ge=0)


class PathMappingTestPayload(BaseModel):
    media_roots: list[str] = Field(default_factory=list, max_length=32)
    radarr_path_prefix: str | None = Field(default=None, max_length=4096)
    radarr_media_path: str | None = Field(default=None, max_length=4096)
    sonarr_path_prefix: str | None = Field(default=None, max_length=4096)
    sonarr_media_path: str | None = Field(default=None, max_length=4096)


def _validate_movie_poster_format(value: str) -> str:
    try:
        return normalize_poster_template("movie", value)
    except PathValidationError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid movie poster format: {exc}") from exc


def _validate_series_poster_format(value: str) -> str:
    try:
        return normalize_poster_template("series", value)
    except PathValidationError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid series poster format: {exc}") from exc


def _validate_season_poster_format(value: str) -> str:
    try:
        return normalize_poster_template("season", value)
    except PathValidationError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid season poster format: {exc}") from exc


def _validate_special_values(values: dict[str, Any]) -> dict[str, Any]:
    updates = dict(values)
    if "MOVIE_POSTER_FORMAT" in updates:
        updates["MOVIE_POSTER_FORMAT"] = _validate_movie_poster_format(
            updates["MOVIE_POSTER_FORMAT"]
        )
    if "SERIES_POSTER_FORMAT" in updates:
        updates["SERIES_POSTER_FORMAT"] = _validate_series_poster_format(
            updates["SERIES_POSTER_FORMAT"]
        )
    if "SEASON_POSTER_FORMAT" in updates:
        updates["SEASON_POSTER_FORMAT"] = _validate_season_poster_format(
            updates["SEASON_POSTER_FORMAT"]
        )
    return updates


def _normalize_container_path(value: str, *, label: str) -> str:
    normalized = value.strip()
    if not normalized or "\0" in normalized:
        raise HTTPException(status_code=400, detail=f"{label} is invalid.")
    path = Path(normalized)
    if not path.is_absolute():
        raise HTTPException(status_code=400, detail=f"{label} must be an absolute path.")
    return str(path)


def _path_accessibility(path_value: str) -> dict[str, Any]:
    path = Path(path_value)
    exists = path.exists()
    return {
        "path": str(path),
        "exists": exists,
        "directory": exists and path.is_dir(),
        "readable": exists and os.access(path, os.R_OK),
        "writable": exists and os.access(path, os.W_OK),
    }


@router.post("/paths/test")
async def test_path_mappings(payload: PathMappingTestPayload):
    """Validate candidate logical paths without creating files or changing mounts."""
    pairs = {
        "radarr": (payload.radarr_path_prefix, payload.radarr_media_path),
        "sonarr": (payload.sonarr_path_prefix, payload.sonarr_media_path),
    }
    mappings: dict[str, dict[str, Any]] = {}
    for provider, (prefix, target) in pairs.items():
        if bool(prefix) != bool(target):
            raise HTTPException(
                status_code=400,
                detail=f"{provider.title()} path prefix and media path must be configured together.",
            )
        if prefix and target:
            normalized_prefix = _normalize_container_path(
                prefix, label=f"{provider.title()} path prefix"
            )
            normalized_target = _normalize_container_path(
                target, label=f"{provider.title()} media path"
            )
            try:
                normalized_target = str(
                    validate_media_path_candidate(
                        normalized_target,
                        label=f"{provider.title()} media path",
                    )
                )
            except PathValidationError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            mappings[provider] = {
                "configured": True,
                "prefix": normalized_prefix,
                "target": _path_accessibility(normalized_target),
            }
        else:
            mappings[provider] = {"configured": False, "prefix": None, "target": None}

    roots: list[dict[str, Any]] = []
    seen: set[str] = set()
    for root in payload.media_roots:
        normalized = _normalize_container_path(root, label="Media root")
        try:
            normalized = str(validate_media_path_candidate(normalized, label="Media root"))
        except PathValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if normalized in seen:
            continue
        seen.add(normalized)
        roots.append(_path_accessibility(normalized))
    return {"ok": True, "mappings": mappings, "media_roots": roots, "mutated": False}


def _configuration_exception(exc: Exception) -> HTTPException:
    if isinstance(exc, ConfigurationVersionConflictError):
        return HTTPException(
            status_code=409,
            detail={
                "code": "configuration_version_conflict",
                "current_version": exc.current.version,
                "etag": exc.current.etag,
            },
        )
    return HTTPException(status_code=400, detail=str(exc))


async def _update_public_configuration(
    db: AsyncSession,
    *,
    expected_version: int,
    values: dict[str, Any],
    trigger: str,
) -> tuple[Any, bool]:
    integration_url_keys = sorted(_INTEGRATION_URL_KEYS.intersection(values))
    if integration_url_keys:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "integration_url_requires_provider_endpoint",
                "fields": integration_url_keys,
            },
        )
    try:
        return await update_configuration(
            db,
            expected_version=expected_version,
            updates=_validate_special_values(values),
            actor={"kind": "api", "id": "settings"},
            trigger=trigger,
        )
    except (ConfigurationError, ConfigurationVersionConflictError) as exc:
        await db.rollback()
        raise _configuration_exception(exc) from exc


@router.put("/config")
async def put_configuration(
    payload: ConfigurationUpdatePayload,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    state, changed = await _update_public_configuration(
        db,
        expected_version=payload.expected_version,
        values=payload.values,
        trigger="unified_settings_api",
    )
    await db.commit()
    await configuration_provider.refresh_from_session(db)
    return {
        "configuration_version": state.version,
        "etag": state.etag,
        "changed": changed,
        "applied": sorted(payload.values) if changed else [],
        "settings": await get_settings(db),
    }


@router.put("")
async def put_settings(
    payload: SettingsUpdatePayload,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Compatibility adapter for the original poster/heal settings endpoint."""
    updates: dict[str, Any] = {}
    if payload.posters:
        poster_update = payload.posters.model_dump(exclude_unset=True)
        mapping = {
            "movie_poster_format": "MOVIE_POSTER_FORMAT",
            "series_poster_format": "SERIES_POSTER_FORMAT",
            "season_poster_format": "SEASON_POSTER_FORMAT",
            "restore_method": "POSTER_RESTORE_METHOD",
        }
        updates.update({mapping[key]: value for key, value in poster_update.items()})
    if payload.heal:
        heal_update = payload.heal.model_dump(exclude_unset=True)
        if "enabled" in heal_update:
            updates["HEAL_ENABLED"] = heal_update["enabled"]
        if "interval_minutes" in heal_update:
            updates["HEAL_INTERVAL_MINUTES"] = heal_update["interval_minutes"]
    state, changed = await _update_public_configuration(
        db,
        expected_version=payload.expected_version,
        values=updates,
        trigger="settings_api",
    )
    await db.commit()
    await configuration_provider.refresh_from_session(db)
    return {
        "configuration_version": state.version,
        "etag": state.etag,
        "changed": changed,
        "applied": sorted(updates) if changed else [],
        "settings": await get_settings(db),
    }


def _is_loopback(host: str | None) -> bool:
    if not host:
        return False
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host.lower() == "localhost"


def _is_private_or_loopback(host: str | None) -> bool:
    if not host:
        return False
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return host.lower() == "localhost"
    return address.is_private or address.is_loopback


def _require_secure_secret_transport(request: Request) -> None:
    client_host = request.client.host if request.client else None
    forwarded_https = (
        _is_private_or_loopback(client_host)
        and request.headers.get("x-marquee-internal-proxy") == "same-origin"
        and request.headers.get("x-forwarded-proto", "").split(",", 1)[0].strip() == "https"
    )
    if request.url.scheme != "https" and not forwarded_https and not _is_loopback(client_host):
        raise HTTPException(
            status_code=400,
            detail="Credential operations require HTTPS or a loopback connection.",
        )


def _normalize_service_url(value: str) -> str:
    try:
        return normalize_integration_url(value)
    except IntegrationURLValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


async def _test_integration(provider: str, *, url: str | None, credential: str | None) -> None:
    if not credential:
        raise HTTPException(status_code=400, detail=f"{provider.title()} credential is required.")
    try:
        if provider == "tmdb":
            async with httpx.AsyncClient(
                base_url="https://api.themoviedb.org/3",
                headers={"Authorization": f"Bearer {credential}", "Accept": "application/json"},
                timeout=httpx.Timeout(10.0),
                follow_redirects=False,
            ) as client:
                response = await client.get("/configuration")
                response.raise_for_status()
            return
        if not url:
            raise HTTPException(status_code=400, detail=f"{provider.title()} URL is required.")
        if provider == "radarr":
            from marquee.core.arr_clients.radarr_client import RadarrClient

            client = RadarrClient(url, credential)
        else:
            from marquee.core.arr_clients.sonarr_client import SonarrClient

            client = SonarrClient(url, credential)
        await client.connect()
        try:
            await client.get_system_status()
        finally:
            await client.disconnect()
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Could not authenticate with {provider.title()}. Check its URL and credential.",
        ) from exc


async def _replace_runtime_integration(
    request: Request,
    provider: str,
    *,
    url: str | None,
    credential: str | None,
) -> None:
    """Swap the API process client after a tested, committed integration change."""
    state = request.app.state
    attribute = f"{provider}_client"
    previous = getattr(state, attribute, None)
    replacement = None
    if credential:
        if provider == "tmdb":
            from marquee.core.poster_sources.tmdb import TMDBClient  # noqa: PLC0415

            replacement = TMDBClient(read_access_token=credential)
        elif provider == "radarr" and url:
            from marquee.core.arr_clients.radarr_client import RadarrClient  # noqa: PLC0415

            replacement = RadarrClient(url, credential)
        elif provider == "sonarr" and url:
            from marquee.core.arr_clients.sonarr_client import SonarrClient  # noqa: PLC0415

            replacement = SonarrClient(url, credential)
        if replacement is not None:
            await replacement.connect()
    setattr(state, attribute, replacement)
    if previous is not None:
        await previous.disconnect()


async def _integration_inputs(
    db: AsyncSession,
    *,
    provider: str,
    url: str | None,
    credential: str | None,
    expected_version: int | None = None,
    expected_secret_generation: int | None = None,
) -> tuple[str | None, str | None, IntegrationCapability]:
    if provider not in _PROVIDER_SECRET_KEYS:
        raise HTTPException(status_code=404, detail="Unknown integration provider.")
    if provider == "tmdb" and url is not None:
        raise HTTPException(status_code=400, detail="TMDB uses Marquee's fixed API endpoint.")

    try:
        snapshot = await read_integration_settings_snapshot(db)
    except (ConfigurationError, ManagedSecretError) as exc:
        raise HTTPException(
            status_code=503,
            detail="Integration settings are temporarily unavailable.",
        ) from exc
    capability = snapshot.for_provider(provider)
    if expected_version is not None and expected_version != snapshot.configuration_version:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "configuration_version_conflict",
                "current_version": snapshot.configuration_version,
            },
        )
    if (
        expected_secret_generation is not None
        and expected_secret_generation != capability.secret_generation
    ):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "credential_generation_conflict",
                "current_generation": capability.secret_generation,
            },
        )

    effective_url = url
    if provider in _PROVIDER_URL_KEYS:
        configured_url = capability.url
        effective_url = _normalize_service_url(url or configured_url or "")
        if (
            url is not None
            and credential is None
            and capability.credential is not None
            and (
                configured_url is None or not same_integration_origin(effective_url, configured_url)
            )
        ):
            raise HTTPException(
                status_code=400,
                detail=(
                    "Changing an integration origin requires an explicit replacement credential."
                ),
            )
    effective_credential = credential if credential is not None else capability.credential
    if not effective_credential:
        raise HTTPException(
            status_code=400,
            detail="A configured or candidate credential is required.",
        )
    return effective_url, effective_credential, capability


@router.post("/integrations/{provider}/test")
async def test_integration(
    provider: str,
    payload: IntegrationTestPayload,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    _require_secure_secret_transport(request)
    url, credential, _ = await _integration_inputs(
        db,
        provider=provider,
        url=payload.url,
        credential=payload.credential,
    )
    await _test_integration(provider, url=url, credential=credential)
    return {"ok": True, "provider": provider}


@router.put("/integrations/{provider}")
async def put_integration(
    provider: str,
    payload: IntegrationUpdatePayload,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    _require_secure_secret_transport(request)
    url, credential, capability = await _integration_inputs(
        db,
        provider=provider,
        url=payload.url,
        credential=payload.credential,
        expected_version=payload.expected_version,
        expected_secret_generation=payload.expected_secret_generation,
    )
    await _test_integration(provider, url=url, credential=credential)
    updates = (
        {_PROVIDER_URL_KEYS[provider]: url}
        if provider in _PROVIDER_URL_KEYS and payload.url is not None
        else {}
    )
    try:
        state, changed = await update_configuration(
            db,
            expected_version=payload.expected_version,
            updates=updates,
            actor={"kind": "api", "id": "settings"},
            trigger=f"{provider}_integration_settings",
        )
        if payload.credential is not None:
            await replace_managed_secret(
                db,
                name=_PROVIDER_SECRET_KEYS[provider],
                value=payload.credential,
                expected_generation=payload.expected_secret_generation,
                actor={"kind": "api", "id": "settings"},
            )
        else:
            await require_managed_secret_generation(
                db,
                name=_PROVIDER_SECRET_KEYS[provider],
                expected_generation=capability.secret_generation,
                for_update=True,
            )
    except ConfigurationVersionConflictError as exc:
        await db.rollback()
        raise _configuration_exception(exc) from exc
    except ManagedSecretGenerationConflictError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail={
                "code": "credential_generation_conflict",
                "current_generation": exc.current_generation,
            },
        ) from exc
    except (ConfigurationError, ManagedSecretError) as exc:
        await db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await db.commit()
    await configuration_provider.refresh_from_session(db)
    await managed_secret_provider.refresh(db)
    committed = (await read_integration_settings_snapshot(db)).for_provider(provider)
    await _replace_runtime_integration(
        request,
        provider,
        url=committed.url,
        credential=committed.credential,
    )
    return {
        "configuration_version": state.version,
        "etag": state.etag,
        "changed": changed or payload.credential is not None,
        "settings": await get_settings(db),
    }


@router.delete("/integrations/{provider}/credential")
async def delete_integration_credential(
    provider: str,
    payload: CredentialClearPayload,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    _require_secure_secret_transport(request)
    if provider not in _PROVIDER_SECRET_KEYS:
        raise HTTPException(status_code=404, detail="Unknown integration provider.")
    try:
        await clear_managed_secret(
            db,
            name=_PROVIDER_SECRET_KEYS[provider],
            expected_generation=payload.expected_generation,
            actor={"kind": "api", "id": "settings"},
        )
    except ManagedSecretGenerationConflictError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail={
                "code": "credential_generation_conflict",
                "current_generation": exc.current_generation,
            },
        ) from exc
    except (ManagedSecretError, ManagedSecretUnavailableError) as exc:
        await db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await db.commit()
    await managed_secret_provider.refresh(db)
    await _replace_runtime_integration(request, provider, url=None, credential=None)
    return {"cleared": True, "settings": await get_settings(db)}


async def _upsert_poster_heal_schedule(
    db: AsyncSession,
    *,
    enabled: bool,
    interval_minutes: int,
) -> None:
    """Compatibility no-op: scheduling reads the versioned configuration."""
    return None
