from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from marquee.core.poster_sources.tmdb import TVDetails
from marquee.core.sync_service import SyncService
from marquee.core.tv_queries import season_downloaded, series_visible
from marquee.main import app
from marquee.models import Season, Series


@pytest.fixture
async def client():
    """Async test client for the FastAPI app."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture(autouse=True)
def _mock_path_validation():
    with patch(
        "marquee.core.sync_service.safe_translate_and_validate",
        side_effect=lambda p, **_: Path(p),
    ):
        yield


def _sonarr_series_data(**overrides) -> dict:
    return {
        "id": 101,
        "title": "The Wire",
        "year": 2002,
        "tvdbId": 79126,
        "imdbId": "tt0306414",
        "path": "/tv/The Wire",
        "qualityProfileId": 2,
        "seasons": [
            {
                "seasonNumber": 0,
                "statistics": {"episodeCount": 3, "episodeFileCount": 1},
            },
            {
                "seasonNumber": 1,
                "statistics": {"episodeCount": 13, "episodeFileCount": 13},
            },
            {
                "seasonNumber": 2,
                "statistics": {"episodeCount": 12, "episodeFileCount": 0},
            },
        ],
        **overrides,
    }


@pytest.mark.asyncio
async def test_sync_tv_statistics_and_specials(db: AsyncSession):
    sonarr = AsyncMock()
    sonarr.get_series.return_value = [_sonarr_series_data()]
    sonarr.get_episodes.return_value = []
    sonarr.get_episode_files.return_value = []

    svc = SyncService(db, sonarr=sonarr)
    await svc.sync_all()

    series = (await db.execute(select(Series).where(Series.sonarr_id == 101))).scalar_one()
    assert series.title == "The Wire"

    seasons = (
        (
            await db.execute(
                select(Season).where(Season.series_id == series.id).order_by(Season.season_number)
            )
        )
        .scalars()
        .all()
    )

    # Season 0, 1, 2 should be synced
    assert len(seasons) == 3
    assert seasons[0].season_number == 0
    assert seasons[0].episode_count == 3
    assert seasons[0].episode_file_count == 1

    assert seasons[1].season_number == 1
    assert seasons[1].episode_count == 13
    assert seasons[1].episode_file_count == 13

    assert seasons[2].season_number == 2
    assert seasons[2].episode_count == 12
    assert seasons[2].episode_file_count == 0


@pytest.mark.asyncio
async def test_sync_tv_statistics_fallback(db: AsyncSession):
    # Statistics missing from Sonarr payload (all 0)
    sonarr = AsyncMock()
    series_payload = _sonarr_series_data(
        seasons=[
            {
                "seasonNumber": 1,
                "statistics": {"episodeCount": 0, "episodeFileCount": 0},
            }
        ]
    )
    sonarr.get_series.return_value = [series_payload]

    # Episodes have files
    sonarr.get_episodes.return_value = [
        {
            "id": 1001,
            "seriesId": 101,
            "seasonNumber": 1,
            "episodeNumber": 1,
            "title": "One",
            "episodeFileId": 5001,
            "hasFile": True,
        },
        {
            "id": 1002,
            "seriesId": 101,
            "seasonNumber": 1,
            "episodeNumber": 2,
            "title": "Two",
            "episodeFileId": 5002,
            "hasFile": True,
        },
    ]
    sonarr.get_episode_files.return_value = [
        {"id": 5001, "path": "/tv/The Wire/Season 1/The Wire - S01E01.mkv"},
        {"id": 5002, "path": "/tv/The Wire/Season 1/The Wire - S01E02.mkv"},
    ]

    svc = SyncService(db, sonarr=sonarr)
    await svc.sync_all()

    series = (await db.execute(select(Series).where(Series.sonarr_id == 101))).scalar_one()
    seasons = (
        (await db.execute(select(Season).where(Season.series_id == series.id))).scalars().all()
    )
    assert len(seasons) == 1
    # Derived from episode file check
    assert seasons[0].episode_count == 2
    assert seasons[0].episode_file_count == 2


@pytest.mark.asyncio
async def test_sync_tv_tmdb_id_resolution_and_enrichment(db: AsyncSession):
    sonarr = AsyncMock()
    # No tmdbId in sonarr payload, only tvdbId
    sonarr.get_series.return_value = [
        _sonarr_series_data(tmdbId=None, tvdbId=79126, imdbId=None)
    ]
    sonarr.get_episodes.return_value = []
    sonarr.get_episode_files.return_value = []

    tmdb = AsyncMock()
    tmdb.find_by_external_id.return_value = {
        "tv_results": [{"id": 1396}]  # Resolved tmdb_id
    }
    tmdb.get_tv_details.return_value = TVDetails(
        director="Vince Gilligan",
        production_companies=["AMC", "Sony Pictures"],
        tagline="Remember my name",
    )

    svc = SyncService(db, sonarr=sonarr, tmdb=tmdb)
    await svc.sync_all()

    # Verify ID was resolved
    series = (await db.execute(select(Series).where(Series.sonarr_id == 101))).scalar_one()
    assert series.tmdb_id == 1396
    tmdb.find_by_external_id.assert_called_once_with("79126", "tvdb_id")

    # Verify enrichment columns
    assert series.director == "Vince Gilligan"
    assert series.production_companies_json == ["AMC", "Sony Pictures"]
    assert series.tagline == "Remember my name"

    # Verify metadata reset if tmdb_id changes on subsequent sync and enrichment fails
    tmdb.get_tv_details.side_effect = Exception("TMDB down")
    sonarr.get_series.return_value = [
        _sonarr_series_data(tmdbId=999, tvdbId=79126)
    ]
    await svc.sync_all()
    await db.refresh(series)
    assert series.tmdb_id == 999
    assert series.director is None
    assert series.production_companies_json is None
    assert series.tagline is None


@pytest.mark.asyncio
async def test_tv_queries_eligibility_predicates(db: AsyncSession):
    # Setup visible and invisible series
    visible_series = Series(title="Visible Show", series_path="/tv/visible", tvdb_id=1)
    invisible_series = Series(title="Invisible Show", series_path="/tv/invisible", tvdb_id=2)
    db.add_all([visible_series, invisible_series])
    await db.flush()

    # Season with episode files -> visible
    s1 = Season(series_id=visible_series.id, season_number=1, episode_file_count=5)
    # Season with no episode files -> invisible
    s2 = Season(series_id=invisible_series.id, season_number=1, episode_file_count=0)
    db.add_all([s1, s2])
    await db.flush()

    # Test season_downloaded query
    assert (
        await db.scalar(select(Season.id).where(Season.id == s1.id, season_downloaded()))
    ) is not None
    assert (
        await db.scalar(select(Season.id).where(Season.id == s2.id, season_downloaded()))
    ) is None

    # Test series_visible query
    assert (
        await db.scalar(
            select(Series.id).where(Series.id == visible_series.id, series_visible())
        )
    ) is not None
    assert (
        await db.scalar(
            select(Series.id).where(Series.id == invisible_series.id, series_visible())
        )
    ) is None


@pytest.mark.asyncio
async def test_settings_route_tv_format_validation(client: AsyncClient, monkeypatch, tmp_path):
    monkeypatch.setattr(
        "marquee.config._overrides_path",
        lambda: tmp_path / "settings_overrides.json",
    )

    # Valid update
    resp = await client.put(
        "/api/settings",
        json={
            "posters": {
                "series_poster_format": "show.jpg",
                "season_poster_format": "season{season:02d}.jpg",
            }
        },
    )
    assert resp.status_code == 200

    # Invalid series format (contains placeholder)
    resp = await client.put(
        "/api/settings",
        json={"posters": {"series_poster_format": "show_{season}.jpg"}},
    )
    assert resp.status_code == 400

    # Invalid season format (missing {season})
    resp = await client.put(
        "/api/settings",
        json={"posters": {"season_poster_format": "season_poster.jpg"}},
    )
    assert resp.status_code == 400
