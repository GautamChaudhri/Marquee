"""PosterService deploy + restore tests against real tmp folders."""

from __future__ import annotations

import json

import pytest
from PIL import Image
from sqlalchemy import select

from marquee.config import settings
from marquee.core.path_utils import PathValidationError
from marquee.core.poster_service import cache_paths, poster_service, render_filename
from marquee.models import ArtworkEvent, Movie


def _make_image(path, color=(200, 30, 30)):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (40, 60), color).save(path)
    return path


@pytest.fixture(autouse=True)
def cache_to_tmp(tmp_path, monkeypatch):
    # Point the poster cache at a temp dir (poster_cache_path is a property).
    monkeypatch.setattr(
        type(settings),
        "poster_cache_path",
        property(lambda self: tmp_path / "cache" / "posters"),
    )
    # Disable media-root enforcement so tmp folders validate (dev mode).
    monkeypatch.setattr(settings, "RADARR_MEDIA_PATH", None)
    monkeypatch.setattr(settings, "SONARR_MEDIA_PATH", None)
    monkeypatch.setattr(settings, "RADARR_PATH_PREFIX", None)
    monkeypatch.setattr(settings, "MEDIA_ROOTS", [])
    yield


async def _movie(db, folder, tmdb_id=562) -> Movie:
    movie = Movie(title="Die Hard", year=1988, folder_path=str(folder), tmdb_id=tmdb_id)
    db.add(movie)
    await db.commit()
    await db.refresh(movie)
    return movie


def test_render_filename_default():
    movie = Movie(title="X", year=2000, folder_path="/x", movie_file_path="X (2000).mkv")
    assert render_filename(movie) == "poster.jpg"  # default format


@pytest.mark.asyncio
async def test_deploy_writes_file_cache_meta_db_event(db, tmp_path):
    folder = tmp_path / "Die Hard (1988)"
    folder.mkdir(parents=True)
    movie = await _movie(db, folder)
    source = _make_image(tmp_path / "src.jpg")

    result = await poster_service.deploy(
        db, movie, source, source="feedback", user_approved=True,
        poster_source_url="https://image.tmdb.org/t/p/original/abc.jpg",
    )

    deployed = folder / "poster.jpg"
    assert deployed.is_file()
    assert result.deployed_path == str(deployed)

    # Cache + meta sidecar.
    cache_file, cache_meta = cache_paths(movie.tmdb_id)
    assert cache_file.is_file()
    meta = json.loads(cache_meta.read_text())
    assert meta["deployed_filename"] == "poster.jpg"
    assert meta["sha256"] == result.sha256

    # DB state.
    await db.refresh(movie)
    assert movie.poster_path == str(deployed)
    assert movie.poster_user_approved is True
    assert movie.poster_deployed_filename == "poster.jpg"
    assert movie.poster_sha256 == result.sha256

    # Audit event.
    events = (
        await db.execute(select(ArtworkEvent).where(ArtworkEvent.movie_id == movie.id))
    ).scalars().all()
    assert any(e.action == "deploy" for e in events)


@pytest.mark.asyncio
async def test_deploy_rejects_missing_folder(db, tmp_path):
    movie = await _movie(db, tmp_path / "does-not-exist")
    source = _make_image(tmp_path / "src.jpg")
    with pytest.raises(PathValidationError):
        await poster_service.deploy(db, movie, source)


@pytest.mark.asyncio
async def test_restore_from_cache(db, tmp_path):
    folder = tmp_path / "Die Hard (1988)"
    folder.mkdir(parents=True)
    movie = await _movie(db, folder)
    source = _make_image(tmp_path / "src.jpg")
    await poster_service.deploy(db, movie, source)

    # Simulate the upgrade: the deployed poster is gone, new folder exists.
    (folder / "poster.jpg").unlink()
    new_folder = tmp_path / "Die Hard (1988) [Bluray]"
    new_folder.mkdir(parents=True)

    result = await poster_service.restore(db, movie, new_folder=str(new_folder))
    assert result.restored is True
    assert result.source == "cache"
    assert (new_folder / "poster.jpg").is_file()

    await db.refresh(movie)
    assert movie.poster_path == str(new_folder / "poster.jpg")
    assert movie.folder_path == str(new_folder)


@pytest.mark.asyncio
async def test_restore_download_fallback(db, tmp_path, monkeypatch):
    folder = tmp_path / "Heat (1995)"
    folder.mkdir(parents=True)
    movie = await _movie(db, folder, tmdb_id=949)
    movie.poster_source_url = "https://example.com/poster.jpg"
    movie.poster_deployed_filename = "poster.jpg"
    movie.poster_path = str(folder / "poster.jpg")
    await db.commit()

    # No cache file exists for this tmdb_id → download fallback.
    png_bytes = _make_image(tmp_path / "dl.jpg").read_bytes()

    class _Resp:
        content = png_bytes

        def raise_for_status(self):
            return None

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url):
            return _Resp()

    monkeypatch.setattr("marquee.core.poster_service.httpx.AsyncClient", lambda **k: _Client())

    result = await poster_service.restore(db, movie, source="webhook")
    assert result.restored is True
    assert result.source == "download"
    assert (folder / "poster.jpg").is_file()


@pytest.mark.asyncio
async def test_restore_fails_flags_for_repipeline(db, tmp_path):
    folder = tmp_path / "Alien (1979)"
    folder.mkdir(parents=True)
    movie = await _movie(db, folder, tmdb_id=348)
    movie.poster_path = str(folder / "poster.jpg")
    movie.poster_deployed_filename = "poster.jpg"
    # No cache, no source URL → restore fails.
    await db.commit()

    result = await poster_service.restore(db, movie)
    assert result.restored is False
    await db.refresh(movie)
    assert movie.poster_path is None  # flagged for re-pipeline
