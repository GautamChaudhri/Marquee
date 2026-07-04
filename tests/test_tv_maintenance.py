from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from PIL import Image
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from marquee.config import settings
from marquee.core.heal import heal_scan
from marquee.core.jobs.builtin_handlers import (
    poster_backup_all,
    poster_deploy_reset,
    poster_maintenance,
    poster_rescan,
)
from marquee.core.poster_service import poster_service
from marquee.core.poster_subjects import PosterSubject
from marquee.models import Job, Season, Series


def _make_image(path, color=(20, 100, 150)):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (40, 60), color).save(path)
    return path


@pytest.fixture(autouse=True)
def cache_to_tmp(tmp_path, monkeypatch):
    # Point the poster cache at a temp dir (poster_cache_path is a property).
    monkeypatch.setattr(
        type(settings),
        "poster_cache_path",
        property(lambda self: tmp_path / "cache" / "posters"),
    )
    monkeypatch.setattr(
        type(settings),
        "poster_backup_path",
        property(lambda self: tmp_path / "backups" / "posters"),
    )
    monkeypatch.setattr(
        type(settings),
        "runs_work_path",
        property(lambda self: tmp_path / "runs" / "work"),
    )
    monkeypatch.setattr(
        type(settings),
        "runs_archive_path",
        property(lambda self: tmp_path / "runs" / "archive"),
    )
    # Disable media-root enforcement so tmp folders validate (dev mode).
    monkeypatch.setattr(settings, "RADARR_MEDIA_PATH", None)
    monkeypatch.setattr(settings, "SONARR_MEDIA_PATH", None)
    monkeypatch.setattr(settings, "RADARR_PATH_PREFIX", None)
    monkeypatch.setattr(settings, "MEDIA_ROOTS", [])
    yield


@pytest.mark.asyncio
async def test_tv_deploy_and_restore_series_and_season(db: AsyncSession, tmp_path):
    # Create Series
    series_folder = tmp_path / "Breaking Bad"
    series_folder.mkdir(parents=True)
    series = Series(
        title="Breaking Bad",
        year=2008,
        series_path=str(series_folder),
        tmdb_id=1396,
        sonarr_id=10,
    )
    db.add(series)
    await db.flush()

    # Create Season
    season = Season(
        series_id=series.id,
        season_number=1,
        episode_count=7,
        episode_file_count=7,
    )
    db.add(season)
    await db.flush()

    source = _make_image(tmp_path / "src.jpg")

    # 1. Deploy Series
    subject_series = PosterSubject.from_series(series)
    result_series = await poster_service.deploy(
        db,
        subject_series,
        source,
        source="feedback",
        poster_source_url="https://image.tmdb.org/t/p/original/bb_show.jpg",
    )
    assert (series_folder / "show.jpg").is_file()
    assert result_series.backup_path == str(tmp_path / "backups" / "posters" / f"series-{series.id}.jpg")
    assert Path(result_series.backup_path).is_file()
    assert (tmp_path / "cache" / "posters" / "tv" / "1396.jpg").is_file()

    # 2. Deploy Season
    subject_season = PosterSubject.from_season(season, series)
    result_season = await poster_service.deploy(
        db,
        subject_season,
        source,
        source="feedback",
        poster_source_url="https://image.tmdb.org/t/p/original/bb_s1.jpg",
    )
    assert (series_folder / "season01.jpg").is_file()
    assert result_season.backup_path == str(tmp_path / "backups" / "posters" / f"season-{season.id}.jpg")
    assert Path(result_season.backup_path).is_file()
    assert (tmp_path / "cache" / "posters" / "tv" / "1396-s01.jpg").is_file()

    # 3. Restore Series (delete file first)
    (series_folder / "show.jpg").unlink()
    restore_res = await poster_service.restore(db, subject_series, source="heal")
    assert restore_res.restored is True
    assert (series_folder / "show.jpg").is_file()

    # 4. Restore Season (delete file first)
    (series_folder / "season01.jpg").unlink()
    restore_res = await poster_service.restore(db, subject_season, source="heal")
    assert restore_res.restored is True
    assert (series_folder / "season01.jpg").is_file()


