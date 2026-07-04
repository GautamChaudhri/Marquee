"""Sync tests for plan 06 phase 1: Sonarr overlay reference data + episode HDR capture."""

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from marquee.core.sync_service import SyncService
from marquee.models import (
    Episode,
    RadarrCustomFormat,
    RadarrProfileFormatItem,
    RadarrQualityProfile,
    Series,
    SonarrCustomFormat,
    SonarrProfileFormatItem,
    SonarrQualityProfile,
)


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
            {"seasonNumber": 1, "statistics": {"episodeCount": 6, "episodeFileCount": 6}},
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
async def test_episode_hdr_capture_variants_and_multi_episode_file(db: AsyncSession):
    sonarr = AsyncMock()
    sonarr.get_series.return_value = [_sonarr_series_data()]
    sonarr.get_episodes.return_value = [
        _episode(1, 1, 5001),  # SDR (empty videoDynamicRangeType, mediaInfo present)
        _episode(2, 2, 5002),  # HDR10
        _episode(3, 3, 5003),  # HDR10Plus
        _episode(4, 4, 5004),  # DV
        _episode(5, 5, 5005),  # DV HDR10 (multi-episode file, shared with ep 6)
        _episode(6, 6, 5005),
        _episode(7, 7, None),  # no file at all -> unknown
        _episode(8, 8, 5006),  # file with no mediaInfo -> unknown
    ]
    sonarr.get_episode_files.return_value = [
        {"id": 5001, "path": "/tv/w/s01e01.mkv", "mediaInfo": {"videoDynamicRangeType": ""}},
        {"id": 5002, "path": "/tv/w/s01e02.mkv", "mediaInfo": {"videoDynamicRangeType": "HDR10"}},
        {
            "id": 5003,
            "path": "/tv/w/s01e03.mkv",
            "mediaInfo": {"videoDynamicRangeType": "HDR10Plus"},
        },
        {"id": 5004, "path": "/tv/w/s01e04.mkv", "mediaInfo": {"videoDynamicRangeType": "DV"}},
        {
            "id": 5005,
            "path": "/tv/w/s01e05-e06.mkv",
            "mediaInfo": {"videoDynamicRangeType": "DV HDR10", "width": 3840, "height": 2160},
        },
        {"id": 5006, "path": "/tv/w/s01e08.mkv"},
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

    assert episodes[1].hdr_type_raw == "SDR"
    assert episodes[1].has_hdr is False
    assert episodes[1].has_dv is False

    assert episodes[2].hdr_type_raw == "HDR10"
    assert episodes[2].has_hdr is True
    assert episodes[2].has_dv is False

    assert episodes[3].hdr_type_raw == "HDR10Plus"
    assert episodes[3].has_hdr is True

    assert episodes[4].hdr_type_raw == "DV"
    assert episodes[4].has_dv is True

    # Multi-episode file: both episodes get identical HDR + resolution truth.
    for ep_num in (5, 6):
        assert episodes[ep_num].hdr_type_raw == "DV HDR10"
        assert episodes[ep_num].has_hdr is True
        assert episodes[ep_num].has_dv is True
        assert episodes[ep_num].video_width == 3840
        assert episodes[ep_num].video_height == 2160

    # No file at all -> unknown (NULL), not SDR.
    assert episodes[7].hdr_type_raw is None
    assert episodes[7].has_hdr is None
    assert episodes[7].has_dv is None
    assert episodes[7].video_width is None
    assert episodes[7].video_height is None

    # File present but no mediaInfo -> unknown (NULL), not SDR.
    assert episodes[8].hdr_type_raw is None
    assert episodes[8].has_hdr is None
    assert episodes[8].has_dv is None


@pytest.mark.asyncio
async def test_episode_hdr_columns_clear_when_file_removed(db: AsyncSession):
    sonarr = AsyncMock()
    sonarr.get_series.return_value = [_sonarr_series_data()]
    sonarr.get_episodes.return_value = [_episode(1, 1, 5001)]
    sonarr.get_episode_files.return_value = [
        {"id": 5001, "path": "/tv/w/s01e01.mkv", "mediaInfo": {"videoDynamicRangeType": "HDR10"}},
    ]

    svc = SyncService(db, sonarr=sonarr)
    await svc.sync_all()

    series = (await db.execute(select(Series).where(Series.sonarr_id == 101))).scalar_one()
    episode = (
        await db.execute(select(Episode).where(Episode.series_id == series.id))
    ).scalar_one()
    assert episode.hdr_type_raw == "HDR10"

    # File removed on next sync.
    sonarr.get_episodes.return_value = [_episode(1, 1, None)]
    sonarr.get_episode_files.return_value = []
    await svc.sync_all()

    await db.refresh(episode)
    assert episode.hdr_type_raw is None
    assert episode.has_hdr is None
    assert episode.has_dv is None
    assert episode.video_width is None
    assert episode.video_height is None


@pytest.mark.asyncio
async def test_sync_sonarr_overlay_reference_data_upserts_and_deletes_stale(db: AsyncSession):
    sonarr = AsyncMock()
    sonarr.get_series.return_value = [_sonarr_series_data()]
    sonarr.get_episodes.return_value = []
    sonarr.get_episode_files.return_value = []
    sonarr.get_custom_formats.return_value = [
        {
            "id": 15,
            "name": "Dolby Vision",
            "includeCustomFormatWhenRenaming": True,
            "specifications": [],
        },
        {
            "id": 20,
            "name": "HDR10+",
            "includeCustomFormatWhenRenaming": False,
            "specifications": [],
        },
    ]
    sonarr.get_quality_profiles.return_value = [
        {
            "id": 3,
            "name": "4K HDR",
            "upgradeAllowed": True,
            "cutoffFormatScore": 100,
            "minFormatScore": 0,
            "formatItems": [
                {"format": 15, "name": "Dolby Vision", "score": 15},
                {"format": 20, "name": "HDR10+", "score": 10},
            ],
        }
    ]

    svc = SyncService(db, sonarr=sonarr)
    await svc.sync_all()

    custom_formats = {row.id: row for row in (await db.execute(select(SonarrCustomFormat))).scalars()}
    profiles = (await db.execute(select(SonarrQualityProfile))).scalars().all()
    items = (await db.execute(select(SonarrProfileFormatItem))).scalars().all()

    assert set(custom_formats) == {15, 20}
    assert custom_formats[15].name == "Dolby Vision"
    assert custom_formats[20].name == "HDR10+"
    assert [p.name for p in profiles] == ["4K HDR"]
    assert {(i.profile_id, i.custom_format_id, i.score) for i in items} == {(3, 15, 15), (3, 20, 10)}

    # Radarr tables untouched by the Sonarr sync.
    assert (await db.execute(select(RadarrCustomFormat))).scalars().all() == []
    assert (await db.execute(select(RadarrQualityProfile))).scalars().all() == []
    assert (await db.execute(select(RadarrProfileFormatItem))).scalars().all() == []

    # Second sync with the "HDR10+" CF removed -> its rows are deleted, not orphaned.
    sonarr.get_custom_formats.return_value = [
        {
            "id": 15,
            "name": "Dolby Vision",
            "includeCustomFormatWhenRenaming": True,
            "specifications": [],
        },
    ]
    sonarr.get_quality_profiles.return_value = [
        {
            "id": 3,
            "name": "4K HDR",
            "upgradeAllowed": True,
            "cutoffFormatScore": 100,
            "minFormatScore": 0,
            "formatItems": [{"format": 15, "name": "Dolby Vision", "score": 15}],
        }
    ]
    await svc.sync_all()

    remaining_cfs = {row.id for row in (await db.execute(select(SonarrCustomFormat))).scalars()}
    assert remaining_cfs == {15}
    remaining_items = {
        (row.profile_id, row.custom_format_id)
        for row in (await db.execute(select(SonarrProfileFormatItem))).scalars()
    }
    assert remaining_items == {(3, 15)}
