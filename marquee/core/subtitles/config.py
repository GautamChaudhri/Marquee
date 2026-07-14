"""SubtitleSettings — validated knobs for subtitle management + generation.

A separate pydantic-settings singleton (like ``PipelineSettings``) so the
subtitle feature's many tunables don't bloat the core ``Settings``. Covers both
the §27 behavior knobs and the §24.1 Subgen generation settings. The Subgen
callback token is secret and must never be echoed in config GET responses.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ENV_FILE = Path(__file__).parent.parent.parent.parent / ".env"


class SubtitleSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ENV_FILE, extra="ignore")

    # ── Feature + concurrency ─────────────────────────────────────────
    SUBTITLE_ENABLED: bool = True
    SUBTITLE_SCAN_CONCURRENCY: int = 2
    SUBTITLE_MUTATION_CONCURRENCY: int = 1
    SUBTITLE_GENERATION_CONCURRENCY: int = 1

    # ── Plan / safety ─────────────────────────────────────────────────
    SUBTITLE_PLAN_TTL_MINUTES: int = 15
    SUBTITLE_FILE_STABILITY_SECONDS: int = 10
    SUBTITLE_IMPORT_DELAY_SECONDS: int = 120
    SUBTITLE_TEMP_SPACE_MARGIN_PERCENT: int = 5
    # block | allow_break
    SUBTITLE_HARDLINK_POLICY: str = "block"
    # none | keep_original
    SUBTITLE_BACKUP_MODE: str = "none"
    # Bandwidth cap for a backup/restore copy that can't be a hardlink/reflink
    # (e.g. genuinely different filesystems) — protects DB/SSH I/O headroom on
    # the same disks. ~80MB/s by default.
    SUBTITLE_BACKUP_COPY_BWLIMIT_KBPS: int = 81920
    # quarantine | delete
    SUBTITLE_EXTERNAL_DELETE_MODE: str = "quarantine"

    # Temporary directory configuration for media mutations (SSD temp space)
    SUBTITLE_MUTATION_USE_TEMP_DIR: bool = False
    SUBTITLE_MUTATION_TEMP_DIR: str | None = None

    # ── Policy defaults ───────────────────────────────────────────────
    # keep | review | remove
    SUBTITLE_UNKNOWN_LANGUAGE_ACTION: str = "keep"
    SUBTITLE_PROTECT_FORCED: bool = True
    SUBTITLE_PROTECT_LAST_FULL_DIALOGUE: bool = True
    SUBTITLE_NORMALIZE_TEXT_UTF8: bool = True
    SUBTITLE_JOB_EVENT_RETENTION_DAYS: int = 30
    # Languages the UI considers "preferred" for missing-coverage flags.
    # Audio/subtitle-specific lists inherit this shared list when unset.
    SUBTITLE_PREFERRED_LANGUAGES: list[str] = ["en"]
    SUBTITLE_PREFERRED_AUDIO_LANGUAGES: list[str] | None = None
    SUBTITLE_PREFERRED_SUBTITLE_LANGUAGES: list[str] | None = None
    SUBTITLE_PREVIEW_MAX_CUES: int = 20
    AUDIO_SUBS_DEEP_SCAN_ENABLED: bool = False
    AUDIO_SUBS_DEEP_SCAN_HOUR: int = 3
    AUDIO_SUBS_DEEP_SCAN_BATCH: int = 200

    # ── Generation (Subgen; external service, not bundled) ────────────
    SUBGEN_URL: str | None = Field(
        default=None, description="Base URL of the external Subgen service; unset = disabled."
    )
    SUBGEN_DEPLOYMENT: str | None = None
    SUBGEN_PROFILE_NAME: str = "Subgen"
    SUBGEN_MODEL_LABEL: str = "unknown"
    # transcribe | translate
    SUBGEN_MODE: str = "transcribe"
    SUBGEN_LOCAL_PATH_PREFIX: str | None = None
    SUBGEN_REMOTE_PATH_PREFIX: str | None = None
    SUBGEN_CALLBACK_TOKEN: str | None = None
    SUBGEN_TIMEOUT_MINUTES: int = 120
    SUBGEN_POLL_SECONDS: int = 30
    SUBGEN_EMBEDDED_PORT: int = 9000
    SUBGEN_WHISPER_MODEL: str = ""
    SUBGEN_TRANSCRIBE_DEVICE: str = "auto"
    SUBGEN_GPU_INDEX: int | None = None
    SUBGEN_COMPUTE_TYPE: str = "auto"
    SUBGEN_CONCURRENT_TRANSCRIPTIONS: int = 1
    SUBGEN_WHISPER_THREADS: int = 0
    SUBGEN_MODEL_PATH: str = "data/subgen/models"
    SUBGEN_NAMING_TYPE: str = "ISO_639_2_B"
    SUBGEN_NAME_INCLUDES_SUBGEN: bool = True
    SUBGEN_NAME_INCLUDES_MODEL: bool = False

    @property
    def subgen_deployment(self) -> str:
        if self.SUBGEN_DEPLOYMENT:
            return self.SUBGEN_DEPLOYMENT
        return "external" if self.SUBGEN_URL else "disabled"

    @property
    def subgen_url(self) -> str | None:
        if self.subgen_deployment == "embedded":
            return f"http://127.0.0.1:{self.SUBGEN_EMBEDDED_PORT}"
        return self.SUBGEN_URL

    @property
    def generation_enabled(self) -> bool:
        if self.subgen_deployment == "embedded":
            return True
        if self.subgen_deployment == "external":
            return bool(self.SUBGEN_URL)
        return False

    @property
    def effective_preferred_audio_languages(self) -> list[str]:
        return self.SUBTITLE_PREFERRED_AUDIO_LANGUAGES or self.SUBTITLE_PREFERRED_LANGUAGES

    @property
    def effective_preferred_subtitle_languages(self) -> list[str]:
        return self.SUBTITLE_PREFERRED_SUBTITLE_LANGUAGES or self.SUBTITLE_PREFERRED_LANGUAGES


subtitle_settings = SubtitleSettings()
