"""Regression coverage for durable job-platform hardening."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest
from sqlalchemy import update

from marquee import database
from marquee.core import letterbox_reencode as lr
from marquee.core.media_jobs.manager import media_job_manager
from marquee.models import MediaJob


def test_engine_connect_args_apply_timeouts_only_to_asyncpg(monkeypatch):
    monkeypatch.setattr(database.settings, "DB_LOCK_TIMEOUT_MS", 10_000)
    monkeypatch.setattr(database.settings, "DB_IDLE_TXN_TIMEOUT_MS", 300_000)

    assert database._engine_connect_args("sqlite+aiosqlite:///test.db") == {}
    assert database._engine_connect_args("postgresql+asyncpg://localhost/marquee") == {
        "server_settings": {
            "lock_timeout": "10000",
            "idle_in_transaction_session_timeout": "300000",
        }
    }


@pytest.mark.asyncio
async def test_encode_progress_does_not_block_concurrent_media_cancel(db, tmp_path, monkeypatch):
    job = MediaJob(job_id="short-session-progress", operation="letterbox_reencode", status="running")
    db.add(job)
    await db.commit()
    progress_persisted = asyncio.Event()
    script = (
        "import sys, time\n"
        "for _ in range(1000):\n"
        " print('out_time_ms=1000000'); print('progress=continue'); sys.stdout.flush(); time.sleep(0.01)\n"
    )
    monkeypatch.setattr(lr.binaries, "resolve", lambda _name: sys.executable)
    monkeypatch.setattr(lr, "build_ffmpeg_args", lambda *_args: ["-c", script])

    async def emit(_db, job_id, stage, state, **kwargs):
        factory = database._get_session_factory()
        async with factory() as event_db:
            await media_job_manager.emit(
                event_db,
                job_id,
                stage,
                state,
                message=kwargs.get("message"),
                progress=kwargs.get("progress"),
                persist=kwargs.get("persist", True),
            )
        if kwargs.get("persist", True):
            progress_persisted.set()

    task = asyncio.create_task(
        lr._run_encode_attempt(
            db,
            job,
            emit,
            Path("/source.mkv"),
            tmp_path / "output.mkv",
            {},
            lr.SourceVideo(
                codec="hevc",
                width=1920,
                height=1080,
                pix_fmt="yuv420p",
                color_transfer=None,
                color_primaries=None,
                color_space=None,
                duration_s=60,
                has_hdr=False,
                has_dovi=False,
                dovi_profile=None,
                dovi_level=None,
                dovi_el_present=None,
                dovi_bl_signal_compatibility_id=None,
                video_streams=1,
                audio_streams=0,
                subtitle_streams=0,
                attachment_streams=0,
            ),
        )
    )
    await asyncio.wait_for(progress_persisted.wait(), timeout=1)

    factory = database._get_session_factory()
    async with factory() as cancel_db:
        await asyncio.wait_for(
            cancel_db.execute(
                update(MediaJob).where(MediaJob.job_id == job.job_id).values(cancel_requested=True)
            ),
            timeout=1,
        )
        await asyncio.wait_for(cancel_db.commit(), timeout=1)

    with pytest.raises(lr.ReencodePlanError, match="cancelled"):
        await asyncio.wait_for(task, timeout=2)
