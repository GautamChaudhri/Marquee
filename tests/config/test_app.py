from __future__ import annotations

from pathlib import Path

import pytest

from marquee.config import app
from marquee.config.app import REPO_ROOT, Settings


def _settings(**values: object) -> Settings:
    return Settings(_env_file=None, **values)


def test_media_roots_split_on_commas(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MOVIE_ROOTS", "/media/movies, /media/more-movies")
    monkeypatch.setenv("TV_ROOTS", "/media/tv")
    settings = Settings(_env_file=None)
    assert [Path("/media/movies"), Path("/media/more-movies")] == settings.MOVIE_ROOTS
    assert [Path("/media/tv")] == settings.TV_ROOTS


def test_relative_data_dir_resolves_against_repo_root() -> None:
    assert (REPO_ROOT / "data").resolve() == _settings(DATA_DIR="data").DATA_DIR


def test_runtime_paths_derive_from_data_dir(tmp_path: Path) -> None:
    settings = _settings(DATA_DIR=tmp_path)
    assert settings.models_dir == tmp_path / "models"
    assert settings.model_manifest_path == tmp_path / "models" / "manifest.json"
    assert settings.ocr_models_dir == tmp_path / "models" / "paddlex"
    assert settings.ml_dir == tmp_path / "ml"
    assert settings.embedding_cache_dir == tmp_path / "cache" / "embeddings"
    assert settings.poster_cache_dir == tmp_path / "cache" / "posters"
    assert settings.runs_dir == tmp_path / "runs"
    assert settings.backups_dir == tmp_path / "backups"
    assert settings.text_profiles_path == tmp_path / "text_profiles.json"


def test_database_urls_quote_credentials() -> None:
    settings = _settings(
        POSTGRES_USER="mar quee",
        POSTGRES_PASSWORD="p@ss:word/1",
        POSTGRES_HOST="db.example",
        POSTGRES_PORT=6543,
        POSTGRES_DB="marquee",
    )
    assert settings.database_url == (
        "postgresql+asyncpg://mar%20quee:p%40ss%3Aword%2F1@db.example:6543/marquee"
    )
    assert settings.database_url_sync.startswith("postgresql://mar%20quee:p%40ss%3Aword%2F1@")


def test_secrets_never_appear_in_repr() -> None:
    settings = _settings(TMDB_READ_ACCESS_TOKEN="tmdb-secret", RADARR_API_KEY="radarr-secret")
    text = repr(settings) + str(settings)
    assert "tmdb-secret" not in text
    assert "radarr-secret" not in text
    assert settings.TMDB_READ_ACCESS_TOKEN.get_secret_value() == "tmdb-secret"


def test_empty_values_count_as_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TMDB_READ_ACCESS_TOKEN", "")
    assert Settings(_env_file=None).TMDB_READ_ACCESS_TOKEN is None


def test_env_file_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("MARQUEE_ENV_FILE", raising=False)
    assert app._resolve_env_file() == REPO_ROOT / ".env"
    monkeypatch.setenv("MARQUEE_ENV_FILE", str(tmp_path / "other.env"))
    assert app._resolve_env_file() == tmp_path / "other.env"
    monkeypatch.setenv("MARQUEE_ENV_FILE", "")
    assert app._resolve_env_file() is None
