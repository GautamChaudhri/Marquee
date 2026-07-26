"""Redacted operational settings summary for the frontend settings page."""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from marquee.config import Settings
from marquee.core.configuration import (
    CONFIGURATION_CATALOG,
    ConfigurationError,
    ConfigurationVersionConflictError,
    update_configuration,
)
from marquee.core.configuration_cache import configuration_provider
from marquee.core.path_utils import PathValidationError
from marquee.core.poster_files import sanitize_poster_filename
from marquee.database import get_db

router = APIRouter(prefix="/api/settings", tags=["settings"])


def _configured(value: object) -> bool:
    return value is not None and value != ""


@router.get("")
async def get_settings(db: Annotated[AsyncSession, Depends(get_db)]):
    """Return effective redacted settings from the current immutable revision."""
    state = configuration_provider.state
    provider_health = configuration_provider.health()
    app_settings = Settings(**configuration_provider.effective("app"))
    metadata = {
        key: {
            "owner": "database" if entry.database_owned else "environment",
            "apply_mode": entry.apply_mode,
            "sensitivity": entry.sensitivity,
            "scope": entry.scope,
        }
        for key, entry in CONFIGURATION_CATALOG.items()
        if entry.owner == "app"
    }
    return {
        "configuration_version": state.version,
        "etag": state.etag,
        "stale": provider_health["status"] != "valid",
        "health": provider_health,
        "configuration_meta": metadata,
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
            "tmdb": {"configured": app_settings.tmdb_configured},
            "fanart": {"configured": app_settings.fanart_configured},
            "tvdb": {"configured": app_settings.tvdb_configured},
            "radarr": {
                "configured": app_settings.radarr_configured,
                "url_configured": _configured(app_settings.RADARR_URL),
                "api_key_configured": _configured(app_settings.RADARR_API_KEY),
                "path_mapping_configured": app_settings.radarr_path_configured,
            },
            "sonarr": {
                "configured": app_settings.sonarr_configured,
                "url_configured": _configured(app_settings.SONARR_URL),
                "api_key_configured": _configured(app_settings.SONARR_API_KEY),
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
        "writable": True,
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


def _validate_movie_poster_format(value: str) -> str:
    try:
        rendered = value.format(movie_basename="Example Movie")
        sanitize_poster_filename(rendered)
    except (IndexError, KeyError, PathValidationError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=f"Invalid movie poster format: {exc}") from exc
    return value


def _validate_series_poster_format(value: str) -> str:
    if "{" in value or "}" in value:
        raise HTTPException(
            status_code=400,
            detail="TV series poster format must not contain template placeholders.",
        )
    try:
        sanitize_poster_filename(value)
    except (PathValidationError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=f"Invalid series poster format: {exc}") from exc
    return value


def _validate_season_poster_format(value: str) -> str:
    if "{season" not in value:
        raise HTTPException(
            status_code=400, detail="Season poster format must contain a '{season' placeholder."
        )
    try:
        r1 = value.format(season=1)
        r0 = value.format(season=0)
        sanitize_poster_filename(r1)
        sanitize_poster_filename(r0)
    except (IndexError, KeyError, PathValidationError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=f"Invalid season poster format: {exc}") from exc
    return value


async def _upsert_poster_heal_schedule(
    db: AsyncSession,
    *,
    enabled: bool,
    interval_minutes: int,
) -> None:
    """Scheduling is PgQueuer-owned; A3 persists these values as configuration."""
    return None


@router.put("")
async def put_settings(
    payload: SettingsUpdatePayload,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Append one validated configuration revision using optimistic concurrency."""
    updates: dict[str, object] = {}
    if payload.posters:
        poster_update = payload.posters.model_dump(exclude_unset=True)
        if "movie_poster_format" in poster_update:
            updates["MOVIE_POSTER_FORMAT"] = _validate_movie_poster_format(
                poster_update["movie_poster_format"]
            )
        if "series_poster_format" in poster_update:
            updates["SERIES_POSTER_FORMAT"] = _validate_series_poster_format(
                poster_update["series_poster_format"]
            )
        if "season_poster_format" in poster_update:
            updates["SEASON_POSTER_FORMAT"] = _validate_season_poster_format(
                poster_update["season_poster_format"]
            )
        if "restore_method" in poster_update:
            updates["POSTER_RESTORE_METHOD"] = poster_update["restore_method"]
    if payload.heal:
        heal_update = payload.heal.model_dump(exclude_unset=True)
        if "enabled" in heal_update:
            updates["HEAL_ENABLED"] = heal_update["enabled"]
        if "interval_minutes" in heal_update:
            updates["HEAL_INTERVAL_MINUTES"] = heal_update["interval_minutes"]

    try:
        state, changed = await update_configuration(
            db,
            expected_version=payload.expected_version,
            updates=updates,
            actor={"kind": "api", "id": "settings"},
            trigger="settings_api",
        )
    except ConfigurationVersionConflictError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail={
                "code": "configuration_version_conflict",
                "current_version": exc.current.version,
                "etag": exc.current.etag,
            },
        ) from exc
    except ConfigurationError as exc:
        await db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await db.commit()
    await configuration_provider.refresh_from_session(db)
    return {
        "configuration_version": state.version,
        "etag": state.etag,
        "changed": changed,
        "applied": sorted(updates) if changed else [],
        "settings": await get_settings(db),
    }
