"""Webhook dispatch, restoration, self-heal, and system endpoint tests."""

from __future__ import annotations

import asyncio

import pytest
from httpx import ASGITransport, AsyncClient
from PIL import Image
from sqlalchemy import select

from marquee.config import settings
from marquee.core.heal import heal_scan
from marquee.core.poster_service import poster_service
from marquee.main import app
from marquee.models import ArtworkEvent, Movie


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(
        type(settings),
        "poster_cache_path",
        property(lambda self: tmp_path / "cache" / "posters"),
    )
    monkeypatch.setattr(settings, "RADARR_MEDIA_PATH", None)
    monkeypatch.setattr(settings, "SONARR_MEDIA_PATH", None)
    monkeypatch.setattr(settings, "RADARR_PATH_PREFIX", None)
    monkeypatch.setattr(settings, "MEDIA_ROOTS", [])
    monkeypatch.setattr(settings, "WEBHOOK_DRY_RUN", False)
    yield


def _img(path, color=(10, 120, 200)):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (40, 60), color).save(path)
    return path


async def _deployed_movie(db, tmp_path, radarr_id=11, tmdb_id=562) -> tuple[Movie, object]:
    folder = tmp_path / "Die Hard (1988)"
    folder.mkdir(parents=True)
    movie = Movie(
        title="Die Hard",
        year=1988,
        folder_path=str(folder),
        tmdb_id=tmdb_id,
        radarr_id=radarr_id,
    )
    db.add(movie)
    await db.commit()
    await db.refresh(movie)
    await poster_service.deploy(db, movie, _img(tmp_path / "src.jpg"))
    return movie, folder


# ---------------------------------------------------------------------------
# Webhook dispatch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_webhook_test_event(client):
    resp = await client.post("/api/webhooks/radarr", json={"eventType": "Test"})
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


@pytest.mark.asyncio
async def test_webhook_rename_updates_folder(client, db, tmp_path):
    movie, _folder = await _deployed_movie(db, tmp_path)
    resp = await client.post(
        "/api/webhooks/radarr",
        json={
            "eventType": "Rename",
            "movie": {"id": movie.radarr_id, "folderPath": "/new/Die Hard (1988)"},
        },
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "folder_updated"
    await db.refresh(movie)
    assert movie.folder_path == "/new/Die Hard (1988)"


@pytest.mark.asyncio
async def test_webhook_download_new_movie_ignored(client):
    resp = await client.post(
        "/api/webhooks/radarr",
        json={"eventType": "Download", "isUpgrade": False, "movie": {"id": 999}},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "ignored"


@pytest.mark.asyncio
async def test_webhook_upgrade_restores_poster(client, db, tmp_path):
    movie, folder = await _deployed_movie(db, tmp_path)
    # Simulate the upgrade: delete the deployed poster, make a new folder.
    (folder / "poster.jpg").unlink()
    new_folder = tmp_path / "Die Hard (1988) [Bluray]"
    new_folder.mkdir()

    resp = await client.post(
        "/api/webhooks/radarr",
        json={
            "eventType": "Download",
            "isUpgrade": True,
            "movie": {"id": movie.radarr_id, "folderPath": str(new_folder)},
        },
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "restore_scheduled"

    # Background task does the work — give it a moment.
    for _ in range(50):
        await asyncio.sleep(0.02)
        if (new_folder / "poster.jpg").is_file():
            break
    assert (new_folder / "poster.jpg").is_file()


@pytest.mark.asyncio
async def test_webhook_upgrade_noop_when_poster_survives(client, db, tmp_path):
    movie, folder = await _deployed_movie(db, tmp_path)
    # In-place upgrade: poster.jpg still present.
    resp = await client.post(
        "/api/webhooks/radarr",
        json={
            "eventType": "Download",
            "isUpgrade": True,
            "movie": {"id": movie.radarr_id, "folderPath": str(folder)},
        },
    )
    assert resp.status_code == 200
    for _ in range(50):
        await asyncio.sleep(0.02)
        events = (
            (await db.execute(select(ArtworkEvent).where(ArtworkEvent.movie_id == movie.id)))
            .scalars()
            .all()
        )
        if any(e.action == "webhook_noop" for e in events):
            break
    events = (
        (await db.execute(select(ArtworkEvent).where(ArtworkEvent.movie_id == movie.id)))
        .scalars()
        .all()
    )
    assert any(e.action == "webhook_noop" for e in events)


# ---------------------------------------------------------------------------
# Self-heal + system
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_heal_restores_missing_poster(db, tmp_path):
    movie, folder = await _deployed_movie(db, tmp_path)
    (folder / "poster.jpg").unlink()  # poster vanished from disk

    result = await heal_scan()
    assert result["restored"] >= 1
    assert (folder / "poster.jpg").is_file()


@pytest.mark.asyncio
async def test_system_status_and_manual_heal(client, db, tmp_path):
    await _deployed_movie(db, tmp_path)
    status = await client.get("/api/system/status")
    assert status.status_code == 200
    data = status.json()
    assert data["cache"]["posters"] >= 1

    heal = await client.post("/api/system/heal")
    assert heal.status_code == 200
    assert "checked" in heal.json()


@pytest.mark.asyncio
async def test_artwork_events_endpoint(client, db, tmp_path):
    movie, _folder = await _deployed_movie(db, tmp_path)
    resp = await client.get(f"/api/movies/{movie.id}/artwork-events")
    assert resp.status_code == 200
    events = resp.json()["events"]
    assert any(e["action"] == "deploy" for e in events)
