"""Scoped subtitle scan job tests for plan 08 phase 3."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from marquee.core.jobs import builtin_handlers
from marquee.core.subtitles.config import subtitle_settings
from marquee.models import (
    Episode,
    EpisodeMediaFile,
    Job,
    MediaFile,
    MediaJob,
    Movie,
    Season,
    Series,
    SubtitleInventory,
)


@pytest.mark.asyncio
async def test_subtitle_scan_all_series_scope_filters_and_queues_stale_or_missing(
    db, monkeypatch
):
    series = Series(title="Show", year=2020, series_path="/tv/show", sonarr_id=1)
    other = Series(title="Other", year=2021, series_path="/tv/other", sonarr_id=2)
    db.add_all([series, other])
    await db.flush()

    db.add_all(
        [
            Season(series_id=series.id, season_number=1, episode_file_count=2),
            Season(series_id=series.id, season_number=2, episode_file_count=1),
            Season(series_id=other.id, season_number=1, episode_file_count=1),
        ]
    )
    await db.flush()

    episode_stale = Episode(series_id=series.id, season_number=1, episode_number=1)
    episode_fresh = Episode(series_id=series.id, season_number=1, episode_number=2)
    episode_other_season = Episode(series_id=series.id, season_number=2, episode_number=1)
    episode_other_series = Episode(series_id=other.id, season_number=1, episode_number=1)
    db.add_all([episode_stale, episode_fresh, episode_other_season, episode_other_series])
    await db.flush()

    media_stale = MediaFile(
        source="sonarr",
        source_key="sonarr:stale",
        path="/tv/show/s01e01.mkv",
        is_active=True,
    )
    media_fresh = MediaFile(
        source="sonarr",
        source_key="sonarr:fresh",
        path="/tv/show/s01e02.mkv",
        is_active=True,
    )
    media_other_season = MediaFile(
        source="sonarr",
        source_key="sonarr:other-season",
        path="/tv/show/s02e01.mkv",
        is_active=True,
    )
    media_other_series = MediaFile(
        source="sonarr",
        source_key="sonarr:other-series",
        path="/tv/other/s01e01.mkv",
        is_active=True,
    )
    db.add_all([media_stale, media_fresh, media_other_season, media_other_series])
    await db.flush()

    db.add_all(
        [
            EpisodeMediaFile(episode_id=episode_stale.id, media_file_id=media_stale.id),
            EpisodeMediaFile(episode_id=episode_fresh.id, media_file_id=media_fresh.id),
            EpisodeMediaFile(episode_id=episode_other_season.id, media_file_id=media_other_season.id),
            EpisodeMediaFile(episode_id=episode_other_series.id, media_file_id=media_other_series.id),
        ]
    )
    db.add_all(
        [
            SubtitleInventory(media_file_id=media_stale.id, file_signature="old"),
            SubtitleInventory(media_file_id=media_fresh.id, file_signature="fresh"),
        ]
    )
    await db.commit()

    async def fake_resolve_media_file(_db, media_file_id):
        signatures = {
            media_stale.id: type("Resolved", (), {"signature": "new"})(),
            media_fresh.id: type("Resolved", (), {"signature": "fresh"})(),
            media_other_season.id: type("Resolved", (), {"signature": "other"})(),
            media_other_series.id: type("Resolved", (), {"signature": "other-series"})(),
        }
        return signatures[media_file_id]

    async def fake_progress(*_args, **_kwargs):
        return None

    monkeypatch.setattr(builtin_handlers, "resolve_media_file", fake_resolve_media_file)
    monkeypatch.setattr(builtin_handlers, "_update_progress", fake_progress)

    job = Job(
        id="scan-series",
        type="subtitle_scan_all",
        payload={"scope": "series", "series_id": series.id, "season_number": 1, "force": False},
        resource_request={},
    )

    result = await builtin_handlers.subtitle_scan_all(job)
    queued = (
        await db.execute(select(MediaJob).where(MediaJob.operation == "subtitle_scan"))
    ).scalars().all()

    assert result["queued_scans"] == 1
    assert [row.media_file_id for row in queued] == [media_stale.id]


@pytest.mark.asyncio
async def test_audio_subs_deep_scan_queues_missing_or_stale_files_up_to_batch_limit(
    db, monkeypatch
):
    monkeypatch.setattr(subtitle_settings, "AUDIO_SUBS_DEEP_SCAN_BATCH", 2)

    movie_a = Movie(title="A", year=2020, folder_path="/m/a")
    movie_b = Movie(title="B", year=2021, folder_path="/m/b")
    movie_c = Movie(title="C", year=2022, folder_path="/m/c")
    db.add_all([movie_a, movie_b, movie_c])
    await db.flush()

    media_missing = MediaFile(
        source="radarr",
        source_key="radarr:missing",
        path="/m/a/a.mkv",
        movie_id=movie_a.id,
        is_active=True,
    )
    media_stale = MediaFile(
        source="radarr",
        source_key="radarr:stale",
        path="/m/b/b.mkv",
        movie_id=movie_b.id,
        is_active=True,
    )
    media_fresh = MediaFile(
        source="radarr",
        source_key="radarr:fresh",
        path="/m/c/c.mkv",
        movie_id=movie_c.id,
        is_active=True,
    )
    db.add_all([media_missing, media_stale, media_fresh])
    await db.flush()

    db.add_all(
        [
            SubtitleInventory(
                media_file_id=media_stale.id,
                file_signature="old",
                scanned_at=datetime.now(UTC) - timedelta(days=10),
            ),
            SubtitleInventory(
                media_file_id=media_fresh.id,
                file_signature="fresh",
                scanned_at=datetime.now(UTC) - timedelta(days=1),
            ),
        ]
    )
    await db.commit()

    async def fake_resolve_media_file(_db, media_file_id):
        signatures = {
            media_missing.id: type("Resolved", (), {"signature": "missing"})(),
            media_stale.id: type("Resolved", (), {"signature": "new"})(),
            media_fresh.id: type("Resolved", (), {"signature": "fresh"})(),
        }
        return signatures[media_file_id]

    async def fake_progress(*_args, **_kwargs):
        return None

    monkeypatch.setattr(builtin_handlers, "resolve_media_file", fake_resolve_media_file)
    monkeypatch.setattr(builtin_handlers, "_update_progress", fake_progress)

    job = Job(
        id="deep-scan",
        type="audio_subs_deep_scan",
        payload={"scheduled": True},
        resource_request={},
    )

    result = await builtin_handlers.audio_subs_deep_scan(job)
    queued = (
        await db.execute(select(MediaJob).where(MediaJob.operation == "subtitle_scan"))
    ).scalars().all()

    assert result == {"queued_scans": 2, "batch_limit": 2}
    assert {row.media_file_id for row in queued} == {media_missing.id, media_stale.id}
