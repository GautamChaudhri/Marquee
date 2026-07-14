from __future__ import annotations

from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from marquee.config import settings
from marquee.main import app
from marquee.models import ArtworkEvent, Season, Series


@pytest_asyncio.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


async def _seed_series(
    db: AsyncSession,
    tmp_path: Path,
    *,
    title: str,
    tmdb_id: int | None,
    sonarr_id: int,
    show_poster: bool = False,
    show_text_profile_id: str | None = None,
    season_text_profile_id: str | None = None,
    seasons: list[dict] | None = None,
) -> tuple[Series, list[Season]]:
    root = tmp_path / title
    root.mkdir(parents=True, exist_ok=True)
    series = Series(
        title=title,
        year=2001,
        series_path=str(root),
        tmdb_id=tmdb_id,
        sonarr_id=sonarr_id,
        tvdb_id=sonarr_id + 1000,
        season_count=len(seasons or []),
        show_text_profile_id=show_text_profile_id,
        season_text_profile_id=season_text_profile_id,
    )
    if show_poster:
        show_file = root / "show.jpg"
        show_file.write_bytes(b"show")
        series.poster_path = str(show_file)
        series.poster_user_approved = True
    db.add(series)
    await db.flush()

    created: list[Season] = []
    for spec in seasons or []:
        season = Season(
            series_id=series.id,
            season_number=spec["number"],
            episode_count=spec.get("episode_count", spec.get("episode_file_count", 0)),
            episode_file_count=spec.get("episode_file_count", 0),
        )
        if spec.get("poster"):
            poster_file = root / f"season{spec['number']:02d}.jpg"
            poster_file.write_bytes(b"season")
            season.poster_path = str(poster_file)
            season.poster_user_approved = True
        db.add(season)
        await db.flush()
        created.append(season)

    await db.commit()
    await db.refresh(series)
    for season in created:
        await db.refresh(season)
    return series, created


@pytest.mark.asyncio
async def test_list_series_filters_visibility_and_computes_rollups(
    db: AsyncSession, client: AsyncClient, tmp_path: Path
):
    partial, _ = await _seed_series(
        db,
        tmp_path,
        title="Partial Show",
        tmdb_id=1,
        sonarr_id=10,
        seasons=[
            {"number": 0, "episode_file_count": 1, "poster": True},
            {"number": 1, "episode_file_count": 8, "poster": False},
        ],
    )
    await _seed_series(
        db,
        tmp_path,
        title="Complete Show",
        tmdb_id=2,
        sonarr_id=11,
        show_poster=True,
        seasons=[{"number": 1, "episode_file_count": 8, "poster": True}],
    )
    await _seed_series(
        db,
        tmp_path,
        title="Invisible Show",
        tmdb_id=3,
        sonarr_id=12,
        seasons=[{"number": 1, "episode_file_count": 0, "poster": False}],
    )

    resp = await client.get("/api/library/series")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 2
    items = {item["title"]: item for item in body["items"]}
    assert set(items) == {"Complete Show", "Partial Show"}
    assert items["Partial Show"]["downloaded_seasons"] == 2
    assert items["Partial Show"]["seasons_with_poster"] == 1
    assert items["Partial Show"]["season_poster_status"] == "partial"
    assert items["Partial Show"]["poster"]["has_poster"] is False
    assert items["Complete Show"]["season_poster_status"] == "complete"
    assert items["Complete Show"]["poster"]["has_poster"] is True
    assert items["Partial Show"]["id"] == partial.id


