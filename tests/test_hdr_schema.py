"""ORM smoke tests for the TV HDR + Sonarr overlay schema (plan 06 phase 0)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from marquee.models import (
    DoviState,
    Episode,
    Movie,
    Series,
    SonarrCustomFormat,
    SonarrOverlayProfilePreference,
    SonarrProfileFormatItem,
    SonarrQualityProfile,
)


@pytest.mark.asyncio
async def test_episode_hdr_columns_roundtrip(db):
    series = Series(title="Show", year=2020, series_path="/tv/show")
    db.add(series)
    await db.commit()

    episode = Episode(
        series_id=series.id,
        season_number=1,
        episode_number=1,
        hdr_type_raw="DV HDR10",
        has_hdr=True,
        has_dv=True,
        video_width=3840,
        video_height=2160,
    )
    db.add(episode)
    await db.commit()

    fetched = (await db.execute(select(Episode).where(Episode.id == episode.id))).scalar_one()
    assert fetched.hdr_type_raw == "DV HDR10"
    assert fetched.video_width == 3840
    assert fetched.video_height == 2160


@pytest.mark.asyncio
async def test_sonarr_overlay_tables_roundtrip(db):
    now = datetime.now(UTC)
    cf = SonarrCustomFormat(
        id=1, name="DV", include_when_renaming=False, specifications_json=None, synced_at=now
    )
    profile = SonarrQualityProfile(
        id=1,
        name="4K HDR",
        upgrade_allowed=True,
        cutoff_format_score=0,
        min_format_score=0,
        synced_at=now,
    )
    db.add_all([cf, profile])
    await db.commit()

    db.add(SonarrProfileFormatItem(profile_id=profile.id, custom_format_id=cf.id, score=100))
    db.add(
        SonarrOverlayProfilePreference(
            profile_id=profile.id,
            meet_target="hdr10",
            exceed_target="dovi_fallback",
            excluded_targets=None,
            updated_at=now,
        )
    )
    await db.commit()

    item = (await db.execute(select(SonarrProfileFormatItem))).scalar_one()
    assert item.score == 100
    pref = (await db.execute(select(SonarrOverlayProfilePreference))).scalar_one()
    assert pref.meet_target == "hdr10"


@pytest.mark.asyncio
async def test_dovi_state_movie_shaped_row_unchanged(db):
    movie = Movie(title="Movie", year=2021, folder_path="/m/1")
    db.add(movie)
    await db.commit()

    state = DoviState(movie_id=movie.id, status="analyzed", dovi_profile=8)
    db.add(state)
    await db.commit()

    fetched = (await db.execute(select(DoviState).where(DoviState.movie_id == movie.id))).scalar_one()
    assert fetched.media_type == "movie"
    assert fetched.episode_id is None


@pytest.mark.asyncio
async def test_dovi_state_episode_shaped_row_inserts(db):
    series = Series(title="Show", year=2020, series_path="/tv/show")
    db.add(series)
    await db.commit()

    episode = Episode(series_id=series.id, season_number=1, episode_number=1)
    db.add(episode)
    await db.commit()

    state = DoviState(
        media_type="episode",
        episode_id=episode.id,
        status="analyzed",
        dovi_profile=7,
        el_type="MEL",
    )
    db.add(state)
    await db.commit()

    fetched = (
        await db.execute(select(DoviState).where(DoviState.episode_id == episode.id))
    ).scalar_one()
    assert fetched.movie_id is None
    assert fetched.el_type == "MEL"


@pytest.mark.asyncio
async def test_dovi_state_subject_check_constraint_rejects_neither(db):
    state = DoviState(media_type="movie", movie_id=None, status="unknown")
    db.add(state)
    with pytest.raises(IntegrityError):
        await db.commit()
    await db.rollback()
