"""Application and infrastructure settings, loaded from the repo-root ``.env``.

This is one of exactly two settings classes. ``Settings`` holds what the app needs to
reach its data directory, database, media library, and outside services;
``PipelineSettings`` (``marquee.config.pipeline``) holds the engine knobs. Both read the
same dotenv file. Every runtime path is derived here from ``DATA_DIR``.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import quote

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


def _resolve_env_file() -> Path | None:
    """The dotenv file both settings classes read.

    ``MARQUEE_ENV_FILE`` names a different file; an empty value means read no file at all.
    The test suite sets it empty so tests never see the owner's real settings.
    """
    override = os.environ.get("MARQUEE_ENV_FILE")
    if override is None:
        return REPO_ROOT / ".env"
    return Path(override) if override else None


ENV_FILE = _resolve_env_file()


class Settings(BaseSettings):
    """Application and infrastructure settings."""

    MARQUEE_ENVIRONMENT: str = "development"
    DEBUG: bool = False
    LOG_LEVEL: str = "INFO"
    # Relative values resolve against the repository root.
    DATA_DIR: Path = Path("data")

    POSTGRES_HOST: str = "127.0.0.1"
    POSTGRES_PORT: int = 5432
    POSTGRES_USER: str = "marquee"
    POSTGRES_PASSWORD: SecretStr | None = None
    POSTGRES_DB: str = "marquee"

    # Comma-separated in the environment.
    MOVIE_ROOTS: Annotated[list[Path], NoDecode] = Field(default_factory=list)
    TV_ROOTS: Annotated[list[Path], NoDecode] = Field(default_factory=list)

    TMDB_READ_ACCESS_TOKEN: SecretStr | None = None
    RADARR_URL: str | None = None
    RADARR_API_KEY: SecretStr | None = None
    SONARR_URL: str | None = None
    SONARR_API_KEY: SecretStr | None = None

    model_config = SettingsConfigDict(
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        env_ignore_empty=True,
        extra="ignore",
    )

    @field_validator("MOVIE_ROOTS", "TV_ROOTS", mode="before")
    @classmethod
    def _split_roots(cls, value: Any) -> Any:
        if isinstance(value, str):
            return [Path(part.strip()) for part in value.split(",") if part.strip()]
        return value

    @field_validator("DATA_DIR", mode="after")
    @classmethod
    def _resolve_data_dir(cls, value: Path) -> Path:
        return value if value.is_absolute() else (REPO_ROOT / value).resolve()

    # Runtime data layout. Read-only; nothing here creates directories.

    @property
    def models_dir(self) -> Path:
        return self.DATA_DIR / "models"

    @property
    def ml_dir(self) -> Path:
        return self.DATA_DIR / "ml"

    @property
    def cache_dir(self) -> Path:
        return self.DATA_DIR / "cache"

    @property
    def embedding_cache_dir(self) -> Path:
        return self.cache_dir / "embeddings"

    @property
    def poster_cache_dir(self) -> Path:
        return self.cache_dir / "posters"

    @property
    def runs_dir(self) -> Path:
        return self.DATA_DIR / "runs"

    @property
    def backups_dir(self) -> Path:
        return self.DATA_DIR / "backups"

    @property
    def text_profiles_path(self) -> Path:
        return self.DATA_DIR / "text_profiles.json"

    # Database URLs. They embed the password: never log or print them.

    def _database_netloc(self) -> str:
        password = self.POSTGRES_PASSWORD.get_secret_value() if self.POSTGRES_PASSWORD else ""
        credentials = quote(self.POSTGRES_USER, safe="")
        if password:
            credentials += ":" + quote(password, safe="")
        return f"{credentials}@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}"

    @property
    def database_url(self) -> str:
        """Async SQLAlchemy URL (asyncpg driver)."""
        return f"postgresql+asyncpg://{self._database_netloc()}/{quote(self.POSTGRES_DB, safe='')}"

    @property
    def database_url_sync(self) -> str:
        """Plain libpq URL, as psycopg.connect() takes it."""
        return f"postgresql://{self._database_netloc()}/{quote(self.POSTGRES_DB, safe='')}"


settings = Settings()
