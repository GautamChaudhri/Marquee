from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from marquee.config import settings
from marquee.core.jobs import handlers_maintenance
from marquee.database import _get_session_factory


def _make_image(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (20, 30), (20, 80, 140)).save(path)
    return path


@pytest.fixture(autouse=True)
def poster_paths_to_tmp(tmp_path, monkeypatch):
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
    monkeypatch.setattr(settings, "RADARR_MEDIA_PATH", None)
    monkeypatch.setattr(settings, "SONARR_MEDIA_PATH", None)
    monkeypatch.setattr(settings, "RADARR_PATH_PREFIX", None)
    monkeypatch.setattr(settings, "SONARR_PATH_PREFIX", None)
    monkeypatch.setattr(settings, "MEDIA_ROOTS", [])


@pytest.mark.asyncio
async def test_poster_maintenance_seals_a_path_free_dry_run(tmp_path):
    orphan = _make_image(settings.poster_cache_path / "movies" / "9001.jpg")
    outside = _make_image(tmp_path / "outside.jpg")
    symlink = settings.poster_cache_path / "movies" / "linked.jpg"
    symlink.symlink_to(outside)
    context = SimpleNamespace(
        request={"dry_run": True, "max_items": 10, "batch_size": 1},
        delivery=SimpleNamespace(canonical_job_id="maintenance"),
        cancellation=SimpleNamespace(cancel_called=False),
        writer=SimpleNamespace(owns_current_attempt=lambda _session: True),
        session_factory=_get_session_factory(),
    )

    result = await handlers_maintenance.execute_poster_maintenance(context)

    assert result["outcome"] == "no_change"
    assert result["dry_run"] is True
    assert result["planned_count"] == 1
    assert len(result["plan_checksum"]) == 64
    assert orphan.exists()
    assert symlink.is_symlink()
    assert outside.exists()

    context.request = {
        "dry_run": False,
        "confirmed_plan_checksum": "0" * 64,
        "max_items": 10,
        "batch_size": 1,
    }
    with pytest.raises(handlers_maintenance.MaintenanceOperationError, match="does not match"):
        await handlers_maintenance.execute_poster_maintenance(context)
    assert orphan.exists()
