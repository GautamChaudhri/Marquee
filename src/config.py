"""Centralised application configuration.

All settings are loaded from environment variables and/or a ``.env`` file.
Pydantic-settings handles type coercion, validation, and defaults.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# ---------------------------------------------------------------------------
# Locate the .env file next to pyproject.toml (project root)
# ---------------------------------------------------------------------------
ENV_FILE = Path(__file__).parent.parent / ".env"


class Settings(BaseSettings):
    """Application-wide settings."""

    # ------------------------------------------------------------------
    # Application
    # ------------------------------------------------------------------
    APP_NAME: str = "Marquee"
    HOST: str = "0.0.0.0"
    PORT: int = 3165
    DEBUG: bool = False
    LOG_LEVEL: str = "INFO"

    # ------------------------------------------------------------------
    # Database
    # ------------------------------------------------------------------
    DB_URL: str = "sqlite+aiosqlite:///./data/marquee.db"

    # ------------------------------------------------------------------
    # TMDB
    # ------------------------------------------------------------------
    TMDB_READ_ACCESS_TOKEN: Optional[str] = Field(
        default=None,
        description="TMDB API v3 Read Access Token (Bearer token)",
    )

    @property
    def tmdb_configured(self) -> bool:
        return self.TMDB_READ_ACCESS_TOKEN is not None

    # ------------------------------------------------------------------
    # Fanart.tv
    # ------------------------------------------------------------------
    FANART_API_KEY: Optional[str] = Field(
        default=None,
        description="Fanart.tv personal API key",
    )

    @property
    def fanart_configured(self) -> bool:
        return self.FANART_API_KEY is not None

    # ------------------------------------------------------------------
    # TheTVDB
    # ------------------------------------------------------------------
    TVDB_API_KEY: Optional[str] = Field(
        default=None,
        description="TheTVDB API key (v4)",
    )

    @property
    def tvdb_configured(self) -> bool:
        return self.TVDB_API_KEY is not None

    # ------------------------------------------------------------------
    # Radarr
    # ------------------------------------------------------------------
    RADARR_URL: Optional[str] = Field(
        default=None,
        description="Radarr base URL (e.g. http://localhost:7878)",
    )
    RADARR_API_KEY: Optional[str] = Field(
        default=None,
        description="Radarr API key",
    )

    @property
    def radarr_configured(self) -> bool:
        return self.RADARR_URL is not None and self.RADARR_API_KEY is not None

    # ------------------------------------------------------------------
    # Sonarr
    # ------------------------------------------------------------------
    SONARR_URL: Optional[str] = Field(
        default=None,
        description="Sonarr base URL (e.g. http://localhost:8989)",
    )
    SONARR_API_KEY: Optional[str] = Field(
        default=None,
        description="Sonarr API key",
    )

    @property
    def sonarr_configured(self) -> bool:
        return self.SONARR_URL is not None and self.SONARR_API_KEY is not None

    # ------------------------------------------------------------------
    # Path Mapping (cross-host setups)
    # ------------------------------------------------------------------
    PATH_MAPPING_FROM: Optional[str] = Field(
        default=None,
        description="Local mount prefix (e.g. /Volumes/Media)",
    )
    PATH_MAPPING_TO: Optional[str] = Field(
        default=None,
        description="*arr-reported prefix (e.g. /media)",
    )

    @property
    def path_mapping_configured(self) -> bool:
        return self.PATH_MAPPING_FROM is not None and self.PATH_MAPPING_TO is not None

    def translate_path(self, path: str) -> str:
        """Translate a path from the *arr's view to the local filesystem."""
        if self.path_mapping_configured and path and path.startswith(self.PATH_MAPPING_TO):
            return self.PATH_MAPPING_FROM + path[len(self.PATH_MAPPING_TO):]
        return path

    # ------------------------------------------------------------------
    # Sync
    # ------------------------------------------------------------------
    SYNC_INTERVAL_MINUTES: int = Field(
        default=15,
        description="Minutes between periodic *arr syncs",
    )
    HEAL_INTERVAL_MINUTES: int = Field(
        default=30,
        description="Minutes between self-healing poster existence scans",
    )

    # ------------------------------------------------------------------
    # Poster Cache
    # ------------------------------------------------------------------
    POSTER_CACHE_DIR: str = Field(
        default="data/cache/posters",
        description="Directory for cached posters, keyed by TMDB ID",
    )
    POSTER_STAGING_DIR: str = Field(
        default="data/staging",
        description="Temporary directory for downloaded poster candidates",
    )

    # ------------------------------------------------------------------
    # AI / ML
    # ------------------------------------------------------------------
    AI_MODEL: str = Field(
        default="clip-vit-b-32",
        description="CLIP variant: clip-vit-b-32 | clip-vit-b-16 | clip-vit-l-14",
    )
    VLM_ENABLED: bool = Field(
        default=False,
        description="Enable optional Vision Language Model for descriptive analysis",
    )
    VLM_MODEL: str = Field(
        default="qwen2.5-vl-3b",
        description="VLM model when VLM_ENABLED is true",
    )

    # ------------------------------------------------------------------
    # OCR Filtering
    # ------------------------------------------------------------------
    OCR_WORKERS: int = Field(
        default=4,
        description="Number of parallel PaddleOCR worker processes",
    )
    OCR_CONFIDENCE_THRESHOLD: float = Field(
        default=0.75,
        description="Minimum OCR confidence score for full-image text",
    )

    # ------------------------------------------------------------------
    # Pydantic metadata
    # ------------------------------------------------------------------
    model_config = SettingsConfigDict(
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        env_file_override=False,
        extra="ignore",
    )


# Singleton — import from here everywhere
settings = Settings()
