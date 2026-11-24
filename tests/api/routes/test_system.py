"""GET /api/system against the test database."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from httpx import AsyncClient
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession

from marquee import __version__
from marquee.api import system
from marquee.config import settings
from marquee.db import migrate
from marquee.db.models import Artwork, Movie, PosterRun
from marquee.jobs import queue
from marquee.jobs.registry import default_registry
from marquee.ml import model_manifest

NOW = datetime(2026, 11, 24, tzinfo=UTC)
SECRETS = {
    "POSTGRES_PASSWORD": "pg-secret-value-7",
    "TMDB_READ_ACCESS_TOKEN": "tmdb-secret-value-7",
    "RADARR_API_KEY": "radarr-secret-value-7",
}


@pytest.fixture
def headers(api_key: str) -> dict[str, str]:
    return {"X-Api-Key": api_key}


@pytest.fixture
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    data = tmp_path / "data"
    monkeypatch.setattr(settings, "DATA_DIR", data)
    monkeypatch.setattr(settings, "MOVIE_ROOTS", [tmp_path / "movies"])
    monkeypatch.setattr(settings, "DEPLOY_POLICY", "missing")
    monkeypatch.setattr(system, "storage_cache", system.StorageCache())
    for name, value in SECRETS.items():
        monkeypatch.setattr(settings, name, SecretStr(value))
    return data


def _strings(value: Any) -> Iterator[str]:
    if isinstance(value, dict):
        for key, inner in value.items():
            yield str(key)
            yield from _strings(inner)
    elif isinstance(value, list):
        for inner in value:
            yield from _strings(inner)
    else:
        yield str(value)


async def test_the_system_route_needs_the_key(offline_client: AsyncClient) -> None:
    assert (await offline_client.get("/api/system")).status_code == 401
    wrong = await offline_client.get("/api/system", headers={"X-Api-Key": "wrong"})
    assert wrong.status_code == 401


@pytest.mark.db
async def test_the_system_summary(
    client: AsyncClient, db_session: AsyncSession, headers: dict, data_dir: Path
) -> None:
    movie = Movie(
        radarr_id=1,
        title="Heat",
        sort_title="heat",
        year=1995,
        radarr_path="/radarr/Heat (1995)",
        has_file=True,
        is_present=True,
        last_seen_at=NOW,
    )
    wanted = Movie(
        radarr_id=2,
        title="Wanted",
        sort_title="wanted",
        radarr_path="/radarr/Wanted",
        has_file=False,
        is_present=True,
        last_seen_at=NOW,
    )
    db_session.add_all([movie, wanted])
    await db_session.flush()
    db_session.add(
        Artwork(
            movie_id=movie.id,
            artwork_kind="poster",
            file_name="poster.jpg",
            sha256="a" * 64,
            size_bytes=10,
            file_modified_at=NOW,
            origin="library",
            first_seen_at=NOW,
            last_seen_at=NOW,
        )
    )
    db_session.add(
        PosterRun(movie_id=movie.id, state="completed", run_folder="00000001-heat", started_at=NOW)
    )
    await db_session.commit()
    sync, _ = await queue.enqueue(db_session, default_registry()["library_sync"])
    (data_dir / "runs" / "00000001-heat").mkdir(parents=True)
    (data_dir / "runs" / "00000001-heat" / "a.jpg").write_bytes(b"r" * 100)
    (data_dir / "ml").mkdir()
    (data_dir / "ml" / "p.npz").write_bytes(b"m" * 7)
    (data_dir / "models").mkdir()
    (data_dir / "models" / "clip.onnx").write_bytes(b"c" * 3)
    model_manifest.write_manifest(
        data_dir / "models" / "manifest.json",
        [
            model_manifest.describe(
                data_dir, data_dir / "models" / "clip.onnx", item="clip", source="t"
            )
        ],
    )

    answer = await client.get("/api/system", headers=headers)

    assert answer.status_code == 200
    body = answer.json()
    assert (body["version"], body["environment"]) == (__version__, settings.MARQUEE_ENVIRONMENT)
    assert body["schema"] == {
        "current": migrate.head_revision(),
        "head": migrate.head_revision(),
        "at_head": True,
    }
    assert body["settings"] == {
        "deploy_policy": "missing",
        "run_new_movies": settings.RUN_NEW_MOVIES,
        "sync_interval_minutes": settings.SYNC_INTERVAL_MINUTES,
        "movie_roots": [str(data_dir.parent / "movies")],
        "data_dir": str(data_dir),
    }
    assert body["library"] == {
        "movies": 2,
        "movies_with_file": 1,
        "posters": {"library": 1},
        "runs": {"completed": 1},
        "deployments": 0,
        "exemplars": {},
        "preference_events": 0,
    }
    assert body["models"] == {
        "manifest": "found",
        "files": [
            {
                "item": "clip",
                "path": "models/clip.onnx",
                "kind": "file",
                "present": True,
                "size_bytes": 3,
                "expected_size_bytes": 3,
            }
        ],
    }
    assert body["last_sync"]["job_id"] == sync.id and body["last_sync"]["state"] == "queued"
    storage = body["storage"]
    assert (storage["runs"], storage["backups"], storage["cache"], storage["ml"]) == (100, 0, 0, 7)

    # No value in the answer is, or holds, a secret, a database URL, or the API key.
    secrets = [*settings.secret_values()]
    assert len(secrets) == 4
    for text in _strings(body):
        for secret in secrets:
            assert secret not in text
        assert "postgresql" not in text and "psycopg" not in text
