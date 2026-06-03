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
    LOG_FORMAT: str = "text"  # "text" | "json" — switch point for structured logging

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
    # Radarr Path Mapping (cross-host / Docker setups)
    #
    # Radarr reports paths using its own mount namespace.  When Marquee
    # runs on a different machine with different volume mounts, these
    # settings translate Radarr paths to local filesystem paths.
    #
    # Example:
    #   Radarr sees:  /plunder/movies/Dune (2021)/
    #   Marquee sees: /Volumes/PLUNDER/Media/Movies/Dune (2021)/
    #   → RADARR_PATH_PREFIX=/plunder/movies
    #     RADARR_MEDIA_PATH=/Volumes/PLUNDER/Media/Movies
    # ------------------------------------------------------------------
    RADARR_PATH_PREFIX: Optional[str] = Field(
        default=None,
        description="Path prefix used by Radarr (e.g. /plunder/movies)",
    )
    RADARR_MEDIA_PATH: Optional[str] = Field(
        default=None,
        description="Corresponding path prefix as mounted in Marquee "
        "(e.g. /Volumes/PLUNDER/Media/Movies)",
    )

    @property
    def radarr_path_configured(self) -> bool:
        return (
            self.RADARR_PATH_PREFIX is not None
            and self.RADARR_MEDIA_PATH is not None
        )

    def translate_radarr_path(self, arr_path: str) -> str:
        """Translate a path from Radarr's mount namespace to Marquee's.

        Returns the path untranslated if no mapping is configured or the
        path doesn't start with the configured prefix (caller should log).
        """
        if not arr_path or not self.RADARR_PATH_PREFIX:
            return arr_path
        if arr_path.startswith(self.RADARR_PATH_PREFIX):
            return self.RADARR_MEDIA_PATH + arr_path[len(self.RADARR_PATH_PREFIX):]
        return arr_path

    # ------------------------------------------------------------------
    # Sonarr Path Mapping
    #
    # Same pattern as Radarr — translate Sonarr paths to local filesystem.
    #
    # Example:
    #   Sonarr sees:  /plunder/tv/Breaking Bad/
    #   Marquee sees: /Volumes/PLUNDER/Media/TV/Breaking Bad/
    #   → SONARR_PATH_PREFIX=/plunder/tv
    #     SONARR_MEDIA_PATH=/Volumes/PLUNDER/Media/TV
    # ------------------------------------------------------------------
    SONARR_PATH_PREFIX: Optional[str] = Field(
        default=None,
        description="Path prefix used by Sonarr (e.g. /plunder/tv)",
    )
    SONARR_MEDIA_PATH: Optional[str] = Field(
        default=None,
        description="Corresponding path prefix as mounted in Marquee "
        "(e.g. /Volumes/PLUNDER/Media/TV)",
    )

    @property
    def sonarr_path_configured(self) -> bool:
        return (
            self.SONARR_PATH_PREFIX is not None
            and self.SONARR_MEDIA_PATH is not None
        )

    def translate_sonarr_path(self, arr_path: str) -> str:
        """Translate a path from Sonarr's mount namespace to Marquee's.

        Returns the path untranslated if no mapping is configured or the
        path doesn't start with the configured prefix (caller should log).
        """
        if not arr_path or not self.SONARR_PATH_PREFIX:
            return arr_path
        if arr_path.startswith(self.SONARR_PATH_PREFIX):
            return self.SONARR_MEDIA_PATH + arr_path[len(self.SONARR_PATH_PREFIX):]
        return arr_path

    # ------------------------------------------------------------------
    # Path Validation — Media Roots
    #
    # ``effective_media_roots`` auto-derives allowed roots from the
    # configured Radarr/Sonarr media paths plus any manual overrides in
    # MEDIA_ROOTS.  Roots are resolved to their canonical path so symlink
    # aliases don't defeat the check.
    #
    # Default (MEDIA_ROOTS=[] + no path mapping): no enforcement.
    # ------------------------------------------------------------------
    MEDIA_ROOTS: list[str] = Field(
        default=[],
        description="Additional allowed root directories.  Radarr/Sonarr "
        "media paths are automatically included.  Empty + no path mapping "
        "= allow all paths.",
    )

    @property
    def effective_media_roots(self) -> list[Path]:
        """Canonical set of allowed root directories.

        Built from: ``RADARR_MEDIA_PATH``, ``SONARR_MEDIA_PATH``,
        and any manual ``MEDIA_ROOTS`` entries.  Each is resolved to
        its real path so symlinks don't break the ``startswith`` check.
        """
        roots: set[Path] = set()
        for raw in self.MEDIA_ROOTS:
            try:
                roots.add(Path(raw).resolve())
            except (OSError, RuntimeError):
                pass  # unresolvable → skip

        if self.RADARR_MEDIA_PATH:
            try:
                roots.add(Path(self.RADARR_MEDIA_PATH).resolve())
            except (OSError, RuntimeError):
                pass

        if self.SONARR_MEDIA_PATH:
            try:
                roots.add(Path(self.SONARR_MEDIA_PATH).resolve())
            except (OSError, RuntimeError):
                pass

        return sorted(roots)

    # ------------------------------------------------------------------
    # Sync
    # ------------------------------------------------------------------
    SYNC_INTERVAL_MINUTES: int = Field(
        default=15,
        description="Minutes between periodic *arr syncs",
    )
    SYNC_COOLDOWN_SECONDS: int = Field(
        default=300,
        description="Minimum seconds between manual sync triggers (rate limit). "
        "Default 300 = 5 minutes.",
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
        default="clip-vit-b-16",
        description="CLIP variant for poster embeddings and taste matching: "
        "clip-vit-b-32 | clip-vit-b-16 | clip-vit-l-14",
    )

    # Taste scoring weights (emb vs color)
    TASTE_EMB_WEIGHT: float = Field(
        default=0.8,
        description="Weight for CLIP visual similarity in taste scoring",
    )
    TASTE_COLOR_WEIGHT: float = Field(
        default=0.2,
        description="Weight for LAB color histogram similarity in taste scoring",
    )

    # Zero-shot negative prompting
    NEGATIVE_PROMPTS: list[str] = Field(
        default=[
            "A crowded movie poster with floating heads",
            "A generic Hollywood poster with actor faces arranged in a grid",
            "A poster dominated by photos of actors' faces",
        ],
        description="Text prompts for CLIP zero-shot negative filtering. "
        "Any candidate with cosine similarity > NEGATIVE_PROMPT_THRESHOLD "
        "to any prompt is rejected.",
    )
    NEGATIVE_PROMPT_THRESHOLD: float = Field(
        default=0.25,
        description="Cosine similarity above this to any negative prompt → reject candidate",
    )

    # ------------------------------------------------------------------
    # OCR Filtering
    # ------------------------------------------------------------------
    OCR_WORKERS: int = Field(
        default=6,
        description="Number of parallel PaddleOCR worker processes",
    )
    OCR_CONFIDENCE_THRESHOLD: float = Field(
        default=0.75,
        description="Minimum OCR confidence for full-image text regions",
    )
    OCR_STRIP_CONFIDENCE_THRESHOLD: float = Field(
        default=0.65,
        description="Minimum OCR confidence for top-strip text (4K/UHD badges)",
    )
    OCR_BOTTOM_CONFIDENCE_THRESHOLD: float = Field(
        default=0.50,
        description="Minimum OCR confidence for bottom-strip text (credits). "
        "Lower than top strip because fine-print credits on complex backgrounds "
        "reliably score below 0.65.",
    )

    # ------------------------------------------------------------------
    # Poster Filename Formats
    #
    # Template variables:
    #   {movie_basename} — movie filename without extension (movies only)
    #   {season}         — season number (seasons only)
    #   {season:02d}     — zero-padded season number (seasons only)
    # ------------------------------------------------------------------
    MOVIE_POSTER_FORMAT: str = Field(
        default="poster.jpg",
        description="Filename for movie posters. Use {movie_basename} "
        "to derive from the media file (e.g. '{movie_basename}.jpg').",
    )
    SERIES_POSTER_FORMAT: str = Field(
        default="poster.jpg",
        description="Filename for TV series posters.",
    )
    SEASON_POSTER_FORMAT: str = Field(
        default="season{season:02d}-poster.jpg",
        description="Filename for season posters. {season} = season number. "
        "Example: 'season{season:02d}-poster.jpg' → 'season01-poster.jpg'",
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
