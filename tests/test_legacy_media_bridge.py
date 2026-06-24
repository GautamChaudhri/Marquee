"""legacy_media._run_media bridge: session scoping, result write-back, batch sync."""

from __future__ import annotations

import json

import pytest

from marquee.core.jobs import legacy_media
from marquee.core.jobs.manager import job_manager
from marquee.models import MediaBatch, MediaJob


async def _make_media_job(db, *, operation="subtitle_scan", batch_id=None):
    media_job = MediaJob(
        job_id="mj-" + operation,
        operation=operation,
        media_file_id=None,
        status="queued",
        batch_id=batch_id,
    )
    db.add(media_job)
    await db.commit()
    job = await job_manager.create(
        db,
        job_type=operation,
        payload={"media_job_id": media_job.job_id},
    )
    return media_job, job


async def test_run_media_writes_result_json_on_success(db, monkeypatch):
    media_job, job = await _make_media_job(db)

    async def fake_dispatch(_db, _job, _emit):
        return {"tracks": 3}

    monkeypatch.setattr("marquee.core.media_jobs.handlers.dispatch", fake_dispatch)

    result = await legacy_media._run_media(job)

    assert result == {"tracks": 3}
    await db.refresh(media_job)
    assert media_job.status == "succeeded"
    assert json.loads(media_job.result_json) == {"tracks": 3}


async def test_run_media_records_error_on_failure(db, monkeypatch):
    media_job, job = await _make_media_job(db)

    async def fake_dispatch(_db, _job, _emit):
        raise RuntimeError("ffmpeg exploded")

    monkeypatch.setattr("marquee.core.media_jobs.handlers.dispatch", fake_dispatch)

    with pytest.raises(RuntimeError):
        await legacy_media._run_media(job)

    await db.refresh(media_job)
    assert media_job.status == "failed"
    assert "ffmpeg exploded" in media_job.error_json


async def test_run_media_updates_batch_progress_on_completion(db, monkeypatch):
    batch = MediaBatch(batch_id="batch-1", operation="subtitle_policy", status="running")
    db.add(batch)
    await db.commit()
    media_job, job = await _make_media_job(db, operation="subtitle_remove", batch_id="batch-1")

    async def fake_dispatch(_db, _job, _emit):
        return {"removed": 1}

    monkeypatch.setattr("marquee.core.media_jobs.handlers.dispatch", fake_dispatch)

    await legacy_media._run_media(job)

    await db.refresh(batch)
    assert batch.completed_count == 1
    assert batch.failed_count == 0
    assert batch.status == "completed"


async def test_run_media_emit_persists_progress_to_both_streams(db, monkeypatch):
    media_job, job = await _make_media_job(db)

    async def fake_dispatch(_db, _job, emit):
        await emit(_db, _job.job_id, "scan", "running", progress={"percent": 50})
        return {"ok": True}

    monkeypatch.setattr("marquee.core.media_jobs.handlers.dispatch", fake_dispatch)

    await legacy_media._run_media(job)

    await db.refresh(job)
    assert job.current_stage == "scan"
    assert job.progress == {"percent": 50}
