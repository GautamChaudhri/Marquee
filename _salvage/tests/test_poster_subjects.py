from pathlib import Path

import pytest
from sqlalchemy.exc import IntegrityError

from marquee.config import settings
from marquee.core.path_utils import PathValidationError
from marquee.core.poster_subjects import (
    MEDIA_TYPE_MOVIE,
    MEDIA_TYPE_SEASON,
    MEDIA_TYPE_SERIES,
    PosterSubject,
)
from marquee.models import Movie, Season, Series
from tests.support.canonical_poster import seed_canonical_pipeline_run


@pytest.mark.asyncio
async def test_poster_subject_creation_and_properties():
    # 1. Movie Subject
    movie = Movie(
        id=10,
        title="Inception",
        tmdb_id=27205,
        folder_path="/movies/Inception (2010)",
        movie_file_path="/movies/Inception (2010)/Inception.mkv",
    )
    subj_movie = PosterSubject.from_movie(movie)
    assert subj_movie.media_type == MEDIA_TYPE_MOVIE
    assert subj_movie.entity == movie
    assert subj_movie.id == 10
    assert subj_movie.title == "Inception"
    assert subj_movie.tmdb_id == 27205
    assert subj_movie.folder_raw == "/movies/Inception (2010)"
    assert subj_movie.path_source == "radarr"

    # 2. Series Subject
    series = Series(id=20, title="Breaking Bad", tmdb_id=1396, series_path="/tv/Breaking Bad")
    subj_series = PosterSubject.from_series(series)
    assert subj_series.media_type == MEDIA_TYPE_SERIES
    assert subj_series.entity == series
    assert subj_series.id == 20
    assert subj_series.title == "Breaking Bad"
    assert subj_series.tmdb_id == 1396
    assert subj_series.folder_raw == "/tv/Breaking Bad"
    assert subj_series.path_source == "sonarr"

    # 3. Season Subject
    season = Season(id=30, series_id=20, season_number=1, tmdb_id=3572)
    subj_season = PosterSubject.from_season(season, series)
    assert subj_season.media_type == MEDIA_TYPE_SEASON
    assert subj_season.entity == season
    assert subj_season.id == 30
    assert subj_season.title == "Breaking Bad - Season 01"
    assert subj_season.tmdb_id == 1396  # from series
    assert subj_season.folder_raw == "/tv/Breaking Bad"
    assert subj_season.path_source == "sonarr"

    # Special season 0 (specials)
    season_0 = Season(id=31, series_id=20, season_number=0, tmdb_id=3571)
    subj_season_0 = PosterSubject.from_season(season_0, series)
    assert subj_season_0.title == "Breaking Bad - Season 00"

    # Error when season created without series
    with pytest.raises(ValueError):
        PosterSubject.from_season(season, None)


@pytest.mark.asyncio
async def test_poster_subject_filename_rendering():
    # Movie rendering with {movie_basename}
    movie = Movie(title="Inception", movie_file_path="/movies/Inception/inception-1080p.mkv")
    subj_movie = PosterSubject.from_movie(movie)

    original_movie_format = settings.MOVIE_POSTER_FORMAT
    try:
        # Default should render correctly
        settings.MOVIE_POSTER_FORMAT = "{movie_basename}-poster.jpg"
        assert subj_movie.render_filename() == "inception-1080p-poster.jpg"

        # Safe fallback stem when movie_file_path is None
        movie_no_file = Movie(title="Inception", movie_file_path=None)
        assert PosterSubject.from_movie(movie_no_file).render_filename() == "poster-poster.jpg"
    finally:
        settings.MOVIE_POSTER_FORMAT = original_movie_format

    # Series rendering
    series = Series(title="Breaking Bad")
    subj_series = PosterSubject.from_series(series)
    assert subj_series.render_filename() == settings.SERIES_POSTER_FORMAT

    # Season rendering
    season = Season(season_number=3)
    subj_season = PosterSubject.from_season(season, series)
    # Check that {season} is formatted
    original_season_format = settings.SEASON_POSTER_FORMAT
    try:
        settings.SEASON_POSTER_FORMAT = "season{season:02d}.jpg"
        assert subj_season.render_filename() == "season03.jpg"

        # Invalid format string throws PathValidationError
        settings.SEASON_POSTER_FORMAT = "season{invalid_var}.jpg"
        with pytest.raises(PathValidationError):
            subj_season.render_filename()
    finally:
        settings.SEASON_POSTER_FORMAT = original_season_format


