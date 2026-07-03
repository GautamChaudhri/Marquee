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

    # ── Generation (Subgen; external service, not bundled) ────────────
    SUBGEN_URL: str | None = Field(
        default=None, description="Base URL of the external Subgen service; unset = disabled."
    )
    SUBGEN_PROFILE_NAME: str = "Subgen"
    SUBGEN_MODEL_LABEL: str = "unknown"
    # transcribe | translate
    SUBGEN_MODE: str = "transcribe"
    SUBGEN_LOCAL_PATH_PREFIX: str | None = None
    SUBGEN_REMOTE_PATH_PREFIX: str | None = None
    SUBGEN_CALLBACK_TOKEN: str | None = None
    SUBGEN_TIMEOUT_MINUTES: int = 120
    SUBGEN_POLL_SECONDS: int = 30

    @property
    def generation_enabled(self) -> bool:
        return bool(self.SUBGEN_URL)

    @property
    def effective_preferred_audio_languages(self) -> list[str]:
        return self.SUBTITLE_PREFERRED_AUDIO_LANGUAGES or self.SUBTITLE_PREFERRED_LANGUAGES

    @property
    def effective_preferred_subtitle_languages(self) -> list[str]:
        return self.SUBTITLE_PREFERRED_SUBTITLE_LANGUAGES or self.SUBTITLE_PREFERRED_LANGUAGES


def _overrides_path() -> Path:
    return ENV_FILE.parent / "data" / "subtitle_overrides.json"


def load_overrides() -> dict:
    """Persisted UI subtitle overrides, layered on top of env/.env at startup."""
    import json  # noqa: PLC0415

    path = _overrides_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def save_overrides(overrides: dict) -> None:
    """Atomically persist current UI subtitle overrides."""
    import json  # noqa: PLC0415
    import os  # noqa: PLC0415

    path = _overrides_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(overrides, indent=2, default=str), encoding="utf-8")
    os.replace(tmp, path)


try:
    subtitle_settings = SubtitleSettings(**load_overrides())
except Exception:
    subtitle_settings = SubtitleSettings()
