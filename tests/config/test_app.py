from __future__ import annotations

from pathlib import Path, PurePosixPath

import pytest
from pydantic import ValidationError

from marquee.config import app
from marquee.config.app import REPO_ROOT, Settings


def _settings(**values: object) -> Settings:
    return Settings(_env_file=None, **values)


def test_media_roots_split_on_commas(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MOVIE_ROOTS", "/media/movies, /media/more-movies")
    settings = Settings(_env_file=None)
    assert [Path("/media/movies"), Path("/media/more-movies")] == settings.MOVIE_ROOTS


def test_stale_tv_and_sonarr_lines_are_ignored(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "MOVIE_ROOTS=/media/movies\n"
        "TV_ROOTS=/media/tv\n"
        "SONARR_URL=http://sonarr.example:8989\n"
        "SONARR_API_KEY=sonarr-secret\n",
        encoding="utf-8",
    )
    settings = Settings(_env_file=env_file)
    assert [Path("/media/movies")] == settings.MOVIE_ROOTS
    for name in ("TV_ROOTS", "SONARR_URL", "SONARR_API_KEY"):
        assert not hasattr(settings, name)


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
    assert settings.exemplars_dir == tmp_path / "ml" / "exemplars"
    assert settings.profiles_dir == tmp_path / "ml" / "profiles"
    assert settings.taste_build_dir == tmp_path / "cache" / "taste-build"


def test_database_urls_quote_credentials() -> None:
    settings = _settings(
        POSTGRES_USER="mar quee",
        POSTGRES_PASSWORD="p@ss:word/1",
        POSTGRES_HOST="db.example",
        POSTGRES_PORT=6543,
        POSTGRES_DB="marquee",
    )
    assert settings.database_url == (
        "postgresql+psycopg://mar%20quee:p%40ss%3Aword%2F1@db.example:6543/marquee"
    )
    assert settings.database_url_sync.startswith("postgresql://mar%20quee:p%40ss%3Aword%2F1@")


def test_secrets_never_appear_in_repr() -> None:
    settings = _settings(
        TMDB_READ_ACCESS_TOKEN="tmdb-secret",
        RADARR_API_KEY="radarr-secret",
        API_KEY="api-secret",
        POSTGRES_PASSWORD="db-secret",
    )
    text = repr(settings) + str(settings)
    assert "tmdb-secret" not in text
    assert "radarr-secret" not in text
    assert "api-secret" not in text
    assert "db-secret" not in text
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


def test_radarr_movie_roots_split_on_commas_and_pair_by_position(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MOVIE_ROOTS", "/media/movies, /media/more")
    monkeypatch.setenv("RADARR_MOVIE_ROOTS", "/data/movies , /data/more")
    settings = Settings(_env_file=None)
    assert [
        PurePosixPath("/data/movies"),
        PurePosixPath("/data/more"),
    ] == settings.RADARR_MOVIE_ROOTS


def test_radarr_movie_roots_default_to_unset() -> None:
    assert _settings(MOVIE_ROOTS="/media/movies").RADARR_MOVIE_ROOTS == []


def test_radarr_movie_roots_must_match_movie_roots_in_number(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MOVIE_ROOTS", "/media/movies")
    monkeypatch.setenv("RADARR_MOVIE_ROOTS", "/data/movies,/data/more")
    with pytest.raises(ValidationError) as caught:
        Settings(_env_file=None)
    message = str(caught.value)
    assert "RADARR_MOVIE_ROOTS" in message
    assert "MOVIE_ROOTS has 1" in message


def test_radarr_movie_roots_must_be_absolute(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MOVIE_ROOTS", "/media/movies")
    monkeypatch.setenv("RADARR_MOVIE_ROOTS", "movies")
    with pytest.raises(ValidationError, match="not an absolute path"):
        Settings(_env_file=None)


def test_api_settings_defaults() -> None:
    settings = _settings()
    assert settings.API_KEY is None
    assert settings.API_HOST == "127.0.0.1"
    assert settings.API_PORT == 8000


def test_job_settings_defaults_and_bounds() -> None:
    assert _settings().SYNC_INTERVAL_MINUTES == 360
    assert _settings(SYNC_INTERVAL_MINUTES=0).SYNC_INTERVAL_MINUTES == 0
    with pytest.raises(ValidationError):
        _settings(SYNC_INTERVAL_MINUTES=-1)
    assert _settings().GPU_IDLE_SECONDS == 300
    with pytest.raises(ValidationError):
        _settings(GPU_IDLE_SECONDS=-1)
    assert _settings().GPU_CHILD_MAX_RSS_MB == 4096
    assert _settings(GPU_CHILD_MAX_RSS_MB=0).GPU_CHILD_MAX_RSS_MB == 0
    with pytest.raises(ValidationError):
        _settings(GPU_CHILD_MAX_RSS_MB=-1)


def test_secret_values_lists_every_secret_that_is_set() -> None:
    settings = _settings(
        POSTGRES_PASSWORD="db-secret",
        TMDB_READ_ACCESS_TOKEN="tmdb-secret",
        RADARR_API_KEY="radarr-secret",
        API_KEY="api-secret",
    )
    assert sorted(settings.secret_values()) == [
        "api-secret",
        "db-secret",
        "radarr-secret",
        "tmdb-secret",
    ]
    assert _settings().secret_values() == []


def test_production_and_the_role(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MARQUEE_ENVIRONMENT", raising=False)
    monkeypatch.delenv("MARQUEE_ROLE", raising=False)
    assert (_settings().production, _settings().MARQUEE_ROLE) == (False, "all")
    assert _settings(MARQUEE_ENVIRONMENT="production").production is True
    assert _settings(MARQUEE_ROLE="worker").MARQUEE_ROLE == "worker"
    with pytest.raises(ValidationError):
        _settings(MARQUEE_ROLE="web")


def test_the_cuda_kernel_cache_is_where_the_driver_keeps_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("CUDA_CACHE_PATH", raising=False)
    monkeypatch.delenv("CUDA_CACHE_MAXSIZE", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    default = _settings()
    assert default.cuda_kernel_cache_dir == tmp_path / ".nv" / "ComputeCache"
    assert default.cuda_kernel_cache_limit == 1024**3
    monkeypatch.setenv("CUDA_CACHE_PATH", "/data/cache/cuda-jit")
    monkeypatch.setenv("CUDA_CACHE_MAXSIZE", str(2 * 1024**3))
    chosen = _settings()
    assert chosen.cuda_kernel_cache_dir == Path("/data/cache/cuda-jit")
    assert chosen.cuda_kernel_cache_limit == 2 * 1024**3
