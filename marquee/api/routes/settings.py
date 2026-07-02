"""Redacted operational settings summary for the frontend settings page."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from marquee.config import Settings
from marquee.config import load_overrides as load_settings_overrides
from marquee.config import save_overrides as save_settings_overrides
from marquee.config import settings as app_settings
from marquee.core.path_utils import PathValidationError
from marquee.core.poster_service import sanitize_poster_filename
from marquee.core.subtitles.config import subtitle_settings
from marquee.database import get_db
from marquee.models import JobSchedule

router = APIRouter(prefix="/api/settings", tags=["settings"])


def _configured(value: object) -> bool:
    return value is not None and value != ""


@router.get("")
async def get_settings():
    """Return non-secret runtime settings and integration configured flags."""
    return {
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
            "subgen": {
                "configured": subtitle_settings.generation_enabled,
                "url_configured": _configured(subtitle_settings.SUBGEN_URL),
                "callback_token_configured": _configured(subtitle_settings.SUBGEN_CALLBACK_TOKEN),
                "url": subtitle_settings.SUBGEN_URL,
                "profile_name": subtitle_settings.SUBGEN_PROFILE_NAME,
                "model_label": subtitle_settings.SUBGEN_MODEL_LABEL,
                "mode": subtitle_settings.SUBGEN_MODE,
                "local_path_prefix": subtitle_settings.SUBGEN_LOCAL_PATH_PREFIX,
                "remote_path_prefix": subtitle_settings.SUBGEN_REMOTE_PATH_PREFIX,
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
        "letterbox": {
            "enabled": app_settings.LETTERBOX_ENABLED,
            "method": app_settings.LETTERBOX_DETECT_METHOD,
            "auto_apply_high": app_settings.LETTERBOX_AUTO_APPLY_HIGH,
            "asymmetric": app_settings.LETTERBOX_ASYMMETRIC,
            "heal_enabled": app_settings.LETTERBOX_HEAL_ENABLED,
            "heal_interval_minutes": app_settings.LETTERBOX_HEAL_INTERVAL_MINUTES,
            "max_parallel": app_settings.LETTERBOX_MAX_PARALLEL,
        },
        "subtitles": {
            "enabled": subtitle_settings.SUBTITLE_ENABLED,
            "scan_concurrency": subtitle_settings.SUBTITLE_SCAN_CONCURRENCY,
            "mutation_concurrency": subtitle_settings.SUBTITLE_MUTATION_CONCURRENCY,
            "generation_concurrency": subtitle_settings.SUBTITLE_GENERATION_CONCURRENCY,
            "preferred_languages": subtitle_settings.SUBTITLE_PREFERRED_LANGUAGES,
            "preferred_audio_languages": subtitle_settings.SUBTITLE_PREFERRED_AUDIO_LANGUAGES,
            "preferred_subtitle_languages": subtitle_settings.SUBTITLE_PREFERRED_SUBTITLE_LANGUAGES,
            "effective_preferred_audio_languages": (
                subtitle_settings.effective_preferred_audio_languages
            ),
            "effective_preferred_subtitle_languages": (
                subtitle_settings.effective_preferred_subtitle_languages
            ),
            "unknown_language_action": subtitle_settings.SUBTITLE_UNKNOWN_LANGUAGE_ACTION,
            "protect_forced": subtitle_settings.SUBTITLE_PROTECT_FORCED,
            "protect_last_full_dialogue": subtitle_settings.SUBTITLE_PROTECT_LAST_FULL_DIALOGUE,
            "backup_mode": subtitle_settings.SUBTITLE_BACKUP_MODE,
            "external_delete_mode": subtitle_settings.SUBTITLE_EXTERNAL_DELETE_MODE,
        },
        "poster_formats": {
            "movie": app_settings.MOVIE_POSTER_FORMAT,
            "series": app_settings.SERIES_POSTER_FORMAT,
            "season": app_settings.SEASON_POSTER_FORMAT,
        },
        "writable": True,
    }


class SubtitlesSettingsUpdate(BaseModel):
    enabled: bool | None = None
    scan_concurrency: int | None = None
    mutation_concurrency: int | None = None
    generation_concurrency: int | None = None
    preferred_languages: list[str] | None = None
    preferred_audio_languages: list[str] | None = None
    preferred_subtitle_languages: list[str] | None = None
    unknown_language_action: str | None = None
    protect_forced: bool | None = None
    protect_last_full_dialogue: bool | None = None
    backup_mode: str | None = None
    external_delete_mode: str | None = None


class SubgenSettingsUpdate(BaseModel):
    url: str | None = None
    profile_name: str | None = None
    model_label: str | None = None
    mode: str | None = None
    local_path_prefix: str | None = None
    remote_path_prefix: str | None = None
    callback_token: str | None = None


class PostersSettingsUpdate(BaseModel):
    movie_poster_format: str | None = None
    restore_method: Literal["download", "local"] | None = None


class HealSettingsUpdate(BaseModel):
    enabled: bool | None = None
    interval_minutes: int | None = Field(default=None, ge=5, le=10080)


class SettingsUpdatePayload(BaseModel):
    subtitles: SubtitlesSettingsUpdate | None = None
    subgen: SubgenSettingsUpdate | None = None
    posters: PostersSettingsUpdate | None = None
    heal: HealSettingsUpdate | None = None


def _validate_movie_poster_format(value: str) -> str:
    try:
        rendered = value.format(movie_basename="Example Movie")
        sanitize_poster_filename(rendered)
    except (IndexError, KeyError, PathValidationError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=f"Invalid movie poster format: {exc}") from exc
    return value


async def _upsert_poster_heal_schedule(
    db: AsyncSession,
    *,
    enabled: bool,
    interval_minutes: int,
) -> None:
    interval_seconds = max(60, interval_minutes * 60)
    next_run_at = datetime.now(UTC) + timedelta(seconds=interval_seconds)
    row = await db.get(JobSchedule, "poster-heal")
    if row is None:
        db.add(
            JobSchedule(
                id="poster-heal",
                job_type="poster_heal",
                interval_seconds=interval_seconds,
                enabled=enabled,
                next_run_at=next_run_at,
            )
        )
    else:
        changed = row.interval_seconds != interval_seconds or row.enabled != enabled
        row.job_type = "poster_heal"
        row.interval_seconds = interval_seconds
        row.enabled = enabled
        if changed:
            row.next_run_at = next_run_at
    await db.commit()


@router.put("")
async def put_settings(
    payload: SettingsUpdatePayload,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Update mutable UI settings, validate, mutate singleton, and save overrides."""
    current = subtitle_settings.model_dump()

    if payload.subtitles:
        sub_update = payload.subtitles.model_dump(exclude_unset=True)
        for key, val in sub_update.items():
            current[f"SUBTITLE_{key.upper()}"] = val

    if payload.subgen:
        subgen_update = payload.subgen.model_dump(exclude_unset=True)
        for key, val in subgen_update.items():
            current[f"SUBGEN_{key.upper()}"] = val

    from marquee.core.subtitles.config import SubtitleSettings

    try:
        SubtitleSettings(**current)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    app_updates: dict[str, object] = {}
    if payload.posters:
        poster_update = payload.posters.model_dump(exclude_unset=True)
        if "movie_poster_format" in poster_update:
            app_updates["MOVIE_POSTER_FORMAT"] = _validate_movie_poster_format(
                poster_update["movie_poster_format"]
            )
        if "restore_method" in poster_update:
            app_updates["POSTER_RESTORE_METHOD"] = poster_update["restore_method"]

    if payload.heal:
        heal_update = payload.heal.model_dump(exclude_unset=True)
        if "enabled" in heal_update:
            app_updates["HEAL_ENABLED"] = heal_update["enabled"]
        if "interval_minutes" in heal_update:
            app_updates["HEAL_INTERVAL_MINUTES"] = heal_update["interval_minutes"]

    if app_updates:
        app_current = app_settings.model_dump()
        app_current.update(app_updates)
        try:
            Settings(**app_current)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    from marquee.core.subtitles.config import load_overrides, save_overrides

    updated_fields = {}
    if payload.subtitles:
        for key, val in payload.subtitles.model_dump(exclude_unset=True).items():
            setting_key = f"SUBTITLE_{key.upper()}"
            setattr(subtitle_settings, setting_key, val)
            updated_fields[setting_key] = val

    if payload.subgen:
        for key, val in payload.subgen.model_dump(exclude_unset=True).items():
            setting_key = f"SUBGEN_{key.upper()}"
            setattr(subtitle_settings, setting_key, val)
            if setting_key == "SUBGEN_CALLBACK_TOKEN":
                updated_fields[setting_key] = "<redacted>"
            else:
                updated_fields[setting_key] = val

    current_overrides = load_overrides()
    for setting_key, val in updated_fields.items():
        if setting_key == "SUBGEN_CALLBACK_TOKEN":
            val = getattr(subtitle_settings, setting_key)
        current_overrides[setting_key] = val

    save_overrides(current_overrides)
    if app_updates:
        current_app_overrides = load_settings_overrides()
        for setting_key, val in app_updates.items():
            setattr(app_settings, setting_key, val)
            current_app_overrides[setting_key] = val
            updated_fields[setting_key] = val
        save_settings_overrides(current_app_overrides)

    if any(key in app_updates for key in ("HEAL_ENABLED", "HEAL_INTERVAL_MINUTES")):
        await _upsert_poster_heal_schedule(
            db,
            enabled=app_settings.HEAL_ENABLED,
            interval_minutes=app_settings.HEAL_INTERVAL_MINUTES,
        )

    return {"applied": sorted(updated_fields.keys()), "settings": await get_settings()}
