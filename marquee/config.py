"""Centralised application configuration.

All settings are loaded from environment variables and/or a ``.env`` file.
Pydantic-settings handles type coercion, validation, and defaults.
"""

from __future__ import annotations

import contextlib
import socket
from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field, model_validator
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
    MARQUEE_ENVIRONMENT: Literal["development", "test", "production"] = "production"
    MARQUEE_PROCESS_ROLE: Literal["api", "worker", "scheduler", "migration", "test"] = "api"
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
        'Generate one with: python -c "import secrets; print(secrets.token_urlsafe(32))".',
    )
    AUTH_ALLOW_LOCAL: bool = Field(
        default=True,
        description="When true, loopback requests (127.0.0.1/::1) skip the API key "
        "even outside DEBUG. Tailscale (100.64.0.0/10) and LAN addresses always "
        "require the key.",
    )
    AUTH_BRUTE_LOCKOUT_ATTEMPTS: int = Field(
        default=10,
        description="Failed auth attempts from one IP within AUTH_BRUTE_WINDOW_SECONDS "
        "before that IP is locked out.",
    )
    AUTH_BRUTE_WINDOW_SECONDS: int = Field(
        default=60,
        description="Rolling window (seconds) used to count auth failures per IP.",
    )
    AUTH_BRUTE_LOCKOUT_SECONDS: int = Field(
        default=300,
        description="How long (seconds) a locked-out IP must wait before retrying.",
    )

    # ------------------------------------------------------------------
    # Database
    # ------------------------------------------------------------------
    DB_URL: str = "postgresql+asyncpg://marquee:marquee@postgres:5432/marquee"
    DB_LOCK_TIMEOUT_MS: int = Field(
        default=10_000,
        ge=0,
        description="PostgreSQL lock wait timeout in milliseconds; 0 disables it.",
    )
    DB_IDLE_TXN_TIMEOUT_MS: int = Field(
        default=300_000,
        ge=0,
        description="PostgreSQL idle-in-transaction timeout in milliseconds; 0 disables it.",
    )
    DB_API_POOL_SIZE: int = Field(default=10, ge=1, le=64)
    DB_API_MAX_OVERFLOW: int = Field(default=5, ge=0, le=32)
    DB_WORKER_POOL_SIZE: int = Field(default=2, ge=1, le=16)
    DB_WORKER_MAX_OVERFLOW: int = Field(default=1, ge=0, le=16)
    DB_SCHEDULER_POOL_SIZE: int = Field(default=1, ge=1, le=8)
    DB_SCHEDULER_MAX_OVERFLOW: int = Field(default=1, ge=0, le=8)
    DB_MIGRATION_CONNECTIONS: int = Field(default=1, ge=1, le=4)
    DB_DEPLOYMENT_MAX_CONNECTIONS: int = Field(default=32, ge=1, le=256)
    HEALTH_READY_TIMEOUT_SECONDS: float = Field(default=2.0, ge=0.1, le=30.0)
    HEALTH_STARTUP_ATTEMPTS: int = Field(default=3, ge=1, le=30)
    HEALTH_STARTUP_RETRY_SECONDS: float = Field(default=1.0, ge=0.0, le=30.0)
    DATA_DIR: str = "data"
    # Standalone dev: the API auto-spawns the worker + scheduler as child
    # processes so nothing has to be started by hand.  The Compose topology runs
    # dedicated worker/scheduler services, so it sets this false on the API.
    JOB_EMBEDDED_WORKERS: bool = Field(
        default=True,
        description="Auto-spawn the job worker + scheduler as supervised child "
        "processes from the API. Set false when dedicated worker/scheduler "
        "services run separately (e.g. docker-compose).",
    )
    JOB_EMBEDDED_WORKER_COUNT: int = Field(
        default=1,
        ge=1,
        le=8,
        description="How many embedded worker processes to spawn. Resource caps "
        "already serialize GPU/write work, so one is usually enough.",
    )
    JOB_WORKER_CONCURRENCY: int = Field(default=4, ge=1, le=32)
    JOB_WORKER_NODE_ID: str = Field(default_factory=socket.gethostname, min_length=1, max_length=100)
    JOB_CONTROL_CONCURRENCY: int = Field(default=4, ge=1, le=32)
    JOB_NETWORK_CONCURRENCY: int = Field(default=4, ge=1, le=32)
    JOB_CPU_CONCURRENCY: int = Field(default=2, ge=1, le=32)
    JOB_MEDIA_READ_CONCURRENCY: int = Field(default=2, ge=1, le=32)
    JOB_MEDIA_WRITE_CONCURRENCY: int = Field(default=1, ge=1, le=32)
    JOB_GPU_CONCURRENCY: int = Field(default=1, ge=1, le=32)
    JOB_MAINTENANCE_CONCURRENCY: int = Field(default=1, ge=1, le=32)
    JOB_SAFETY_GATE_CONNECTIONS: int = Field(
        default=4,
        ge=1,
        le=32,
        description="Dedicated advisory-lock sessions budgeted per worker process.",
    )
    JOB_PGQUEUER_BATCH_SIZE: int = Field(default=2, ge=1, le=16)
    JOB_PGQUEUER_HEARTBEAT_SECONDS: float = Field(default=60.0, ge=1.0, le=3600.0)
    JOB_PGQUEUER_DEQUEUE_SECONDS: float = Field(default=10.0, ge=0.05, le=300.0)
    JOB_RUNTIME_HEARTBEAT_SECONDS: float = Field(default=10.0, ge=1.0, le=300.0)
    JOB_RUNTIME_EXPIRY_SECONDS: float = Field(default=30.0, ge=3.0, le=900.0)
    JOB_RUNTIME_QUERY_LIMIT: int = Field(default=100, ge=1, le=500)
    JOB_PRODUCTION_SCHEDULES_ENABLED: bool = False
    JOB_DOVI_CONVERSION_CERTIFIED: bool = Field(
        default=False,
        description="Enable Dolby Vision conversion only after an owner-approved real dovi_tool "
        "fixture smoke certifies the deployed tool and media path.",
    )
    JOB_WORKER_ENTRYPOINTS: str = Field(
        default="control,network,cpu,media_read,media_write,gpu,maintenance",
        description="Comma-separated PgQueuer execution classes this worker may advertise.",
    )
    JOB_POLL_SECONDS: float = Field(default=0.5, ge=0.05, le=30.0)
    JOB_ADMISSION_TIMEOUT_SECONDS: float = Field(default=30.0, ge=0.1, le=3600.0)
    JOB_PROCESS_COOPERATIVE_SECONDS: float = Field(default=2.0, ge=0.05, le=300.0)
    JOB_PROCESS_TERM_SECONDS: float = Field(default=5.0, ge=0.05, le=300.0)
    JOB_INTENT_MONITOR_SECONDS: float = Field(default=30.0, ge=1.0, le=3600.0)
    JOB_INTENT_MONITOR_BATCH_SIZE: int = Field(default=25, ge=1, le=100)
    JOB_EVENT_LISTENER_CONNECTIONS: int = Field(
        default=1,
        ge=1,
        le=1,
        description="Exactly one dedicated event-listener connection per API instance.",
    )
    JOB_EVENT_CLIENT_QUEUE_SIZE: int = Field(default=128, ge=8, le=4096)
    JOB_EVENT_TAIL_BATCH_SIZE: int = Field(default=256, ge=1, le=2000)
    JOB_EVENT_REPLAY_LIMIT: int = Field(default=128, ge=1, le=4096)
    JOB_EVENT_REPAIR_SECONDS: float = Field(default=1.0, ge=0.1, le=30.0)
    JOB_EVENT_RECONNECT_SECONDS: float = Field(default=1.0, ge=0.1, le=30.0)
    JOB_EVENT_KEEPALIVE_SECONDS: float = Field(default=15.0, ge=1.0, le=120.0)
    JOB_LOG_CAP_BYTES: int = Field(
        default=100 * 1024 * 1024,
        ge=100 * 1024 * 1024,
        le=100 * 1024 * 1024,
    )
    JOB_LOG_RETENTION_DAYS: int = Field(default=30, ge=30, le=30)
    JOB_LOG_QUEUE_SIZE: int = Field(default=1024, ge=64, le=8192)
    JOB_LOG_MESSAGE_CHARS: int = Field(default=4096, ge=256, le=16_384)
    JOB_LOG_METADATA_LINES: int = Field(default=256, ge=16, le=4096)
    JOB_LOG_STREAM_POLL_SECONDS: float = Field(default=0.25, ge=0.05, le=5.0)
    JOB_LOG_STREAM_QUEUE_SIZE: int = Field(default=64, ge=8, le=1024)
    JOB_ARTIFACT_VIRTUAL_MAX_BYTES: int = Field(
        default=1024 * 1024, ge=64 * 1024, le=1024 * 1024
    )
    JOB_ARTIFACT_STRING_CHARS: int = Field(default=16_384, ge=256, le=16_384)
    JOB_ARTIFACT_EVENT_LIMIT: int = Field(default=1000, ge=1, le=1000)
    JOB_HEARTBEAT_SECONDS: int = Field(default=10, ge=1, le=300)
    JOB_LEASE_SECONDS: int = Field(default=60, ge=10, le=3600)
    JOB_SHUTDOWN_GRACE_SECONDS: int = Field(default=30, ge=1, le=600)
    JOB_ENCODE_STALL_SECONDS: int = Field(
        default=120,
        ge=30,
        description="Maximum seconds FFmpeg may go without emitting encode progress.",
    )
    JOB_CANCEL_FORCE_SECONDS: int = Field(
        default=30,
        ge=1,
        description="Seconds before recovery force-finalizes a requested cancellation.",
    )
    JOB_MAX_RUNTIME_SECONDS: int = Field(
        default=3600,
        ge=60,
        le=86400,
        description="Maximum runtime for any job (seconds). Jobs exceeding this "
        "are terminated to prevent hung workers from blocking resources.",
    )
    JOB_MEDIA_MAX_RUNTIME_SECONDS: int = Field(
        default=21600,
        ge=60,
        le=86400,
        description="Maximum runtime for long media mutation jobs (seconds).",
    )
    JOB_GPU_SLOTS: int = Field(default=1, ge=0, le=8)
    JOB_MEDIA_READ_SLOTS: int = Field(default=2, ge=1, le=16)
    JOB_MEDIA_WRITE_SLOTS: int = Field(default=1, ge=1, le=8)
    JOB_NETWORK_SLOTS: int = Field(default=4, ge=1, le=32)
    JOB_RETENTION_DAYS: int = Field(
        default=30,
        ge=1,
        le=3650,
        description=(
            "Days to retain terminal canonical jobs and their durable evidence before the "
            "daily job_retention_purge job deletes them."
        ),
    )
    # Filesystem the dashboard disk gauge reports on. Defaults to the volume
    # holding DATA_DIR; point it at the media volume for a more useful number.
    METRICS_DISK_PATH: str | None = None
    METRICS_SAMPLE_INTERVAL_SECONDS: int = Field(
        default=15,
        ge=5,
        le=300,
        description="Seconds between host-telemetry samples written to "
        "system_metrics_samples by the background sampler. Not a Job — never "
        "visible in job history.",
    )
    METRICS_RETENTION_DAYS: int = Field(
        default=14,
        ge=1,
        le=365,
        description="Days to retain system_metrics_samples rows before the "
        "daily system_metrics_purge job deletes them. Independent of "
        "JOB_RETENTION_DAYS.",
    )

    @property
    def _project_root(self) -> Path:
        """Absolute path to the project root (parent of marquee/ package)."""
        return Path(__file__).parent.parent

    @property
    def db_url_resolved(self) -> str:
        """PostgreSQL connection URL used by every Marquee runtime role."""
        return self.DB_URL

    @property
    def db_pool_budget(self) -> tuple[int, int]:
        """Return the SQLAlchemy base/overflow budget for this process role."""
        if self.MARQUEE_PROCESS_ROLE == "worker":
            return self.DB_WORKER_POOL_SIZE, self.DB_WORKER_MAX_OVERFLOW
        if self.MARQUEE_PROCESS_ROLE == "scheduler":
            return self.DB_SCHEDULER_POOL_SIZE, self.DB_SCHEDULER_MAX_OVERFLOW
        return self.DB_API_POOL_SIZE, self.DB_API_MAX_OVERFLOW

    @property
    def deployment_connection_budget(self) -> int:
        """Maximum connections for one API, configured workers, scheduler, and migration."""
        api = (
            self.DB_API_POOL_SIZE
            + self.DB_API_MAX_OVERFLOW
            + self.JOB_EVENT_LISTENER_CONNECTIONS
        )
        worker = (
            self.DB_WORKER_POOL_SIZE
            + self.DB_WORKER_MAX_OVERFLOW
            + 1
            + self.JOB_SAFETY_GATE_CONNECTIONS
        )
        scheduler = self.DB_SCHEDULER_POOL_SIZE + self.DB_SCHEDULER_MAX_OVERFLOW + 1
        return (
            api
            + self.JOB_EMBEDDED_WORKER_COUNT * worker
            + scheduler
            + self.DB_MIGRATION_CONNECTIONS
        )

    @model_validator(mode="after")
    def validate_connection_budget(self) -> Settings:
        if self.deployment_connection_budget > self.DB_DEPLOYMENT_MAX_CONNECTIONS:
            raise ValueError(
                "configured role connection budget exceeds DB_DEPLOYMENT_MAX_CONNECTIONS"
            )
        if self.JOB_EVENT_REPLAY_LIMIT > self.JOB_EVENT_CLIENT_QUEUE_SIZE:
            raise ValueError("JOB_EVENT_REPLAY_LIMIT cannot exceed the client queue size")
        if self.JOB_PGQUEUER_BATCH_SIZE > self.JOB_WORKER_CONCURRENCY:
            raise ValueError("JOB_PGQUEUER_BATCH_SIZE cannot exceed JOB_WORKER_CONCURRENCY")
        if self.JOB_SAFETY_GATE_CONNECTIONS < self.JOB_WORKER_CONCURRENCY:
            raise ValueError(
                "JOB_SAFETY_GATE_CONNECTIONS cannot be below JOB_WORKER_CONCURRENCY"
            )
        return self

    @property
    def data_dir_path(self) -> Path:
        """Absolute path to the runtime data directory."""
        path = self._project_root / self.DATA_DIR
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def job_log_redaction_secrets(self) -> tuple[str, ...]:
        """Return bounded configured credential values without logging their sources."""
        values = (
            self.API_KEY,
            self.DB_URL,
            self.TMDB_READ_ACCESS_TOKEN,
            self.FANART_API_KEY,
            self.TVDB_API_KEY,
            self.RADARR_API_KEY,
            self.SONARR_API_KEY,
        )
        return tuple(sorted({value for value in values if value}, key=len, reverse=True))

    @property
    def metrics_disk_path(self) -> Path:
        """Filesystem path the dashboard disk gauge reports on."""
        if self.METRICS_DISK_PATH:
            return Path(self.METRICS_DISK_PATH)
        return self.data_dir_path

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

        Survives re-runs of the same movie (the data/runs/work/<title>/
        working dir is overwritten on re-run; this archive is not).
        """
        return self.data_dir_path / "runs" / "archive"

    @property
    def runs_work_path(self) -> Path:
        """Where live pipeline working output for each movie is written."""
        return self.data_dir_path / "runs" / "work"

    @property
    def letterbox_preview_path(self) -> Path:
        """Where generated letterbox preview/thumbnail frames (webp) live."""
        return self.data_dir_path / "cache" / "letterbox"

    # ------------------------------------------------------------------
    # Internal Backups
    # ------------------------------------------------------------------
    BACKUP_INTERVAL_HOURS: int = Field(
        default=24,
        validation_alias=AliasChoices("BACKUP_INTERVAL_HOURS", "MARQUEE_BACKUP_INTERVAL_HOURS"),
        description="Hours between automatic backups. Set to 0 to disable scheduled backups.",
    )
    BACKUP_RETENTION_DAYS: int = Field(
        default=7,
        validation_alias=AliasChoices("BACKUP_RETENTION_DAYS", "MARQUEE_BACKUP_RETENTION_DAYS"),
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
        return self.RADARR_PATH_PREFIX is not None and self.RADARR_MEDIA_PATH is not None

    def translate_radarr_path(self, arr_path: str) -> str:
        """Translate a path from Radarr's mount namespace to Marquee's.

        Returns the path untranslated if no mapping is configured or the
        path doesn't start with the configured prefix (caller should log).
        """
        if not arr_path or not self.RADARR_PATH_PREFIX:
            return arr_path
        if arr_path.startswith(self.RADARR_PATH_PREFIX):
            return self.RADARR_MEDIA_PATH + arr_path[len(self.RADARR_PATH_PREFIX) :]
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
        return self.SONARR_PATH_PREFIX is not None and self.SONARR_MEDIA_PATH is not None

    def translate_sonarr_path(self, arr_path: str) -> str:
        """Translate a path from Sonarr's mount namespace to Marquee's.

        Returns the path untranslated if no mapping is configured or the
        path doesn't start with the configured prefix (caller should log).
        """
        if not arr_path or not self.SONARR_PATH_PREFIX:
            return arr_path
        if arr_path.startswith(self.SONARR_PATH_PREFIX):
            return self.SONARR_MEDIA_PATH + arr_path[len(self.SONARR_PATH_PREFIX) :]
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
    HEAL_RECENT_DEPLOY_GRACE_MINUTES: int = Field(
        default=10,
        description="Skip heal-restoring movies deployed within this window — "
        "the scan runs in the worker process, so a time window (not a lock) "
        "is what prevents it racing a concurrent deploy from the API process.",
    )

    # ------------------------------------------------------------------
    # Request size cap
    # ------------------------------------------------------------------
    MAX_REQUEST_BODY_BYTES: int = Field(
        default=1_048_576,
        description="Maximum Content-Length for any request body (bytes). Default 1 MB. "
        "Raise only if file-upload endpoints are added.",
    )

    # ------------------------------------------------------------------
    # Pipeline Performance Tuning
    # ------------------------------------------------------------------
    PIPELINE_CACHE_EXTRACTOR: bool = Field(
        default=False,
        description="Keep CLIP/DINOv2 models loaded in GPU memory between runs. "
        "Set true for back-to-back poster runs (faster), false to share GPU with "
        "letterbox jobs (more flexible). Process-lifetime cache on small GPUs may "
        "prevent letterbox re-encode from allocating decode buffers.",
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
        default=60, description="Cooldown between ranking-residual retrains."
    )
    RATE_TASTE_MAP_REBUILD_SECONDS: int = Field(
        default=300, description="Cooldown between taste-map rebuilds."
    )
    RATE_TASTE_ENRICH_SECONDS: int = Field(
        default=600, description="Cooldown between profile-enrichment runs."
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
    LETTERBOX_DETECT_NVIDIA_ACCELERATION: Literal["auto", "off"] = Field(
        default="auto",
        description=(
            "Use benchmarked NVIDIA NVDEC decode before CPU cropdetect when it is faster; "
            "'off' always uses CPU decode."
        ),
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
    LETTERBOX_MKVMERGE: str = Field(default="mkvmerge", description="mkvmerge binary path/name.")
    LETTERBOX_CONVERT: str = Field(
        default="convert", description="ImageMagick 'convert' binary (trim backend only)."
    )
    LETTERBOX_DOVI_TOOL: str = Field(
        default="dovi_tool", description="dovi_tool binary path/name for Dolby Vision RPU handling."
    )
    LETTERBOX_REENCODE_ALLOW_CPU_FALLBACK: bool = Field(
        default=True, description="Allow CPU encoding when no supported GPU encoder is available."
    )
    LETTERBOX_REENCODE_NVIDIA_ACCELERATION: Literal["auto", "off"] = Field(
        default="auto",
        description=(
            "Use NVIDIA NVDEC decode and crop before NVENC encoding when FFmpeg and the "
            "source support a zero-copy CUDA path; 'off' always uses CPU decode/crop."
        ),
    )
    LETTERBOX_REENCODE_STRICT_DOVI: bool = Field(
        default=False,
        description="Fail permanent re-encode plans when Dolby Vision cannot be preserved.",
    )
    LETTERBOX_MOVIE_SAMPLES_MIN: int = Field(
        default=5, description="Movie sampling start (minutes)."
    )
    LETTERBOX_MOVIE_SAMPLES_MAX: int = Field(
        default=60, description="Movie sampling end (minutes)."
    )
    LETTERBOX_MOVIE_SAMPLE_STEP: int = Field(
        default=5, description="Minutes between movie samples."
    )
    LETTERBOX_TV_QUICK_WINDOWS: int = Field(
        default=3, description="Quick-pass sample window count for TV episodes."
    )
    LETTERBOX_TV_THOROUGH_WINDOWS: int = Field(
        default=8, description="Thorough-pass sample window count for TV episodes."
    )
    LETTERBOX_TV_HEAD_SKIP_PCT: int = Field(
        default=12, description="Percent of runtime to skip at the head for TV sampling."
    )
    LETTERBOX_TV_TAIL_SKIP_PCT: int = Field(
        default=12, description="Percent of runtime to skip at the tail for TV sampling."
    )
    LETTERBOX_TV_SEASON_SAMPLE_EPISODES: int = Field(
        default=3, description="Representative episodes sampled per season during TV triage."
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
    LETTERBOX_VARIABLE_GAP_PX: int = Field(
        default=40,
        description="Bar-value gap (px) that separates two distinct aspect-ratio clusters.",
    )
    LETTERBOX_VARIABLE_MIN_FRACTION: float = Field(
        default=0.2,
        description="Min fraction of samples a bar cluster needs to count as a real AR (not an outlier).",
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
    LETTERBOX_FFMPEG_CONCURRENCY: int = Field(
        default=2,
        description=(
            "Max concurrent request-path ffmpeg/ffprobe ops (preview, single detect, "
            "inspect). Bounds disk contention so probes don't dogpile or starve each "
            "other while an encode runs. The encode worker is serialized separately."
        ),
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
        default="show.jpg",
        description="Filename for TV series posters.",
    )
    SEASON_POSTER_FORMAT: str = Field(
        default="season{season:02d}.jpg",
        description="Filename for season posters. {season} = season number. "
        "Example: 'season{season:02d}.jpg' → 'season01.jpg'",
    )

    # ------------------------------------------------------------------
    # Poster Restoration
    # ------------------------------------------------------------------
    POSTER_RESTORE_METHOD: Literal["download", "local"] = Field(
        default="download",
        description="Preferred source when restoring a missing poster. "
        "'download': cache → TMDB re-download → local backup. "
        "'local': local backup → cache → TMDB re-download.",
    )
    POSTER_BACKUP_DIR: str = Field(
        default="data/backups/posters",
        description="Directory for local poster backups (one .jpg per movie, "
        "keyed by movie ID). Written on every deploy; preferred restore "
        "source when POSTER_RESTORE_METHOD='local'.",
    )

    @property
    def poster_backup_path(self) -> Path:
        """Absolute path to the local poster backup directory."""
        path = Path(self.POSTER_BACKUP_DIR)
        if not path.is_absolute():
            path = self._project_root / path
        path.mkdir(parents=True, exist_ok=True)
        return path

    # ------------------------------------------------------------------
    # Pydantic metadata
    # ------------------------------------------------------------------
    model_config = SettingsConfigDict(
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        env_file_override=False,
        extra="ignore",
    )


# Environment/restart-owned singleton. Database-owned values are resolved by
# the versioned configuration provider and never applied by mutating this object.
settings = Settings()
