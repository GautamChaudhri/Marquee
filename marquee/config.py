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
    DATA_DIR: str = "data"

    @property
    def _project_root(self) -> Path:
        """Absolute path to the project root (parent of marquee/ package)."""
        return Path(__file__).parent.parent

    @property
    def db_url_resolved(self) -> str:
        """Database URL with path resolved relative to project root.

        Unlike ``DB_URL`` (which is relative to CWD), this always points
        to ``<project>/data/marquee.db`` regardless of where uvicorn is
        launched from.
        """
        db_path = self._project_root / self.DATA_DIR / "marquee.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        return f"sqlite+aiosqlite:///{db_path}"

    @property
    def poster_cache_path(self) -> Path:
        """Absolute path to the poster cache directory."""
        return self._project_root / self.DATA_DIR / "cache" / "posters"

    @property
    def poster_staging_path(self) -> Path:
        """Absolute path to the staging directory for downloaded candidates."""
        return self._project_root / self.DATA_DIR / "staging"

    # ------------------------------------------------------------------
    # CORS (for web UI development in Phase 6)
    # ------------------------------------------------------------------
    CORS_ORIGINS: list[str] = Field(
        default=["http://localhost:5173", "http://localhost:3000"],
        description="Allowed origins for CORS. Add your web UI dev server here.",
    )

    # ------------------------------------------------------------------
    # Shutdown
    # ------------------------------------------------------------------
    SHUTDOWN_TIMEOUT_SECONDS: int = Field(
        default=10,
        description="Max seconds to wait for clients to disconnect during shutdown.",
    )

    # ------------------------------------------------------------------
    # Path Validation
    # ------------------------------------------------------------------
    MEDIA_ROOTS: list[str] = Field(
        default=["/media", "/tvshows"],
        description="Allowed root directories for media filesystem operations.",
    )

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
    # Path Mapping (cross-host / Docker setups)
    #
    # Sonarr/Radarr report paths using their own mount namespace.
    # When Marquee runs on a different machine or in a container with
    # different volume mounts, these settings translate *arr paths to
    # local filesystem paths.  Bazarr uses the same pattern.
    #
    # Example:
    #   Sonarr sees:  /data/media/Shows/Breaking Bad/
    #   Marquee sees: /media/Shows/Breaking Bad/
    #   → ARR_PATH_PREFIX=/data/media  LOCAL_PATH_PREFIX=/media
    # ------------------------------------------------------------------
    ARR_PATH_PREFIX: Optional[str] = Field(
        default=None,
        description="Path prefix used by Sonarr/Radarr (e.g. /data/media)",
    )
    LOCAL_PATH_PREFIX: Optional[str] = Field(
        default=None,
        description="Path prefix as mounted in Marquee (e.g. /media)",
    )

    @property
    def path_mapping_configured(self) -> bool:
        return self.ARR_PATH_PREFIX is not None and self.LOCAL_PATH_PREFIX is not None

    def translate_arr_path(self, arr_path: str) -> str:
        """Translate a path from the *arr's mount namespace to Marquee's."""
        if not arr_path or not self.ARR_PATH_PREFIX:
            return arr_path
        if arr_path.startswith(self.ARR_PATH_PREFIX):
            return self.LOCAL_PATH_PREFIX + arr_path[len(self.ARR_PATH_PREFIX):]
        return arr_path

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
    # Deduplication
    # ------------------------------------------------------------------
    DEDUP_PHASH_THRESHOLD: int = Field(
        default=6,
        description="Hamming distance ≤ N means visual near-duplicate. "
        "0 = identical, <6 = near-dupe, 6-10 = similar, >10 = different.",
    )
    DEDUP_MIN_POSTER_WIDTH: int = Field(
        default=500,
        description="Skip poster candidates narrower than this in pixels. "
        "TMDB minimum poster width is 500px.",
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