@pytest.mark.asyncio
async def test_get_series_and_list_seasons_include_downloaded_specials_and_overrides(
    db: AsyncSession, client: AsyncClient, tmp_path: Path
):
    series, seasons = await _seed_series(
        db,
        tmp_path,
        title="Detailed Show",
        tmdb_id=4,
        sonarr_id=20,
        show_text_profile_id="show-textless",
        season_text_profile_id="season-title",
        seasons=[
            {"number": 0, "episode_file_count": 1, "poster": True},
            {"number": 1, "episode_file_count": 8, "poster": False},
            {"number": 2, "episode_file_count": 0, "poster": False},
        ],
    )

    detail = await client.get(f"/api/library/series/{series.id}")
    assert detail.status_code == 200
    data = detail.json()
    assert data["show_text_profile_id"] == "show-textless"
    assert data["season_text_profile_id"] == "season-title"
    assert [season["season_number"] for season in data["seasons"]] == [0, 1]
    assert data["downloaded_seasons"] == 2
    assert data["seasons_with_poster"] == 1
    assert data["season_poster_status"] == "partial"

    downloaded = await client.get(f"/api/library/series/{series.id}/seasons")
    assert downloaded.status_code == 200
    assert [season["season_number"] for season in downloaded.json()["seasons"]] == [0, 1]

    all_seasons = await client.get(
        f"/api/library/series/{series.id}/seasons?downloaded_only=false"
    )
    assert all_seasons.status_code == 200
    assert [season["season_number"] for season in all_seasons.json()["seasons"]] == [0, 1, 2]
    assert seasons[2].episode_file_count == 0


@pytest.mark.asyncio
async def test_series_and_season_poster_file_and_delete_endpoints(
    db: AsyncSession, client: AsyncClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(settings, "MEDIA_ROOTS", [str(tmp_path)])
    series, seasons = await _seed_series(
        db,
        tmp_path,
        title="Poster Show",
        tmdb_id=5,
        sonarr_id=30,
        show_poster=True,
        seasons=[{"number": 1, "episode_file_count": 8, "poster": True}],
    )

    show_file = await client.get(f"/api/library/series/{series.id}/poster")
    assert show_file.status_code == 200
    season_file = await client.get(f"/api/library/seasons/{seasons[0].id}/poster")
    assert season_file.status_code == 200

    delete_show = await client.delete(f"/api/library/series/{series.id}/poster")
    assert delete_show.status_code == 200
    await db.refresh(series)
    assert series.poster_path is None
    show_event = (
        await db.execute(
            select(ArtworkEvent)
            .where(ArtworkEvent.series_id == series.id, ArtworkEvent.media_type == "series")
            .order_by(ArtworkEvent.created_at.desc())
        )
    ).scalar_one()
    assert show_event.action == "deploy_reset"

    delete_season = await client.delete(f"/api/library/seasons/{seasons[0].id}/poster")
    assert delete_season.status_code == 200
    await db.refresh(seasons[0])
    assert seasons[0].poster_path is None
    season_event = (
        await db.execute(
            select(ArtworkEvent)
            .where(
                ArtworkEvent.season_id == seasons[0].id,
                ArtworkEvent.media_type == "season",
            )
            .order_by(ArtworkEvent.created_at.desc())
        )
    ).scalar_one()
    assert season_event.action == "deploy_reset"


@pytest.mark.asyncio
async def test_series_poster_delete_rejects_poisoned_stored_path(
    db: AsyncSession, client: AsyncClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    media_root = tmp_path / "media"
    outside = tmp_path / "outside.jpg"
    outside.write_bytes(b"keep")
    series, _ = await _seed_series(
        db,
        media_root,
        title="Poisoned Poster Show",
        tmdb_id=6,
        sonarr_id=31,
        show_poster=True,
        seasons=[{"number": 1, "episode_file_count": 1}],
    )
    monkeypatch.setattr(settings, "MEDIA_ROOTS", [str(media_root)])
    series.poster_path = str(outside)
    await db.commit()

    response = await client.delete(f"/api/library/series/{series.id}/poster")

    assert response.status_code == 200
    assert response.json()["ok"] is False
    assert outside.read_bytes() == b"keep"
    await db.refresh(series)
    assert series.poster_path == str(outside)
