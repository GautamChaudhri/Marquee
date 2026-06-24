"""Tests for the ORM models: Movie, Series, Season, Episode."""

import pytest
from sqlalchemy import inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from marquee.database import _get_engine
from marquee.models import (
    Episode,
    Movie,
    MovieCustomFormatScore,
    RadarrCustomFormat,
    RadarrProfileFormatItem,
    RadarrQualityProfile,
    Season,
    Series,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _table_exists(table_name: str) -> bool:
    """Check if a table was created in the database."""
    engine = _get_engine()
    async with engine.connect() as conn:

        def _sync(conn):
            return inspect(conn).has_table(table_name)

        return await conn.run_sync(_sync)


async def _column_names(table_name: str) -> set[str]:
    """Return the set of column names for a table."""
    engine = _get_engine()
    async with engine.connect() as conn:

        def _sync(conn):
            return {c["name"] for c in inspect(conn).get_columns(table_name)}

        return await conn.run_sync(_sync)


# ---------------------------------------------------------------------------
# Table creation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_all_tables_created(db):
    """After init_db, all four tables should exist."""
    assert await _table_exists("movies")
    assert await _table_exists("series")
    assert await _table_exists("seasons")
    assert await _table_exists("episodes")
    assert await _table_exists("radarr_custom_formats")
    assert await _table_exists("radarr_quality_profiles")
    assert await _table_exists("radarr_profile_format_items")
    assert await _table_exists("movie_custom_format_scores")


@pytest.mark.asyncio
async def test_movie_columns(db):
    """Movie table should have domain columns + poster columns."""
    cols = await _column_names("movies")
    # Domain
    assert "id" in cols
    assert "title" in cols
    assert "year" in cols
    assert "tmdb_id" in cols
    assert "imdb_id" in cols
    assert "folder_path" in cols
    assert "movie_file_path" in cols
    assert "radarr_id" in cols
    assert "quality_profile_id" in cols
    assert "quality_cutoff_met" in cols
    assert "hdr_type_raw" in cols
    assert "has_hdr" in cols
    assert "has_dv" in cols
    # ArtworkMixin
    assert "poster_path" in cols
    assert "poster_source" in cols
    assert "poster_ai_selected" in cols
    assert "poster_embedding" in cols
    # Timestamps
    assert "created_at" in cols
    assert "updated_at" in cols


@pytest.mark.asyncio
async def test_series_columns(db):
    """Series table should have domain + poster columns."""
    cols = await _column_names("series")
    assert "tvdb_id" in cols
    assert "tmdb_id" in cols
    assert "imdb_id" in cols
    assert "series_path" in cols
    assert "sonarr_id" in cols
    assert "season_count" in cols
    assert "poster_path" in cols
    assert "poster_ai_selected" in cols


@pytest.mark.asyncio
async def test_season_columns(db):
    """Season table should have FK + poster columns."""
    cols = await _column_names("seasons")
    assert "series_id" in cols
    assert "season_number" in cols
    assert "tmdb_id" in cols
    assert "poster_path" in cols
    assert "poster_ai_selected" in cols


@pytest.mark.asyncio
async def test_episode_columns(db):
    """Episode table should have FK + HDR columns, NO poster columns."""
    cols = await _column_names("episodes")
    assert "series_id" in cols
    assert "season_number" in cols
    assert "episode_number" in cols
    assert "title" in cols
    assert "episode_file_path" in cols
    assert "sonarr_episode_id" in cols
    assert "has_hdr" in cols
    assert "has_dv" in cols
    # Should NOT have poster columns
    assert "poster_path" not in cols
    assert "poster_ai_selected" not in cols


# ---------------------------------------------------------------------------
# ArtworkMixin
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_needs_poster_defaults_true(db: AsyncSession):
    """A new Movie should report needs_poster=True."""
    movie = Movie(title="Test", year=2024, folder_path="/movies/Test")
    db.add(movie)
    await db.flush()

    assert movie.needs_poster is True
    assert movie.poster_path is None
    assert movie.poster_ai_selected is False


@pytest.mark.asyncio
async def test_needs_poster_false_after_set(db: AsyncSession):
    """Setting poster_path should make needs_poster=False."""
    movie = Movie(title="Test", year=2024, folder_path="/movies/Test")
    movie.poster_path = "/movies/Test/poster.jpg"
    db.add(movie)
    await db.flush()

    assert movie.needs_poster is False


# ---------------------------------------------------------------------------
# Unique constraints
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_season_unique_constraint(db: AsyncSession):
    """Cannot insert two seasons with same series_id + season_number."""
    series = Series(title="Test Show", year=2024, series_path="/tv/Test")
    db.add(series)
    await db.flush()

    s1 = Season(series_id=series.id, season_number=1)
    s2 = Season(series_id=series.id, season_number=1)
    db.add_all([s1, s2])

    with pytest.raises(IntegrityError):
        await db.flush()


# ---------------------------------------------------------------------------
# Foreign keys
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_season_fk_enforced(db: AsyncSession):
    """Season with invalid series_id should fail."""
    season = Season(series_id=99999, season_number=1)
    db.add(season)
    with pytest.raises(IntegrityError):
        await db.flush()


@pytest.mark.asyncio
async def test_episode_fk_enforced(db: AsyncSession):
    """Episode with invalid series_id should fail."""
    episode = Episode(series_id=99999, season_number=1, episode_number=1)
    db.add(episode)
    with pytest.raises(IntegrityError):
        await db.flush()


# ---------------------------------------------------------------------------
# Cascade delete
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cascade_delete_seasons(db: AsyncSession):
    """Deleting a Series should cascade-delete its Seasons."""
    series = Series(title="Test", year=2024, series_path="/tv/Test")
    db.add(series)
    await db.flush()

    season = Season(series_id=series.id, season_number=1)
    db.add(season)
    await db.flush()

    # Verify season exists
    result = await db.execute(
        select(Season).where(Season.series_id == series.id)
    )
    assert result.scalar_one_or_none() is not None

    # Delete series
    await db.delete(series)
    await db.flush()

    # Season should be gone
    result = await db.execute(
        select(Season).where(Season.series_id == series.id)
    )
    assert result.scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_cascade_delete_episodes(db: AsyncSession):
    """Deleting a Series should cascade-delete its Episodes."""
    series = Series(title="Test", year=2024, series_path="/tv/Test")
    db.add(series)
    await db.flush()

    episode = Episode(series_id=series.id, season_number=1, episode_number=1)
    db.add(episode)
    await db.flush()

    await db.delete(series)
    await db.flush()

    result = await db.execute(
        select(Episode).where(Episode.series_id == series.id)
    )
    assert result.scalar_one_or_none() is None


# ---------------------------------------------------------------------------
# Repr
# ---------------------------------------------------------------------------


def test_movie_repr():
    m = Movie(id=1, title="Dune", year=2021, folder_path="/m/dune", tmdb_id=438631)
    r = repr(m)
    assert "Movie" in r
    assert "Dune" in r
    assert "438631" in r


def test_series_repr():
    s = Series(id=1, title="Breaking Bad", year=2008, series_path="/tv/bb", tvdb_id=81189)
    r = repr(s)
    assert "Series" in r
    assert "Breaking Bad" in r


def test_episode_repr():
    e = Episode(id=1, series_id=1, season_number=1, episode_number=5)
    r = repr(e)
    assert "S01E05" in r


# ---------------------------------------------------------------------------
# Partial index verification
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_partial_indexes_exist():
    """Verify the missing-poster partial indexes are created on each table."""
    engine = _get_engine()
    async with engine.connect() as conn:

        def _sync(conn):
            inspector = inspect(conn)
            index_names = {idx["name"] for idx in inspector.get_indexes("movies")}
            return index_names

        movie_indexes = await conn.run_sync(_sync)
    assert "ix_movies_missing_poster" in movie_indexes


@pytest.mark.asyncio
async def test_hdr_dv_nullable():
    """has_hdr and has_dv should be nullable (None = not yet checked)."""
    movie = Movie(title="Test", year=2024, folder_path="/m/test")
    assert movie.has_hdr is None
    assert movie.has_dv is None

    episode = Episode(series_id=1, season_number=1, episode_number=1)
    assert episode.has_hdr is None
    assert episode.has_dv is None


@pytest.mark.asyncio
async def test_radarr_overlay_tables_columns(db):
    """Normalized Radarr overlay tables should expose the synced fields."""
    assert {"id", "name", "include_when_renaming", "specifications_json", "synced_at"} <= (
        await _column_names("radarr_custom_formats")
    )
    assert {"id", "name", "upgrade_allowed", "cutoff_format_score", "min_format_score", "synced_at"} <= (
        await _column_names("radarr_quality_profiles")
    )
    assert {"profile_id", "custom_format_id", "score"} <= (
        await _column_names("radarr_profile_format_items")
    )
    assert {"movie_id", "custom_format_id", "score", "synced_at"} <= (
        await _column_names("movie_custom_format_scores")
    )


def test_radarr_overlay_model_repr_smoke():
    """Model constructors should accept the synced overlay fields."""
    assert RadarrCustomFormat(id=1, name="HDR10+", include_when_renaming=False)
    assert RadarrQualityProfile(id=2, name="UHD", cutoff_format_score=100)
    assert RadarrProfileFormatItem(profile_id=2, custom_format_id=1, score=10)
    assert MovieCustomFormatScore(movie_id=4, custom_format_id=1, score=10)