@pytest.mark.asyncio
async def test_tv_heal_scan(db: AsyncSession, tmp_path):
    series_folder = tmp_path / "Breaking Bad"
    series_folder.mkdir(parents=True)
    series = Series(
        title="Breaking Bad",
        year=2008,
        series_path=str(series_folder),
        tmdb_id=1396,
        sonarr_id=10,
    )
    db.add(series)
    await db.flush()

    source = _make_image(tmp_path / "src.jpg")
    subject = PosterSubject.from_series(series)
    await poster_service.deploy(db, subject, source)

    # Set poster_deployed_at in the past to bypass grace period
    series.poster_deployed_at = datetime.now(UTC) - timedelta(hours=1)
    await db.commit()

    # Delete deployed poster
    Path(series.poster_path).unlink()

    # Run heal_scan
    with patch("marquee.core.heal._get_session_factory", return_value=lambda: db):
        res = await heal_scan()

    assert res["checked"] == 1
    assert res["restored"] == 1
    assert res["by_type"]["series"]["restored"] == 1
    assert Path(series.poster_path).is_file()


@pytest.mark.asyncio
async def test_tv_maintenance_job_runs(db: AsyncSession, tmp_path, monkeypatch):
    # Setup a Series and a visible Season
    series_folder = tmp_path / "Breaking Bad"
    series_folder.mkdir(parents=True)
    series = Series(
        title="Breaking Bad",
        year=2008,
        series_path=str(series_folder),
        tmdb_id=1396,
        sonarr_id=10,
    )
    db.add(series)
    await db.flush()

    season = Season(
        series_id=series.id,
        season_number=1,
        episode_count=7,
        episode_file_count=7,
    )
    db.add(season)
    await db.flush()

    source = _make_image(tmp_path / "src.jpg")
    subject = PosterSubject.from_series(series)
    await poster_service.deploy(db, subject, source)

    # 1. Run poster_backup_all
    job = Job(id="job-backup", type="poster_backup_all", payload={})
    db.add(job)
    await db.flush()

    # Delete local backup on disk
    backup_file = Path(series.poster_local_backup_path)
    backup_file.unlink()

    backup_res = await poster_backup_all(job)
    assert backup_res["copied"] == 1
    assert backup_res["by_type"]["series"]["copied"] == 1
    assert backup_file.is_file()

    # 2. Run poster_rescan after shifting format setting
    job_rescan = Job(id="job-rescan", type="poster_rescan", payload={})
    db.add(job_rescan)
    await db.flush()

    # Shift settings.SERIES_POSTER_FORMAT
    with patch.object(settings, "SERIES_POSTER_FORMAT", "custom-show.jpg"):
        rescan_res = await poster_rescan(job_rescan)
        # Expected poster changed and doesn't exist on disk, so it gets set to None/missing
        assert rescan_res["missing"] == 1
        assert rescan_res["by_type"]["series"]["missing"] == 1
        await db.refresh(series)
        assert series.poster_path is None

    # Redeply poster
    await poster_service.deploy(db, subject, source)
    assert series.poster_path is not None

    # 3. Run poster_deploy_reset
    job_reset = Job(id="job-reset", type="poster_deploy_reset", payload={})
    db.add(job_reset)
    await db.flush()

    reset_res = await poster_deploy_reset(job_reset)
    assert reset_res["reset"] == 1
    assert reset_res["by_type"]["series"]["reset"] == 1
    await db.refresh(series)
    assert series.poster_path is None

    # 4. Run poster_maintenance to delete Sonarr-deleted shows
    # Re-setup series with poster
    series.sonarr_id = 99
    series.poster_path = str(series_folder / "show.jpg")
    (series_folder / "show.jpg").touch()
    await db.commit()

    # Mock SonarrClient
    mock_sonarr_payload = [{"id": 100}]  # series 99 is missing -> deleted candidate
    sonarr_mock = AsyncMock()
    sonarr_mock.get_series.return_value = mock_sonarr_payload

    # Patch settings.sonarr_configured and client class using monkeypatch
    monkeypatch.setattr(type(settings), "sonarr_configured", property(lambda self: True))
    monkeypatch.setattr(type(settings), "radarr_configured", property(lambda self: False))

    with patch(
        "marquee.core.arr_clients.sonarr_client.SonarrClient",
        return_value=sonarr_mock,
    ):
        job_maintenance = Job(id="job-maint", type="poster_maintenance", payload={})
        db.add(job_maintenance)
        await db.flush()

        maint_res = await poster_maintenance(job_maintenance)
        assert maint_res["series_deleted"] == 1

        # Check DB series row is deleted
        series_in_db = (await db.execute(select(Series).where(Series.id == series.id))).scalar_one_or_none()
        assert series_in_db is None
