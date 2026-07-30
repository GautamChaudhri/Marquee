from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from marquee.config import settings
from marquee.core.jobs import handlers_maintenance
from marquee.core.jobs.mutation_documents import MaintenanceResultV1
from marquee.core.pipeline_config import pipeline_settings
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


@pytest.fixture
def cache_paths_to_tmp(tmp_path, monkeypatch):
    """Point every cache-clear root at a disposable tree."""
    roots = {
        "runs_work_path": tmp_path / "runs" / "work",
        "poster_staging_path": tmp_path / "staging",
        "runs_archive_path": tmp_path / "runs" / "archive",
    }
    for name, path in roots.items():
        monkeypatch.setattr(type(settings), name, property(lambda self, p=path: p))
    embeddings = tmp_path / "cache" / "embeddings"
    monkeypatch.setattr(pipeline_settings, "EMBEDDING_CACHE_DIR", embeddings)
    return {**roots, "embeddings": embeddings}


async def _owns_current_attempt(_session):
    return True


async def _noop_progress(*_args, **_kwargs):
    return None


def _cache_clear_context(request: dict):
    return SimpleNamespace(
        request=request,
        delivery=SimpleNamespace(canonical_job_id="cache-clear"),
        cancellation=SimpleNamespace(cancel_called=False),
        writer=SimpleNamespace(owns_current_attempt=_owns_current_attempt),
        session_factory=_get_session_factory(),
        progress=SimpleNamespace(stage=_noop_progress),
    )


@pytest.mark.asyncio
async def test_pipeline_cache_clear_needs_the_plan_it_showed(cache_paths_to_tmp, monkeypatch):
    """Only a job quoting the sealed plan may delete — a dry run never does."""
    work = _make_image(cache_paths_to_tmp["runs_work_path"] / "run-a" / "cand-1.jpg")
    staged = _make_image(cache_paths_to_tmp["poster_staging_path"] / "run-a" / "cand-2.jpg")
    embedding = _make_image(cache_paths_to_tmp["embeddings"] / "abc123.jpg")
    archive = _make_image(cache_paths_to_tmp["runs_archive_path"] / "run-a" / "archive.jpg")

    context = _cache_clear_context({"dry_run": True, "include_archives": False})
    plan = await handlers_maintenance.execute_pipeline_cache_clear(context)

    assert plan["dry_run"] is True
    assert plan["planned_count"] == 3
    assert plan["counts"] == {"runs_work": 1, "staging": 1, "embeddings": 1}
    assert work.exists() and staged.exists() and embedding.exists()

    context.request = {
        "dry_run": False,
        "include_archives": False,
        "confirmed_plan_checksum": "0" * 64,
    }
    with pytest.raises(handlers_maintenance.MaintenanceOperationError, match="does not match"):
        await handlers_maintenance.execute_pipeline_cache_clear(context)
    assert work.exists()

    context.request = {
        "dry_run": False,
        "include_archives": False,
        "confirmed_plan_checksum": plan["plan_checksum"],
        "batch_size": 2,
    }
    thread_hops = 0

    async def inline_to_thread(function, *args, **kwargs):
        nonlocal thread_hops
        thread_hops += 1
        return function(*args, **kwargs)

    monkeypatch.setattr(handlers_maintenance.asyncio, "to_thread", inline_to_thread)
    applied = await handlers_maintenance.execute_pipeline_cache_clear(context)

    assert applied["outcome"] == "succeeded"
    assert applied["deleted_count"] == 3
    assert thread_hops == 2
    assert not work.exists()
    assert not staged.exists()
    assert not embedding.exists()
    # The archives checkbox was off, so results history is untouched.
    assert archive.exists()


@pytest.mark.asyncio
async def test_pipeline_cache_clear_plans_a_scope_larger_than_ten_thousand_files(
    cache_paths_to_tmp,
):
    """The real work directory holds tens of thousands of poster candidates.

    ``_walk_files`` raises rather than truncating past ``max_items``, so the
    default bound has to cover a genuine cache or the clear is unusable.
    """
    root = cache_paths_to_tmp["runs_work_path"] / "run-a"
    root.mkdir(parents=True, exist_ok=True)
    for index in range(10_050):
        (root / f"cand-{index}.jpg").write_bytes(b"x")

    plan = await handlers_maintenance.execute_pipeline_cache_clear(
        _cache_clear_context({"dry_run": True})
    )

    assert plan["planned_count"] == 10_050
    assert MaintenanceResultV1.model_validate(plan).planned_count == 10_050
