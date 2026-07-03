"""legacy_media._run_media bridge: session scoping, result write-back, batch sync."""

from __future__ import annotations

import asyncio
import json

import pytest
from sqlalchemy import select

from marquee.core.jobs import legacy_media
from marquee.core.jobs.manager import job_manager
from marquee.core.media_jobs import media_job_manager
from marquee.core.subtitles.mutation import PreflightError
from marquee.models import Job, MediaBatch, MediaFile, MediaJob


async def _make_media_job(
    db,
    *,
    operation="subtitle_scan",
    batch_id=None,
    media_file_id=None,
    request_json=None,
):
    media_job = MediaJob(
        job_id="mj-" + operation,
        operation=operation,
        media_file_id=media_file_id,
        status="queued",
        batch_id=batch_id,
        request_json=json.dumps(request_json) if request_json is not None else None,
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


async def test_run_media_records_structured_error_context(db, monkeypatch):
    media_file = MediaFile(
        source="radarr", source_key="radarr:mf:55", path="/movies/failure/file.mkv"
    )
    db.add(media_file)
    await db.commit()
    media_job, job = await _make_media_job(
        db,
        operation="subtitle_remove",
        media_file_id=media_file.id,
        request_json={"track_ids": ["sub-en"], "backup": True},
    )

    async def fake_dispatch(_db, _job, _emit):
        # Production writes the stage through emit(), which commits before any
        # failure propagates; commit here so the fail handler's fresh session
        # sees it (test sessions no longer share state post-PostgreSQL move).
        _job.stage = "remux"
        await _db.commit()
        raise PreflightError("remux_failed", "mkvmerge exited 2")

    monkeypatch.setattr("marquee.core.media_jobs.handlers.dispatch", fake_dispatch)

    with pytest.raises(PreflightError):
        await legacy_media._run_media(job)

    await db.refresh(media_job)
    error = json.loads(media_job.error_json)
    assert media_job.status == "failed"
    assert error["type"] == "PreflightError"
    assert error["code"] == "remux_failed"
    assert error["operation"] == "subtitle_remove"
    assert error["stage"] == "remux"
    assert error["file_path"] == "/movies/failure/file.mkv"
    assert error["request"] == {"track_ids": ["sub-en"], "backup": True}


async def test_run_media_marks_cancel_requested_failure_as_cancelled(db, monkeypatch):
    media_job, job = await _make_media_job(db, operation="letterbox_reencode")
    media_job.cancel_requested = True
    await db.commit()

    async def fake_dispatch(_db, _job, _emit):
        raise RuntimeError("cancelled during encode")

    monkeypatch.setattr("marquee.core.media_jobs.handlers.dispatch", fake_dispatch)

    with pytest.raises(RuntimeError):
        await legacy_media._run_media(job)

    await db.refresh(media_job)
    assert media_job.status == "cancelled"


async def test_run_media_marks_shutdown_cancellation_as_interrupted(db, monkeypatch):
    media_job, job = await _make_media_job(db, operation="letterbox_reencode")

    async def fake_dispatch(_db, _job, _emit):
        raise asyncio.CancelledError("worker shutdown")

    monkeypatch.setattr("marquee.core.media_jobs.handlers.dispatch", fake_dispatch)

    with pytest.raises(asyncio.CancelledError):
        await legacy_media._run_media(job)

    await db.refresh(media_job)
    assert media_job.status == "interrupted"
    assert "worker shutdown" in media_job.error_json


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
    assert job.progress["percent"] == 50
    assert job.progress["stage"] == "scan"


async def _make_media_file(db, key: str) -> MediaFile:
    media_file = MediaFile(source="radarr", source_key=key, path=f"/movies/{key}.mkv")
    db.add(media_file)
    await db.commit()
    return media_file


async def test_create_job_preserves_track_remove_operation(db):
    media_file = await _make_media_file(db, "radarr:mf:track-remove")
    media_job = await media_job_manager.create_job(
        db,
        operation="track_remove",
        media_file_id=media_file.id,
        request={"track_ids": ["sub-fr"]},
        status="queued",
    )

    generic_job = (await db.execute(select(Job))).scalar_one()

    assert media_job.operation == "track_remove"
    assert generic_job.type == "track_remove"


async def test_create_job_preserves_audio_remove_operation(db):
    media_file = await _make_media_file(db, "radarr:mf:audio-remove")
    media_job = await media_job_manager.create_job(
        db,
        operation="audio_remove",
        media_file_id=media_file.id,
        request={"audio_stream_indices": [2]},
        status="queued",
    )

    generic_job = (await db.execute(select(Job))).scalar_one()

    assert media_job.operation == "audio_remove"
    assert generic_job.type == "audio_remove"


async def test_supersede_planned_media_jobs_cancels_paired_generic_jobs(db):
    media_file = await _make_media_file(db, "radarr:mf:supersede")
    media_job = await media_job_manager.create_job(
        db,
        operation="subtitle_remove",
        media_file_id=media_file.id,
        request={"track_ids": ["sub-en"]},
        status="planned",
    )
    generic_job = (await db.execute(select(Job))).scalar_one()

    cancelled_ids = await media_job_manager.supersede_planned_media_jobs(
        db,
        media_file_id=media_file.id,
        operation="subtitle_remove",
    )

    await db.refresh(media_job)
    await db.refresh(generic_job)
    assert cancelled_ids == [media_job.job_id]
    assert media_job.status == "cancelled"
    assert generic_job.status == "cancelled"
    assert generic_job.finished_at is not None