@pytest.mark.asyncio
async def test_poster_subject_cache_and_backup_paths():
    # Cache and backup paths shapes
    movie = Movie(id=1, tmdb_id=101)
    subj_movie = PosterSubject.from_movie(movie)
    assert subj_movie.cache_paths() == (
        settings.poster_cache_path / "movies" / "101.jpg",
        settings.poster_cache_path / "movies" / "101.meta.json",
    )
    assert subj_movie.backup_file() == settings.poster_backup_path / "1.jpg"

    # Local backup override
    movie.poster_local_backup_path = "/backups/my_movie.jpg"
    assert subj_movie.backup_file() == Path("/backups/my_movie.jpg")

    # Series
    series = Series(id=2, tmdb_id=202)
    subj_series = PosterSubject.from_series(series)
    assert subj_series.cache_paths() == (
        settings.poster_cache_path / "tv" / "202.jpg",
        settings.poster_cache_path / "tv" / "202.meta.json",
    )
    assert subj_series.backup_file() == settings.poster_backup_path / "series-2.jpg"

    series.poster_local_backup_path = "/backups/my_series.jpg"
    assert subj_series.backup_file() == Path("/backups/my_series.jpg")

    # Season
    season = Season(id=3, season_number=4)
    subj_season = PosterSubject.from_season(season, series)
    assert subj_season.cache_paths() == (
        settings.poster_cache_path / "tv" / "202-s04.jpg",
        settings.poster_cache_path / "tv" / "202-s04.meta.json",
    )
    assert subj_season.backup_file() == settings.poster_backup_path / "season-3.jpg"

    season.poster_local_backup_path = "/backups/my_season.jpg"
    assert subj_season.backup_file() == Path("/backups/my_season.jpg")


@pytest.mark.asyncio
async def test_poster_subject_fk_kwargs():
    movie = Movie(id=5)
    series = Series(id=6)
    season = Season(id=7)

    assert PosterSubject.from_movie(movie).event_fk_kwargs() == {
        "media_type": "movie",
        "movie_id": 5,
        "series_id": None,
        "season_id": None,
    }

    assert PosterSubject.from_series(series).event_fk_kwargs() == {
        "media_type": "series",
        "movie_id": None,
        "series_id": 6,
        "season_id": None,
    }

    assert PosterSubject.from_season(season, series).event_fk_kwargs() == {
        "media_type": "season",
        "movie_id": None,
        "series_id": 6,
        "season_id": 7,
    }


@pytest.mark.asyncio
async def test_orm_smoke_and_constraints(db):
    # Setup rows
    movie = Movie(
        title="Test Movie", folder_path="/movies/test", movie_file_path="/movies/test/test.mkv"
    )
    series = Series(title="Test Series", series_path="/tv/test")
    db.add_all([movie, series])
    await db.flush()

    season = Season(series_id=series.id, season_number=1)
    db.add(season)
    await db.flush()

    await seed_canonical_pipeline_run(
        db,
        run_id="runmovie000000000000000000000001",
        movie_id=movie.id,
        archive={"run_id": "runmovie000000000000000000000001", "candidates": []},
    )
    await seed_canonical_pipeline_run(
        db,
        run_id="runseries00000000000000000000001",
        archive={"run_id": "runseries00000000000000000000001", "candidates": []},
        media_type="series",
        series_id=series.id,
    )
    await seed_canonical_pipeline_run(
        db,
        run_id="runseason00000000000000000000001",
        archive={"run_id": "runseason00000000000000000000001", "candidates": []},
        media_type="season",
        series_id=series.id,
        season_id=season.id,
    )
    await db.flush()

    # Verify they were saved and check constraint works
    # Check constraint: movie run without movie_id should raise IntegrityError
    invalid_run = await seed_canonical_pipeline_run(
        db,
        run_id="runinvalid0000000000000000000001",
        archive={"run_id": "runinvalid0000000000000000000001", "candidates": []},
        movie_id=movie.id,
    )
    invalid_run.movie_id = None
    invalid_run.series_id = series.id
    with pytest.raises(IntegrityError):
        await db.flush()
    await db.rollback()
