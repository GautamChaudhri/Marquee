"""Tests for SyncService — movies, series, seasons, episodes."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from marquee.core.sync_service import SyncService, _resolve_poster_path
from marquee.models import Episode, LetterboxState, Movie, Season, Series

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
async def test_sync_movies_never_overwrites_poster(db: AsyncSession, tmp_path: Path):
    """Poster columns set by the pipeline must survive a sync.

    The pipeline-deployed poster exists on disk at a path that differs from
    the sync-derived expected location — sync must leave it alone.
    """
    deployed = tmp_path / "poster.jpg"
    deployed.write_bytes(b"poster")
    movie = Movie(
        radarr_id=1,
        title="Dune",
        year=2021,
        folder_path="/movies/Dune",
        poster_path=str(deployed),
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
    assert movie.poster_path == str(deployed)
    assert movie.poster_ai_selected is True
    assert movie.poster_source == "tmdb"


@pytest.mark.asyncio
async def test_sync_movies_populates_hdr_dv(db: AsyncSession):
    """videoDynamicRangeType from Radarr mediaInfo sets has_hdr / has_dv."""
    radarr = AsyncMock()
    radarr.get_movies.return_value = [
        _radarr_movie(
            movieFile={
                "relativePath": "Dune (2021).mkv",
                "mediaInfo": {
                    "width": 3840,
                    "height": 1600,
                    "videoDynamicRangeType": "DV HDR10",
                },
            }
        )
    ]

    svc = SyncService(db, radarr=radarr)
    await svc.sync_all()

    movie = (await db.execute(select(Movie).where(Movie.radarr_id == 1))).scalar_one()
    assert movie.has_dv is True
    assert movie.has_hdr is True
    assert movie.video_width == 3840


@pytest.mark.asyncio
async def test_sync_movies_prefilters_letterbox_candidate(db: AsyncSession):
    radarr = AsyncMock()
    radarr.get_movies.return_value = [
        _radarr_movie(
            movieFile={
                "relativePath": "Dune (2021).mkv",
                "mediaInfo": {"width": 3840, "height": 2160},
            }
        )
    ]

    svc = SyncService(db, radarr=radarr)
    await svc.sync_all()

    movie = (await db.execute(select(Movie).where(Movie.radarr_id == 1))).scalar_one()
    state = (
        await db.execute(select(LetterboxState).where(LetterboxState.movie_id == movie.id))
    ).scalar_one()
    assert state.status == "prefilter_candidate"
    assert state.prefilter_reason == "sixteen_nine_container"


@pytest.mark.asyncio
async def test_sync_movies_prefilters_full_frame_skipped(db: AsyncSession):
    radarr = AsyncMock()
    radarr.get_movies.return_value = [
        _radarr_movie(
            movieFile={
                "relativePath": "Dune (2021).mkv",
                "mediaInfo": {"width": 3840, "height": 1600},
            }
        )
    ]

    svc = SyncService(db, radarr=radarr)
    await svc.sync_all()

    movie = (await db.execute(select(Movie).where(Movie.radarr_id == 1))).scalar_one()
    state = (
        await db.execute(select(LetterboxState).where(LetterboxState.movie_id == movie.id))
    ).scalar_one()
    assert state.status == "prefilter_skipped"
    assert state.prefilter_reason == "native_wide"


@pytest.mark.asyncio
async def test_sync_movies_without_file_stays_out_of_letterbox_workflow(db: AsyncSession):
    radarr = AsyncMock()
    radarr.get_movies.return_value = [_radarr_movie(movieFile=None)]

    svc = SyncService(db, radarr=radarr)
    await svc.sync_all()

    movie = (await db.execute(select(Movie).where(Movie.radarr_id == 1))).scalar_one()
    assert movie.movie_file_path is None
    state = (
        await db.execute(select(LetterboxState).where(LetterboxState.movie_id == movie.id))
    ).scalar_one_or_none()
    assert state is None


@pytest.mark.asyncio
async def test_sync_movies_later_file_creates_prefilter_row(db: AsyncSession):
    radarr = AsyncMock()
    radarr.get_movies.return_value = [_radarr_movie(movieFile=None)]

    svc = SyncService(db, radarr=radarr)
    await svc.sync_all()

    radarr.get_movies.return_value = [
        _radarr_movie(
            movieFile={
                "relativePath": "Dune (2021).mkv",
                "mediaInfo": {"width": 1920, "height": 1080},
            }
        )
    ]
    await svc.sync_all()

    movie = (await db.execute(select(Movie).where(Movie.radarr_id == 1))).scalar_one()
    state = (
        await db.execute(select(LetterboxState).where(LetterboxState.movie_id == movie.id))
    ).scalar_one()
    assert state.status == "prefilter_candidate"


@pytest.mark.asyncio
async def test_sync_movies_prefilter_does_not_overwrite_detector_truth(db: AsyncSession):
    movie = Movie(
        radarr_id=1,
        title="Dune",
        year=2021,
        folder_path="/movies/Dune (2021)",
        movie_file_path="Dune (2021).mkv",
    )
    db.add(movie)
    await db.flush()
    db.add(
        LetterboxState(
            movie_id=movie.id,
            status="not_letterboxed",
            confidence="none",
            reviewed=True,
            last_detected_at=None,
        )
    )
    await db.commit()

    radarr = AsyncMock()
    radarr.get_movies.return_value = [
        _radarr_movie(
            movieFile={
                "relativePath": "Dune (2021).mkv",
                "mediaInfo": {"width": 3840, "height": 2160},
            }
        )
    ]

    svc = SyncService(db, radarr=radarr)
    await svc.sync_all()

    state = (
        await db.execute(select(LetterboxState).where(LetterboxState.movie_id == movie.id))
    ).scalar_one()
    assert state.status == "not_letterboxed"
    assert state.reviewed is True


@pytest.mark.asyncio
async def test_sync_movies_hdr_sdr_vs_unknown(db: AsyncSession):
    """Explicit SDR → False/False; absent dynamic-range info → NULL (unchecked)."""
    radarr = AsyncMock()
    radarr.get_movies.return_value = [
        _radarr_movie(
            title="SDR Film",
            movieFile={"relativePath": "a.mkv", "mediaInfo": {"videoDynamicRange": "SDR"}},
        ),
        _radarr_movie(
            id=2, title="Unknown Film", tmdbId=2,
            movieFile={"relativePath": "b.mkv"},  # no mediaInfo
        ),
    ]

    svc = SyncService(db, radarr=radarr)
    await svc.sync_all()

    sdr = (await db.execute(select(Movie).where(Movie.title == "SDR Film"))).scalar_one()
    assert sdr.has_hdr is False
    assert sdr.has_dv is False

    unknown = (await db.execute(select(Movie).where(Movie.title == "Unknown Film"))).scalar_one()
    assert unknown.has_hdr is None
    assert unknown.has_dv is None


@pytest.mark.asyncio
async def test_sync_movies_clears_stale_poster_path(db: AsyncSession):
    """A recorded poster whose file no longer exists must be NULLed.

    Radarr upgrades delete and recreate the movie folder; if sync keeps the
    stale path, the item stays "complete" forever and is never queued for
    re-selection (NULL poster_path = needs poster).
    """
    movie = Movie(
        radarr_id=1,
        title="Dune",
        year=2021,
        folder_path="/movies/Dune",
        poster_path="/movies/Dune (2021)/poster-that-was-deleted.jpg",
        poster_ai_selected=True,
    )
    db.add(movie)
    await db.flush()

    radarr = AsyncMock()
    radarr.get_movies.return_value = [_radarr_movie()]

    svc = SyncService(db, radarr=radarr)
    await svc.sync_all()

    await db.refresh(movie)
    assert movie.poster_path is None
    assert movie.needs_poster


@pytest.mark.asyncio
async def test_sync_series_missing_title_does_not_poison_commit(db: AsyncSession):
    """A title-less Sonarr entry must be skipped, not abort the whole sync."""
    sonarr = AsyncMock()
    sonarr.get_series.return_value = [
        {"id": 200, "title": None, "path": "/tv/Broken"},
        _sonarr_series(),
    ]
    sonarr.get_episodes.return_value = [_sonarr_episode()]
    sonarr.get_episode_files.return_value = [_sonarr_episode_file()]

    svc = SyncService(db, sonarr=sonarr)
    report = await svc.sync_all()

    assert report.series.errors == 1
    assert report.series.created == 1  # the valid one still lands
    series = (
        await db.execute(select(Series).where(Series.sonarr_id == 100))
    ).scalar_one()
    assert series.title == "Breaking Bad"


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
