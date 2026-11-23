from types import SimpleNamespace

import pytest
from PIL import Image
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from marquee.config import settings
from marquee.core.jobs import handlers_maintenance
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

    referenced = _make_image(settings.poster_cache_path / "tv" / "1396.jpg")

    await db.commit()
    orphan = _make_image(settings.poster_cache_path / "tv" / "9999.jpg")
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
