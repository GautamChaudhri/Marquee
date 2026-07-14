from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image
from sqlalchemy.ext.asyncio import AsyncSession

from marquee.config import settings
from marquee.core.jobs.builtin_handlers import poster_backup_all, poster_maintenance
from marquee.models import Job, Movie


def _make_image(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (20, 30), (20, 80, 140)).save(path)
    return path


@pytest.fixture(autouse=True)
def poster_paths_to_tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(
        type(settings),
        "poster_cache_path",
        property(lambda self: tmp_path / "cache" / "posters"),
    )
    monkeypatch.setattr(
        type(settings),
        "poster_backup_path",
        property(lambda self: tmp_path / "backups" / "posters"),
    )
    monkeypatch.setattr(settings, "RADARR_MEDIA_PATH", None)
    monkeypatch.setattr(settings, "SONARR_MEDIA_PATH", None)
    monkeypatch.setattr(settings, "RADARR_PATH_PREFIX", None)
    monkeypatch.setattr(settings, "SONARR_PATH_PREFIX", None)
    monkeypatch.setattr(settings, "MEDIA_ROOTS", [])


@pytest.mark.asyncio
async def test_poster_backup_all_copies_deployed_posters(db: AsyncSession, tmp_path):
    poster = _make_image(tmp_path / "movie" / "poster.jpg")
    movie = Movie(
        title="Alpha",
        year=2020,
        folder_path=str(poster.parent),
        movie_file_path="alpha.mkv",
        tmdb_id=1,
        poster_path=str(poster),
    )
    db.add(movie)
    await db.commit()

    result = await poster_backup_all(Job(id="backup", type="poster_backup_all", request={}))

    await db.refresh(movie)
    assert result["copied"] == 1
    assert (settings.poster_backup_path / f"{movie.id}.jpg").is_file()
    assert movie.poster_local_backup_path == str(settings.poster_backup_path / f"{movie.id}.jpg")


@pytest.mark.asyncio
async def test_poster_maintenance_skips_when_radarr_unconfigured(monkeypatch):
    monkeypatch.setattr(settings, "RADARR_URL", None)
    monkeypatch.setattr(settings, "RADARR_API_KEY", None)
    monkeypatch.setattr(settings, "SONARR_URL", None)
    monkeypatch.setattr(settings, "SONARR_API_KEY", None)

    result = await poster_maintenance(
        Job(id="maintenance", type="poster_maintenance", request={"dry_run": True})
    )

    assert result["skipped"] == "neither radarr nor sonarr configured"
    assert result["dry_run"] is True
