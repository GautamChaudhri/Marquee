"""Tests for SyncService — movies, series, seasons, episodes."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from PIL import Image
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from marquee.core.poster_sources.tmdb import MovieDetails
from marquee.core.sync_service import (
    SyncService,
    _resolve_poster_path,
)
from marquee.models import (
    Episode,
    EpisodeMediaFile,
    MediaFile,
    Movie,
    Season,
    Series,
)

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
async def test_full_movie_sync_retires_and_reactivates_stable_identity(db: AsyncSession):
    movie = Movie(radarr_id=99, title="Retire Me", year=2020, folder_path="/movies/retire")
    db.add(movie)
    await db.flush()
    media_file = MediaFile(
        source="radarr",
        source_key="radarr:movie-file:99",
        source_file_id=99,
        movie_id=movie.id,
        path="/movies/retire/movie.mkv",
    )
    db.add(media_file)
    await db.commit()

    radarr = AsyncMock()
    radarr.get_movies.return_value = []
    radarr.get_movie_files.return_value = []
    radarr.get_custom_formats.return_value = []
    radarr.get_quality_profiles.return_value = []
    await SyncService(db, radarr=radarr).sync_all()

    await db.refresh(movie)
    await db.refresh(media_file)
    assert (movie.is_present, media_file.is_present, media_file.is_active) == (False, False, False)
    assert movie.retired_at is not None and media_file.retired_at is not None

    radarr.get_movies.return_value = [_radarr_movie(id=99)]
    await SyncService(db, radarr=radarr).sync_all()

    await db.refresh(movie)
    reactivated_file = await db.scalar(
        select(MediaFile).where(MediaFile.movie_id == movie.id, MediaFile.is_present.is_(True))
    )
    assert movie.is_present is True and movie.retired_at is None
    assert reactivated_file is not None and reactivated_file.is_active is True


@pytest.mark.asyncio
async def test_partial_movie_sync_never_retires_absent_rows(db: AsyncSession):
    movie = Movie(radarr_id=99, title="Keep Me", year=2020, folder_path="/movies/keep")
    db.add(movie)
    await db.commit()

    radarr = AsyncMock()
    radarr.get_movies.return_value = [{"id": 1, "title": None}]
    radarr.get_movie_files.return_value = []
    radarr.get_custom_formats.return_value = []
    radarr.get_quality_profiles.return_value = []
    report = await SyncService(db, radarr=radarr).sync_all()

    await db.refresh(movie)
    assert report.movies.errors == 1
    assert movie.is_present is True and movie.retired_at is None


@pytest.mark.asyncio
async def test_sync_movies_enriches_tmdb_metadata(db: AsyncSession):
    radarr = AsyncMock()
    radarr.get_movies.return_value = [_radarr_movie()]
    tmdb = AsyncMock()
    tmdb.get_movie_details.return_value = MovieDetails(
        director="Denis Villeneuve",
        production_companies=["Legendary Pictures", "Warner Bros."],
        tagline="It begins.",
    )

    svc = SyncService(db, radarr=radarr, tmdb=tmdb)
    await svc.sync_all()

    movie = (await db.execute(select(Movie).where(Movie.radarr_id == 1))).scalar_one()
    assert movie.director == "Denis Villeneuve"
    assert movie.production_companies_json == ["Legendary Pictures", "Warner Bros."]
    assert movie.tagline == "It begins."
    tmdb.get_movie_details.assert_awaited_once_with(438631)


@pytest.mark.asyncio
async def test_sync_movies_skips_tmdb_fetch_for_enriched_rows(db: AsyncSession):
    movie = Movie(
        radarr_id=1,
        title="Old Title",
        year=2000,
        folder_path="/old",
        tmdb_id=438631,
        director="Already Set",
        production_companies_json=["Studio"],
        tagline=None,
    )
    db.add(movie)
    await db.flush()

    radarr = AsyncMock()
    radarr.get_movies.return_value = [_radarr_movie()]
    tmdb = AsyncMock()

    svc = SyncService(db, radarr=radarr, tmdb=tmdb)
    await svc.sync_all()

    tmdb.get_movie_details.assert_not_awaited()


@pytest.mark.asyncio
async def test_sync_movies_refreshes_tmdb_metadata_when_tmdb_id_changes(db: AsyncSession):
    movie = Movie(
        radarr_id=1,
        title="Old Title",
        year=2000,
        folder_path="/old",
        tmdb_id=99,
        director="Wrong Director",
        production_companies_json=["Old Studio"],
        tagline="Old tagline",
    )
    db.add(movie)
    await db.flush()

    radarr = AsyncMock()
    radarr.get_movies.return_value = [_radarr_movie(tmdbId=438631)]
    tmdb = AsyncMock()
    tmdb.get_movie_details.return_value = MovieDetails(
        director="Denis Villeneuve",
        production_companies=["Legendary Pictures"],
        tagline="It begins.",
    )

    svc = SyncService(db, radarr=radarr, tmdb=tmdb)
    await svc.sync_all()

    await db.refresh(movie)
    assert movie.tmdb_id == 438631
    assert movie.director == "Denis Villeneuve"
    assert movie.production_companies_json == ["Legendary Pictures"]
    assert movie.tagline == "It begins."
    tmdb.get_movie_details.assert_awaited_once_with(438631)


@pytest.mark.asyncio
async def test_sync_movies_tmdb_enrichment_failure_tolerated(db: AsyncSession):
    radarr = AsyncMock()
    radarr.get_movies.return_value = [_radarr_movie()]
    tmdb = AsyncMock()
    tmdb.get_movie_details.side_effect = RuntimeError("boom")

    svc = SyncService(db, radarr=radarr, tmdb=tmdb)
    report = await svc.sync_all()

    movie = (await db.execute(select(Movie).where(Movie.radarr_id == 1))).scalar_one()
    assert report.movies.errors == 0
    assert movie.director is None
    assert movie.production_companies_json is None
    assert movie.tagline is None
    tmdb.get_movie_details.assert_awaited_once_with(438631)


@pytest.mark.asyncio
async def test_sync_movies_never_overwrites_poster(db: AsyncSession, tmp_path: Path):
    """Poster columns set by the pipeline must survive a sync.

    The pipeline-deployed poster exists on disk at a path that differs from
    the sync-derived expected location — sync must leave it alone.
    """
    deployed = tmp_path / "poster.jpg"
    Image.new("RGB", (16, 24), (10, 20, 30)).save(deployed, format="JPEG")
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
async def test_sync_movies_clears_non_jpeg_poster_path(db: AsyncSession, tmp_path: Path):
    """A file named .jpg is not trusted unless its bytes decode as JPEG."""
    invalid = tmp_path / "poster.jpg"
    invalid.write_bytes(b"not-a-jpeg")
    movie = Movie(
        radarr_id=1,
        title="Dune",
        year=2021,
        folder_path="/movies/Dune",
        poster_path=str(invalid),
        poster_ai_selected=True,
    )
    db.add(movie)
    await db.flush()

    radarr = AsyncMock()
    radarr.get_movies.return_value = [_radarr_movie()]

    await SyncService(db, radarr=radarr).sync_all()

    await db.refresh(movie)
    assert movie.poster_path is None
    assert movie.needs_poster


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
    series = (await db.execute(select(Series).where(Series.sonarr_id == 100))).scalar_one()
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

    series = (await db.execute(select(Series).where(Series.sonarr_id == 100))).scalar_one()
    assert series.title == "Breaking Bad"
    assert series.tvdb_id == 81189
    assert series.season_count == 2

    # Seasons
    seasons = (
        (
            await db.execute(
                select(Season).where(Season.series_id == series.id).order_by(Season.season_number)
            )
        )
        .scalars()
        .all()
    )
    assert len(seasons) == 2
    assert seasons[0].season_number == 1
    assert seasons[1].season_number == 2

    # Episodes
    episodes = (
        (await db.execute(select(Episode).where(Episode.series_id == series.id))).scalars().all()
    )
    assert len(episodes) == 1
    assert episodes[0].title == "Pilot"
    assert episodes[0].episode_file_path == "/tv/Breaking Bad/Season 1/Breaking Bad - S01E01.mkv"


@pytest.mark.asyncio
async def test_sync_series_persists_sonarr_genres_and_rejects_malformed_values(
    db: AsyncSession,
):
    sonarr = AsyncMock()
    sonarr.get_series.return_value = [_sonarr_series(genres=["Crime", "Drama"])]
    sonarr.get_episodes.return_value = []
    sonarr.get_episode_files.return_value = []

    await SyncService(db, sonarr=sonarr).sync_all()

    series = (await db.execute(select(Series).where(Series.sonarr_id == 100))).scalar_one()
    assert series.genres == ["Crime", "Drama"]

    # Sonarr omitting genres, or returning a malformed list, leaves the
    # nullable field empty instead of persisting an invalid API payload.
    sonarr.get_series.return_value = [_sonarr_series()]
    await SyncService(db, sonarr=sonarr).sync_all()
    await db.refresh(series)
    assert series.genres is None

    sonarr.get_series.return_value = [_sonarr_series(genres=["Drama", 42])]
    await SyncService(db, sonarr=sonarr).sync_all()
    await db.refresh(series)
    assert series.genres is None


@pytest.mark.asyncio
async def test_full_series_sync_retires_and_reactivates_descendants(db: AsyncSession):
    sonarr = AsyncMock()
    sonarr.get_series.return_value = [_sonarr_series()]
    sonarr.get_episodes.return_value = [_sonarr_episode()]
    sonarr.get_episode_files.return_value = [_sonarr_episode_file()]
    sonarr.get_custom_formats.return_value = []
    sonarr.get_quality_profiles.return_value = []
    await SyncService(db, sonarr=sonarr).sync_all()

    series = await db.scalar(select(Series).where(Series.sonarr_id == 100))
    assert series is not None
    season = await db.scalar(select(Season).where(Season.series_id == series.id))
    episode = await db.scalar(select(Episode).where(Episode.series_id == series.id))
    media_file = await db.scalar(
        select(MediaFile)
        .join(EpisodeMediaFile, EpisodeMediaFile.media_file_id == MediaFile.id)
        .where(EpisodeMediaFile.episode_id == episode.id)
    )
    assert season is not None and episode is not None and media_file is not None

    sonarr.get_series.return_value = []
    await SyncService(db, sonarr=sonarr).sync_all()
    for row in (series, season, episode, media_file):
        await db.refresh(row)
        assert row.is_present is False and row.retired_at is not None

    sonarr.get_series.return_value = [_sonarr_series()]
    await SyncService(db, sonarr=sonarr).sync_all()
    for row in (series, season, episode, media_file):
        await db.refresh(row)
        assert row.is_present is True and row.retired_at is None


@pytest.mark.asyncio
async def test_sync_series_syncs_season_zero(db: AsyncSession):
    """Season 0 (Specials) should be synced."""
    sonarr = AsyncMock()
    sonarr.get_series.return_value = [
        _sonarr_series(
            seasons=[
                {"seasonNumber": 0, "monitored": False},
                {"seasonNumber": 1, "monitored": True},
            ]
        )
    ]
    sonarr.get_episodes.return_value = []
    sonarr.get_episode_files.return_value = []

    svc = SyncService(db, sonarr=sonarr)
    await svc.sync_all()

    series = (await db.execute(select(Series).where(Series.sonarr_id == 100))).scalar_one()
    seasons = (
        (await db.execute(select(Season).where(Season.series_id == series.id))).scalars().all()
    )
    assert len(seasons) == 2
    assert {s.season_number for s in seasons} == {0, 1}


@pytest.mark.asyncio
async def test_sync_series_handles_missing_tvdb_id(db: AsyncSession):
    """Series without tvdbId should still sync (stored as NULL)."""
    sonarr = AsyncMock()
    sonarr.get_series.return_value = [_sonarr_series(tvdbId=0)]
    sonarr.get_episodes.return_value = []
    sonarr.get_episode_files.return_value = []

    svc = SyncService(db, sonarr=sonarr)
    await svc.sync_all()

    series = (await db.execute(select(Series).where(Series.sonarr_id == 100))).scalar_one()
    assert series.tvdb_id is None


# ---------------------------------------------------------------------------
# Poster path resolution
# ---------------------------------------------------------------------------


def test_resolve_movie_poster_default():
    """Default format: poster.jpg inside movie folder."""
    movie = Movie(id=1, title="Dune", year=2021, folder_path="/movies/Dune (2021)")
    with patch(
        "marquee.core.sync_service.safe_translate_and_validate",
        return_value=Path("/movies/Dune (2021)"),
    ):
        result = _resolve_poster_path(movie)
    assert result == Path("/movies/Dune (2021)/poster.jpg")


def test_resolve_movie_poster_with_basename():
    """{movie_basename} should be replaced with the media file stem."""
    from marquee.config import settings

    movie = Movie(
        id=1,
        title="Dune",
        year=2021,
        folder_path="/movies/Dune (2021)",
        movie_file_path="Dune (2021).mkv",
    )
    with (
        patch(
            "marquee.core.sync_service.safe_translate_and_validate",
            return_value=Path("/movies/Dune (2021)"),
        ),
        patch.object(settings, "MOVIE_POSTER_FORMAT", "{movie_basename}.jpg"),
    ):
        result = _resolve_poster_path(movie)
    assert result == Path("/movies/Dune (2021)/Dune (2021).jpg")


def test_resolve_series_poster():
    """Series poster should use SERIES_POSTER_FORMAT."""
    from marquee.config import settings

    series = Series(id=1, title="Breaking Bad", year=2008, series_path="/tv/Breaking Bad")
    with (
        patch(
            "marquee.core.sync_service.safe_translate_and_validate",
            return_value=Path("/tv/Breaking Bad"),
        ),
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
        patch(
            "marquee.core.sync_service.safe_translate_and_validate",
            return_value=Path("/tv/Breaking Bad"),
        ),
        patch.object(settings, "SEASON_POSTER_FORMAT", "season{season:02d}-poster.jpg"),
    ):
        result = _resolve_poster_path(season, series=series)
    assert result == Path("/tv/Breaking Bad/season03-poster.jpg")


def test_resolve_poster_invalid_path_returns_none():
    """If path validation fails, return None gracefully."""
    movie = Movie(title="Test", year=2024, folder_path="/bad/../escape")
    with patch(
        "marquee.core.sync_service.safe_translate_and_validate",
        side_effect=ValueError("outside allowed roots"),
    ):
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
