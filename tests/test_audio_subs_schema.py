"""ORM smoke tests for the audio/subtitles backend schema (plan 08 phase 0)."""

from __future__ import annotations

from sqlalchemy import select

from marquee.models import Episode, Series


async def test_episode_audio_and_subtitle_columns_roundtrip(db):
    series = Series(title="Show", year=2020, series_path="/tv/show")
    db.add(series)
    await db.commit()

    episode = Episode(
        series_id=series.id,
        season_number=1,
        episode_number=1,
        audio_languages_json=["en", "ja"],
        subtitle_languages_json=[],
    )
    db.add(episode)
    await db.commit()

    fetched = (await db.execute(select(Episode).where(Episode.id == episode.id))).scalar_one()
    assert fetched.audio_languages_json == ["en", "ja"]
    assert fetched.subtitle_languages_json == []


async def test_series_preferred_language_override_columns_roundtrip(db):
    series = Series(
        title="Show",
        year=2020,
        series_path="/tv/show",
        preferred_audio_languages_json=["ja"],
        preferred_subtitle_languages_json=["en", "es"],
    )
    db.add(series)
    await db.commit()

    fetched = (await db.execute(select(Series).where(Series.id == series.id))).scalar_one()
    assert fetched.preferred_audio_languages_json == ["ja"]
    assert fetched.preferred_subtitle_languages_json == ["en", "es"]
