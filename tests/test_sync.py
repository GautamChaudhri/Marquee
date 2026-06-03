"""Tests for SyncService — movies, series, seasons, episodes."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from marquee.core.sync_service import SyncService, _resolve_poster_path
from marquee.models import Episode, Movie, Season, Series


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _mock_path_validation():
    """All sync tests use pass-through path validation.

    Real path validation requires MEDIA_ROOTS to include test paths
    like /movies and /tv.  Tests shouldn't depend on the host filesystem.
    """
    with patch(
        "marquee.core.sync_service.safe_translate_and_validate",
        side_effect=lambda p, **_: Path(p),
    ):
        yield


# ---------------------------------------------------------------------------
# Helpers — build mock *arr API responses
# ---------------------------------------------------------------------------


def _radarr_movie(**overrides) -> dict:
    """Minimal Radarr movie dict."""
    return {
        "id": 1,
        "title": "Dune",
        "year": 2021,
        "tmdbId": 438631,
        "imdbId": "tt1160419",
        "path": "/movies/Dune (2021)",
        "qualityProfileId": 3,
        "movieFile": {"relativePath": "Dune (2021).mkv"},
        **overrides,
    }


def _sonarr_series(**overrides) -> dict:
    """Minimal Sonarr series dict."""
    return {
        "id": 100,
        "title": "Breaking Bad",
        "year": 2008,
        "tvdbId": 81189,
        "imdbId": "tt0903747",
        "tmdbId": 1396,
        "path": "/tv/Breaking Bad",
        "qualityProfileId": 2,
        "seasons": [
            {"seasonNumber": 1, "monitored": True},
            {"seasonNumber": 2, "monitored": True},
        ],
        **overrides,
    }


def _sonarr_episode(**overrides) -> dict:
    return {
        "id": 1001,
        "seriesId": 100,
        "seasonNumber": 1,
        "episodeNumber": 1,
        "title": "Pilot",
        "episodeFileId": 5001,
        "hasFile": True,
        **overrides,
    }


def _sonarr_episode_file(**overrides) -> dict:
    return {
        "id": 5001,
        "relativePath": "Season 1/Breaking Bad - S01E01.mkv",
        "path": "/tv/Breaking Bad/Season 1/Breaking Bad - S01E01.mkv",
        **overrides,
    }


# ---------------------------------------------------------------------------
# Movies
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sync_movies_creates_new(db: AsyncSession):
    """A new movie from Radarr should be inserted."""
    radarr = AsyncMock()
    radarr.get_movies.return_value = [_radarr_movie()]

    svc = SyncService(db, radarr=radarr)
    report = await svc.sync_all()

    assert report.movies.created == 1
    assert report.movies.updated == 0
    assert report.movies.errors == 0

    movie = (await db.execute(select(Movie).where(Movie.radarr_id == 1))).scalar_one()
    assert movie.title == "Dune"
    assert movie.tmdb_id == 438631
    assert movie.folder_path == "/movies/Dune (2021)"


@pytest.mark.asyncio
async def test_sync_movies_updates_existing(db: AsyncSession):
    """An existing movie should be updated, not duplicated."""
    movie = Movie(radarr_id=1, title="Old Title", year=2000, folder_path="/old")
    db.add(movie)
    await db.flush()

    radarr = AsyncMock()
    radarr.get_movies.return_value = [_radarr_movie()]

    svc = SyncService(db, radarr=radarr)
    report = await svc.sync_all()

    assert report.movies.updated == 1
    assert report.movies.created == 0

    await db.refresh(movie)
    assert movie.title == "Dune"
    assert movie.year == 2021


@pytest.mark.asyncio
async def test_sync_movies_never_overwrites_poster(db: AsyncSession):
    """Poster columns set by the pipeline must survive a sync."""
    movie = Movie(
        radarr_id=1,
        title="Dune",
        year=2021,
        folder_path="/movies/Dune",
        poster_path="/movies/Dune/poster.jpg",
        poster_ai_selected=True,
        poster_source="tmdb",
    )
    db.add(movie)
    await db.flush()

    radarr = AsyncMock()
    radarr.get_movies.return_value = [_radarr_movie()]

    svc = SyncService(db, radarr=radarr)
    await svc.sync_all()

    await db.refresh(movie)
    assert movie.poster_path == "/movies/Dune/poster.jpg"
    assert movie.poster_ai_selected is True
    assert movie.poster_source == "tmdb"


@pytest.mark.asyncio
async def test_sync_movies_counts_errors(db: AsyncSession):
    """A malformed movie entry should increment errors, not crash."""
    radarr = AsyncMock()
    radarr.get_movies.return_value = [
        _radarr_movie(),
        {"id": 2, "title": None},  # missing required fields
    ]

    svc = SyncService(db, radarr=radarr)
    report = await svc.sync_all()

    assert report.movies.created == 1
    assert report.movies.errors >= 1  # the bad entry


# ---------------------------------------------------------------------------
# Series
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sync_series_creates_new(db: AsyncSession):
    """A new series from Sonarr should be inserted with seasons and episodes."""
    sonarr = AsyncMock()
    sonarr.get_series.return_value = [_sonarr_series()]
    sonarr.get_episodes.return_value = [_sonarr_episode()]
    sonarr.get_episode_files.return_value = [_sonarr_episode_file()]

    svc = SyncService(db, sonarr=sonarr)
    report = await svc.sync_all()

    assert report.series.created == 1

    series = (
        await db.execute(select(Series).where(Series.sonarr_id == 100))
    ).scalar_one()
    assert series.title == "Breaking Bad"
    assert series.tvdb_id == 81189
    assert series.season_count == 2

    # Seasons
    seasons = (
        await db.execute(
            select(Season).where(Season.series_id == series.id).order_by(Season.season_number)
        )
    ).scalars().all()
    assert len(seasons) == 2
    assert seasons[0].season_number == 1
    assert seasons[1].season_number == 2

    # Episodes
    episodes = (
        await db.execute(select(Episode).where(Episode.series_id == series.id))
    ).scalars().all()
    assert len(episodes) == 1
    assert episodes[0].title == "Pilot"
    assert episodes[0].episode_file_path == "/tv/Breaking Bad/Season 1/Breaking Bad - S01E01.mkv"


@pytest.mark.asyncio
async def test_sync_series_skips_season_zero(db: AsyncSession):
    """Season 0 (Specials) should be skipped."""
    sonarr = AsyncMock()
    sonarr.get_series.return_value = [
        _sonarr_series(seasons=[
            {"seasonNumber": 0, "monitored": False},
            {"seasonNumber": 1, "monitored": True},
        ])
    ]
    sonarr.get_episodes.return_value = []
    sonarr.get_episode_files.return_value = []

    svc = SyncService(db, sonarr=sonarr)
    await svc.sync_all()

    series = (
        await db.execute(select(Series).where(Series.sonarr_id == 100))
    ).scalar_one()
    seasons = (
        await db.execute(select(Season).where(Season.series_id == series.id))
    ).scalars().all()
    assert len(seasons) == 1
    assert seasons[0].season_number == 1


@pytest.mark.asyncio
async def test_sync_series_handles_missing_tvdb_id(db: AsyncSession):
    """Series without tvdbId should still sync (stored as NULL)."""
    sonarr = AsyncMock()
    sonarr.get_series.return_value = [_sonarr_series(tvdbId=0)]
    sonarr.get_episodes.return_value = []
    sonarr.get_episode_files.return_value = []

    svc = SyncService(db, sonarr=sonarr)
    await svc.sync_all()

    series = (
        await db.execute(select(Series).where(Series.sonarr_id == 100))
    ).scalar_one()
    assert series.tvdb_id is None


# ---------------------------------------------------------------------------
# Poster path resolution
# ---------------------------------------------------------------------------


def test_resolve_movie_poster_default():
    """Default format: poster.jpg inside movie folder."""
    movie = Movie(
        id=1, title="Dune", year=2021, folder_path="/movies/Dune (2021)"
    )
    with patch("marquee.core.sync_service.safe_translate_and_validate",
               return_value=Path("/movies/Dune (2021)")):
        result = _resolve_poster_path(movie)
    assert result == Path("/movies/Dune (2021)/poster.jpg")


def test_resolve_movie_poster_with_basename():
    """{movie_basename} should be replaced with the media file stem."""
    from marquee.config import settings

    movie = Movie(
        id=1, title="Dune", year=2021,
        folder_path="/movies/Dune (2021)",
        movie_file_path="Dune (2021).mkv",
    )
    with (
        patch("marquee.core.sync_service.safe_translate_and_validate",
              return_value=Path("/movies/Dune (2021)")),
        patch.object(settings, "MOVIE_POSTER_FORMAT", "{movie_basename}.jpg"),
    ):
        result = _resolve_poster_path(movie)
    assert result == Path("/movies/Dune (2021)/Dune (2021).jpg")


def test_resolve_series_poster():
    """Series poster should use SERIES_POSTER_FORMAT."""
    from marquee.config import settings

    series = Series(
        id=1, title="Breaking Bad", year=2008, series_path="/tv/Breaking Bad"
    )
    with (
        patch("marquee.core.sync_service.safe_translate_and_validate",
              return_value=Path("/tv/Breaking Bad")),
        patch.object(settings, "SERIES_POSTER_FORMAT", "poster.jpg"),
    ):
        result = _resolve_poster_path(series)
    assert result == Path("/tv/Breaking Bad/poster.jpg")


def test_resolve_season_poster():
    """Season poster should use SEASON_POSTER_FORMAT with season number."""
    from marquee.config import settings

    series = Series(series_path="/tv/Breaking Bad")
    season = Season(series_id=1, season_number=3)

    with (
        patch("marquee.core.sync_service.safe_translate_and_validate",
              return_value=Path("/tv/Breaking Bad")),
        patch.object(settings, "SEASON_POSTER_FORMAT", "season{season:02d}-poster.jpg"),
    ):
        result = _resolve_poster_path(season, series=series)
    assert result == Path("/tv/Breaking Bad/season03-poster.jpg")


def test_resolve_poster_invalid_path_returns_none():
    """If path validation fails, return None gracefully."""
    movie = Movie(title="Test", year=2024, folder_path="/bad/../escape")
    with patch("marquee.core.sync_service.safe_translate_and_validate",
               side_effect=ValueError("outside allowed roots")):
        result = _resolve_poster_path(movie)
    assert result is None


# ---------------------------------------------------------------------------
# Sync skips when clients are missing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sync_all_skips_missing_clients(db: AsyncSession):
    """If neither Radarr nor Sonarr is configured, sync_all should succeed
    with an empty report."""
    svc = SyncService(db, radarr=None, sonarr=None)
    report = await svc.sync_all()

    assert report.movies.total == 0
    assert report.series.total == 0
    assert report.duration_seconds >= 0
