"""Centralised application configuration.

All settings are loaded from environment variables and/or a ``.env`` file.
Pydantic-settings handles type coercion, validation, and defaults.
"""

from __future__ import annotations

import contextlib
from pathlib import Path

from pydantic import AliasChoices, Field
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
    # Security / Authentication
    #
    # A single static API key guards every endpoint (except /health).
    # Send it as ``Authorization: Bearer <key>``, ``X-Api-Key: <key>``,
    # or ``?apikey=<key>`` (the query form lets Radarr/Sonarr webhook URLs
    # and the browser carry it).
    #
    #   DEBUG=true                → auth is bypassed entirely (local dev).
    #   DEBUG=false + API_KEY set → key required (loopback may be exempt).
    #   DEBUG=false + no API_KEY  → protected endpoints fail closed (503).
    # ------------------------------------------------------------------
    API_KEY: str | None = Field(
        default=None,
        description="Static API key required on all endpoints except /health. "
        "Generate one with: python -c \"import secrets; print(secrets.token_urlsafe(32))\".",
    )
    AUTH_ALLOW_LOCAL: bool = Field(
        default=True,
        description="When true, loopback requests (127.0.0.1/::1) skip the API key "
        "even outside DEBUG. Tailscale (100.64.0.0/10) and LAN addresses always "
        "require the key.",
    )

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
    def data_dir_path(self) -> Path:
        """Absolute path to the runtime data directory."""
        path = self._project_root / self.DATA_DIR
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def poster_cache_path(self) -> Path:
        """Absolute path to the poster cache directory."""
        return self.data_dir_path / "cache" / "posters"

    @property
    def poster_staging_path(self) -> Path:
        """Absolute path to the staging directory for downloaded candidates."""
        return self.data_dir_path / "staging"

    @property
    def runs_archive_path(self) -> Path:
        """Where per-run pipeline_run.json copies are archived by run_id.

        Survives re-runs of the same movie (the experiments/runs/<title>/
        working dir is overwritten on re-run; this archive is not).
        """
        return self.data_dir_path / "runs" / "archive"

    @property
    def letterbox_preview_path(self) -> Path:
        """Where generated letterbox preview/thumbnail frames (webp) live."""
        return self.data_dir_path / "cache" / "letterbox"

    # ------------------------------------------------------------------
    # Internal Backups
    # ------------------------------------------------------------------
    BACKUP_INTERVAL_HOURS: int = Field(
        default=24,
        validation_alias=AliasChoices(
            "BACKUP_INTERVAL_HOURS", "MARQUEE_BACKUP_INTERVAL_HOURS"
        ),
        description="Hours between automatic backups. Set to 0 to disable scheduled backups.",
    )
    BACKUP_RETENTION_DAYS: int = Field(
        default=7,
        validation_alias=AliasChoices(
            "BACKUP_RETENTION_DAYS", "MARQUEE_BACKUP_RETENTION_DAYS"
        ),
        description="Number of daily backup directories to retain after rotation.",
    )
    BACKUP_INITIAL_DELAY_SECONDS: int = Field(
        default=300,
        validation_alias=AliasChoices(
            "BACKUP_INITIAL_DELAY_SECONDS", "MARQUEE_BACKUP_INITIAL_DELAY_SECONDS"
        ),
        description="Seconds to wait after startup before the first scheduled backup.",
    )
    BACKUP_DIR: str = Field(
        default="data/backups",
        validation_alias=AliasChoices("BACKUP_DIR", "MARQUEE_BACKUP_DIR"),
        description="Directory for internal backup artifacts.",
    )

    @property
    def backup_dir_path(self) -> Path:
        """Absolute path to the internal backup directory."""
        path = Path(self.BACKUP_DIR)
        if not path.is_absolute():
            path = self._project_root / path
        path.mkdir(parents=True, exist_ok=True)
        return path

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
    TMDB_READ_ACCESS_TOKEN: str | None = Field(
        default=None,
        description="TMDB API v3 Read Access Token (Bearer token)",
    )

    @property
    def tmdb_configured(self) -> bool:
        return self.TMDB_READ_ACCESS_TOKEN is not None

    # ------------------------------------------------------------------
    # Fanart.tv
    # ------------------------------------------------------------------
    FANART_API_KEY: str | None = Field(
        default=None,
        description="Fanart.tv personal API key",
    )

    @property
    def fanart_configured(self) -> bool:
        return self.FANART_API_KEY is not None

    # ------------------------------------------------------------------
    # TheTVDB
    # ------------------------------------------------------------------
    TVDB_API_KEY: str | None = Field(
        default=None,
        description="TheTVDB API key (v4)",
    )

    @property
    def tvdb_configured(self) -> bool:
        return self.TVDB_API_KEY is not None

    # ------------------------------------------------------------------
    # Radarr
    # ------------------------------------------------------------------
    RADARR_URL: str | None = Field(
        default=None,
        description="Radarr base URL (e.g. http://localhost:7878)",
    )
    RADARR_API_KEY: str | None = Field(
        default=None,
        description="Radarr API key",
    )

    @property
    def radarr_configured(self) -> bool:
        return self.RADARR_URL is not None and self.RADARR_API_KEY is not None

    # ------------------------------------------------------------------
    # Sonarr
    # ------------------------------------------------------------------
    SONARR_URL: str | None = Field(
        default=None,
        description="Sonarr base URL (e.g. http://localhost:8989)",
    )
    SONARR_API_KEY: str | None = Field(
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
    RADARR_PATH_PREFIX: str | None = Field(
        default=None,
        description="Path prefix used by Radarr (e.g. /plunder/movies)",
    )
    RADARR_MEDIA_PATH: str | None = Field(
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
    SONARR_PATH_PREFIX: str | None = Field(
        default=None,
        description="Path prefix used by Sonarr (e.g. /plunder/tv)",
    )
    SONARR_MEDIA_PATH: str | None = Field(
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
        candidates = [*self.MEDIA_ROOTS, self.RADARR_MEDIA_PATH, self.SONARR_MEDIA_PATH]
        for raw in candidates:
            if not raw:
                continue
            with contextlib.suppress(OSError, RuntimeError):  # unresolvable → skip
                roots.add(Path(raw).resolve())

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
    HEAL_ENABLED: bool = Field(
        default=True,
        description="Run the periodic self-heal poster existence scan.",
    )

    # ------------------------------------------------------------------
    # Rate Limiting — cooldowns for expensive endpoints (skipped when DEBUG)
    # ------------------------------------------------------------------
    RATE_PIPELINE_RUN_SECONDS: int = Field(
        default=15, description="Per-movie cooldown between poster-pipeline runs."
    )
    RATE_LETTERBOX_DETECT_SECONDS: int = Field(
        default=20, description="Per-movie cooldown between single letterbox detections."
    )
    RATE_LETTERBOX_BATCH_SECONDS: int = Field(
        default=300, description="Cooldown between batch letterbox detections."
    )
    RATE_TASTE_RETRAIN_SECONDS: int = Field(
        default=60, description="Cooldown between learned-head retrains."
    )
    RATE_TASTE_MAP_REBUILD_SECONDS: int = Field(
        default=300, description="Cooldown between taste-map rebuilds."
    )

    # ------------------------------------------------------------------
    # Webhooks (Radarr/Sonarr → poster restoration)
    # ------------------------------------------------------------------
    WEBHOOK_DRY_RUN: bool = Field(
        default=False,
        description="Log + record webhook events without touching the filesystem.",
    )

    # ------------------------------------------------------------------
    # Letterbox crop detection / tag application (design 04-letterbox)
    # ------------------------------------------------------------------
    LETTERBOX_ENABLED: bool = Field(
        default=True, description="Enable the letterbox crop-detection feature."
    )
    LETTERBOX_DETECT_METHOD: str = Field(
        default="cropdetect",
        description="Detection backend: 'cropdetect' (ffmpeg, default) or "
        "'trim' (ImageMagick fallback for faint/color-cast bars).",
    )
    LETTERBOX_TRIM_FUZZ: list[int] = Field(
        default=[5, 15, 25],
        description="Fuzz percentages tried by the 'trim' backend (needs ImageMagick).",
    )
    LETTERBOX_FFMPEG: str = Field(default="ffmpeg", description="ffmpeg binary path/name.")
    LETTERBOX_FFPROBE: str = Field(default="ffprobe", description="ffprobe binary path/name.")
    LETTERBOX_MKVPROPEDIT: str = Field(
        default="mkvpropedit", description="mkvpropedit binary path/name."
    )
    LETTERBOX_MKVMERGE: str = Field(
        default="mkvmerge", description="mkvmerge binary path/name."
    )
    LETTERBOX_CONVERT: str = Field(
        default="convert", description="ImageMagick 'convert' binary (trim backend only)."
    )
    LETTERBOX_MOVIE_SAMPLES_MIN: int = Field(default=5, description="Movie sampling start (minutes).")
    LETTERBOX_MOVIE_SAMPLES_MAX: int = Field(default=60, description="Movie sampling end (minutes).")
    LETTERBOX_MOVIE_SAMPLE_STEP: int = Field(default=5, description="Minutes between movie samples.")
    LETTERBOX_TV_SAMPLES: list[int] = Field(
        default=[5, 10, 15], description="Sample timestamps for TV episodes (minutes)."
    )
    LETTERBOX_WINDOW_SECONDS: int = Field(
        default=2, description="cropdetect accumulation window per sample (seconds)."
    )
    LETTERBOX_CROPDETECT_LIMIT: int = Field(
        default=24, description="cropdetect black-luma threshold (0-255)."
    )
    LETTERBOX_CROPDETECT_HDR_LIMIT: int = Field(
        default=80,
        description="cropdetect black-luma threshold for HDR/PQ/HLG sources.",
    )
    LETTERBOX_CROPDETECT_ROUND: int = Field(
        default=2, description="cropdetect dimension rounding (must be even for codecs)."
    )
    LETTERBOX_NOISE_PX: int = Field(
        default=4, description="Bars at or below this many px count as 'no bars'."
    )
    LETTERBOX_MIN_BAR_PX: int = Field(
        default=8, description="A bar must exceed this to count as a real scope bar."
    )
    LETTERBOX_AGREE_PX: int = Field(
        default=2, description="Max spread across samples for High confidence (Case C)."
    )
    LETTERBOX_MEDIUM_SPREAD_PX: int = Field(
        default=20, description="Spread boundary between Medium and Low confidence."
    )
    LETTERBOX_ASYM_PX: int = Field(
        default=2, description="Top/bottom asymmetry tolerance (px) before honoring uneven bars."
    )
    LETTERBOX_EARLY_STOP_WINDOWS: int = Field(
        default=3, description="Consecutive no-bar samples that trigger early termination."
    )
    LETTERBOX_MAX_PARALLEL: int = Field(
        default=0, description="Batch-detect worker count; 0 = auto (cpu_count - 1)."
    )
    LETTERBOX_AUTO_APPLY_HIGH: bool = Field(
        default=False, description="Opt-in: auto-apply High-confidence detections after a scan."
    )
    LETTERBOX_ASYMMETRIC: bool = Field(
        default=False, description="Honor uneven top/bottom bars instead of forcing symmetry."
    )
    LETTERBOX_HEAL_ENABLED: bool = Field(
        default=True, description="Run the periodic letterbox tag-drift verification scan."
    )
    LETTERBOX_HEAL_INTERVAL_MINUTES: int = Field(
        default=360, description="Minutes between letterbox tag-drift scans."
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
