from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from marquee.config import settings
from marquee.core.jobs import handlers_maintenance
from marquee.core.poster_service import poster_service
from marquee.core.poster_subjects import PosterSubject
from marquee.database import _get_session_factory
from marquee.models import Season, Series


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
async def test_tv_maintenance_prunes_only_orphan_cache(db: AsyncSession, tmp_path):
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

    await db.commit()
    orphan = _make_image(settings.poster_cache_path / "tv" / "9999.jpg")
    referenced = settings.poster_cache_path / "tv" / "1396.jpg"
    assert referenced.exists()

    async def owns_current_attempt(_session):
        return True

    async def stage(*_args, **_kwargs):
        return None

    context = SimpleNamespace(
        request={"dry_run": True, "max_items": 10, "batch_size": 1},
        delivery=SimpleNamespace(canonical_job_id="job-maint"),
        attempt=SimpleNamespace(attempt_id=1, fence_token=1),
        cancellation=SimpleNamespace(cancel_called=False),
        progress=SimpleNamespace(stage=stage),
        writer=SimpleNamespace(owns_current_attempt=owns_current_attempt),
        session_factory=_get_session_factory(),
    )

    plan = await handlers_maintenance.execute_poster_maintenance(context)
    context.request = {
        "dry_run": False,
        "confirmed_plan_checksum": plan["plan_checksum"],
        "max_items": 10,
        "batch_size": 1,
    }
    result = await handlers_maintenance.execute_poster_maintenance(context)

    assert result["deleted_count"] == 1
    assert not orphan.exists()
    assert referenced.exists()
    series_in_db = (
        await db.execute(select(Series).where(Series.id == series.id))
    ).scalar_one_or_none()
    assert series_in_db is not None
