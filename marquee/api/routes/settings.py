"""Redacted operational settings summary for the frontend settings page."""

from __future__ import annotations

from fastapi import APIRouter

from marquee.config import settings as app_settings
from marquee.core.subtitles.config import subtitle_settings

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
                "callback_token_configured": _configured(
                    subtitle_settings.SUBGEN_CALLBACK_TOKEN
                ),
                "profile_name": subtitle_settings.SUBGEN_PROFILE_NAME,
                "model_label": subtitle_settings.SUBGEN_MODEL_LABEL,
                "mode": subtitle_settings.SUBGEN_MODE,
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
        "writable": False,
    }
