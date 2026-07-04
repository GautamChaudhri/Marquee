"""Sync tests for plan 08 phase 1: Sonarr episode audio/subtitle capture."""

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from marquee.core.sync_service import SyncService
from marquee.models import Episode, Series


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
            {"seasonNumber": 1, "statistics": {"episodeCount": 3, "episodeFileCount": 3}},
        ],
        **overrides,
    }


def _episode(ep_id: int, ep_num: int, file_id: int | None) -> dict:
    return {
        "id": ep_id,
        "seriesId": 101,
        "seasonNumber": 1,
        "episodeNumber": ep_num,
        "title": f"Episode {ep_num}",
        "episodeFileId": file_id,
        "hasFile": file_id is not None,
    }


@pytest.mark.asyncio
async def test_episode_audio_and_subtitle_lists_capture_and_fan_out(db: AsyncSession):
    sonarr = AsyncMock()
    sonarr.get_series.return_value = [_sonarr_series_data()]
    sonarr.get_episodes.return_value = [
        _episode(1, 1, 5001),
        _episode(2, 2, 5002),
        _episode(3, 3, 5002),
        _episode(4, 4, 5003),
    ]
    sonarr.get_episode_files.return_value = [
        {
            "id": 5001,
            "path": "/tv/w/s01e01.mkv",
            "mediaInfo": {
                "audioLanguages": "eng / jpn / eng",
                "subtitles": "eng/jpn / English",
            },
        },
        {
            "id": 5002,
            "path": "/tv/w/s01e02-e03.mkv",
            "mediaInfo": {"audioLanguages": "spa", "subtitles": ""},
        },
        {"id": 5003, "path": "/tv/w/s01e04.mkv", "mediaInfo": {}},
    ]

    svc = SyncService(db, sonarr=sonarr)
    await svc.sync_all()

    series = (await db.execute(select(Series).where(Series.sonarr_id == 101))).scalar_one()
    episodes = {
        e.episode_number: e
        for e in (
            await db.execute(select(Episode).where(Episode.series_id == series.id))
        ).scalars()
    }

    assert episodes[1].audio_languages_json == ["en", "ja"]
    assert episodes[1].subtitle_languages_json == ["en", "ja"]

    for ep_num in (2, 3):
        assert episodes[ep_num].audio_languages_json == ["es"]
        assert episodes[ep_num].subtitle_languages_json == []

    assert episodes[4].audio_languages_json == []
    assert episodes[4].subtitle_languages_json == []


@pytest.mark.asyncio
async def test_episode_audio_and_subtitle_columns_clear_when_file_removed(db: AsyncSession):
    sonarr = AsyncMock()
    sonarr.get_series.return_value = [_sonarr_series_data()]
    sonarr.get_episodes.return_value = [_episode(1, 1, 5001)]
    sonarr.get_episode_files.return_value = [
        {
            "id": 5001,
            "path": "/tv/w/s01e01.mkv",
            "mediaInfo": {"audioLanguages": "eng", "subtitles": "eng / spa"},
        },
    ]

    svc = SyncService(db, sonarr=sonarr)
    await svc.sync_all()

    series = (await db.execute(select(Series).where(Series.sonarr_id == 101))).scalar_one()
    episode = (
        await db.execute(select(Episode).where(Episode.series_id == series.id))
    ).scalar_one()
    assert episode.audio_languages_json == ["en"]
    assert episode.subtitle_languages_json == ["en", "es"]

    sonarr.get_episodes.return_value = [_episode(1, 1, None)]
    sonarr.get_episode_files.return_value = []
    await svc.sync_all()

    await db.refresh(episode)
    assert episode.audio_languages_json is None
    assert episode.subtitle_languages_json is None
