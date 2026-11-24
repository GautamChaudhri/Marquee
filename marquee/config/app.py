"""Application and infrastructure settings, loaded from the repo-root ``.env``.

This is one of exactly two settings classes. ``Settings`` holds what the app needs to
reach its data directory, database, media library, and outside services;
``PipelineSettings`` (``marquee.config.pipeline``) holds the engine knobs. Both read the
same dotenv file. Every runtime path is derived here from ``DATA_DIR``.
"""

from __future__ import annotations

import os
from pathlib import Path, PurePosixPath
from typing import Annotated, Any, Self
from urllib.parse import quote

from pydantic import Field, SecretStr, field_validator, model_validator
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
    # The folders in MOVIE_ROOTS as Radarr sees them, in the same order, comma-separated.
    # Unset means Radarr sees the library at the same paths Marquee does. Only
    # marquee.fs.paths uses it, to turn a path from Radarr into a local one.
    RADARR_MOVIE_ROOTS: Annotated[list[PurePosixPath], NoDecode] = Field(default_factory=list)

    TMDB_READ_ACCESS_TOKEN: SecretStr | None = None
    RADARR_URL: str | None = None
    RADARR_API_KEY: SecretStr | None = None

    # Every /api request must present this key. Unset, the API answers 503.
    API_KEY: SecretStr | None = None
    API_HOST: str = "127.0.0.1"
    API_PORT: int = 8000

    model_config = SettingsConfigDict(
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        env_ignore_empty=True,
        extra="ignore",
    )

    @field_validator("MOVIE_ROOTS", mode="before")
    @classmethod
    def _split_roots(cls, value: Any) -> Any:
        if isinstance(value, str):
            return [Path(part.strip()) for part in value.split(",") if part.strip()]
        return value

    @field_validator("RADARR_MOVIE_ROOTS", mode="before")
    @classmethod
    def _split_radarr_roots(cls, value: Any) -> Any:
        if isinstance(value, str):
            return [PurePosixPath(part.strip()) for part in value.split(",") if part.strip()]
        return value

    @field_validator("RADARR_MOVIE_ROOTS", mode="after")
    @classmethod
    def _radarr_roots_are_absolute(cls, value: list[PurePosixPath]) -> list[PurePosixPath]:
        for root in value:
            if not root.is_absolute() or "\0" in str(root):
                raise ValueError(f"RADARR_MOVIE_ROOTS entry {str(root)!r} is not an absolute path")
        return value

    @model_validator(mode="after")
    def _radarr_roots_pair_with_movie_roots(self) -> Self:
        if self.RADARR_MOVIE_ROOTS and len(self.RADARR_MOVIE_ROOTS) != len(self.MOVIE_ROOTS):
            raise ValueError(
                f"RADARR_MOVIE_ROOTS has {len(self.RADARR_MOVIE_ROOTS)} entries and MOVIE_ROOTS "
                f"has {len(self.MOVIE_ROOTS)}; they pair by position, so set one Radarr folder "
                "for each movie root, in the same order, or leave RADARR_MOVIE_ROOTS unset"
            )
        return self

    @field_validator("DATA_DIR", mode="after")
    @classmethod
    def _resolve_data_dir(cls, value: Path) -> Path:
        return value if value.is_absolute() else (REPO_ROOT / value).resolve()

    # Runtime data layout. Read-only; nothing here creates directories.

    @property
    def models_dir(self) -> Path:
        return self.DATA_DIR / "models"

    @property
    def model_manifest_path(self) -> Path:
        return self.models_dir / "manifest.json"

    @property
    def ocr_models_dir(self) -> Path:
        """PaddleX's cache folder; its ``official_models/`` holds the OCR weights."""
        return self.models_dir / "paddlex"

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
        """SQLAlchemy URL on psycopg 3: async with create_async_engine, sync for Alembic."""
        return f"postgresql+psycopg://{self._database_netloc()}/{quote(self.POSTGRES_DB, safe='')}"

    @property
    def database_url_sync(self) -> str:
        """Plain libpq URL, as psycopg.connect() takes it."""
        return f"postgresql://{self._database_netloc()}/{quote(self.POSTGRES_DB, safe='')}"


settings = Settings()
