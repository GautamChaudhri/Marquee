"""Progress enrichment: operation narration + subject context on the job stream."""

from __future__ import annotations

import json

from sqlalchemy import select

from marquee.core.jobs import legacy_media
from marquee.core.jobs.builtin_handlers import _emit_stage, _update_progress
from marquee.core.jobs.manager import job_manager
from marquee.core.subtitles.mutation import _describe_operation
from marquee.models import JobEvent, MediaFile, MediaJob, Movie

# ---------------------------------------------------------------------------
# _describe_operation
# ---------------------------------------------------------------------------

_PLAN = {
    "before": {
        "tracks": [
            {
                "id": "sub-en-sdh",
                "language_tag": "en",
                "is_sdh": True,
                "source": "embedded",
            },
            {
                "id": "sub-en-comm",
                "language_tag": "en",
                "is_commentary": True,
                "source": "embedded",
            },
            {"id": "sub-fr", "language_tag": "fr", "source": "external"},
        ],
        "audio_streams": [
            {"index": 1, "language_tag": "en"},
            {"index": 2, "language_tag": "fr"},
        ],
    }
}


def test_describe_single_subtitle_removal_names_language_and_kind():
    text = _describe_operation("subtitle_remove", {"track_ids": ["sub-en-sdh"]}, _PLAN)
    assert text == "Removing English subtitle (SDH)"


def test_describe_combined_removal_names_subs_and_audio():
    text = _describe_operation(
        "track_remove",
        {"track_ids": ["sub-en-sdh", "sub-en-comm"], "audio_stream_indices": [1, 2]},
        _PLAN,
    )
    assert "English subtitle (SDH)" in text
    assert "English subtitle (Commentary)" in text
    assert "2 audio streams" in text
    assert "French" in text


def test_describe_many_subtitles_collapses_to_language_summary():
    plan = {
        "before": {
            "tracks": [
                {"id": f"s{i}", "language_tag": tag, "source": "embedded"}
                for i, tag in enumerate(["en", "en", "fr"])
            ],
            "audio_streams": [],
        }
    }
    text = _describe_operation("subtitle_remove", {"track_ids": ["s0", "s1", "s2"]}, plan)
    assert text.startswith("Removing 3 subtitles")
    assert "English" in text and "French" in text


def test_describe_embed_and_metadata_and_reorder():
    assert (
        _describe_operation("subtitle_embed", {"track_ids": ["sub-fr"]}, _PLAN)
        == "Embedding French subtitle"
    )
    assert (
        _describe_operation("subtitle_metadata", {"edits": [{}, {}]}, _PLAN)
        == "Editing metadata on 2 tracks"
    )
    assert (
        _describe_operation("audio_reorder", {"audio_stream_order": [2, 1, 0]}, _PLAN)
        == "Reordering 3 audio streams"
    )


def test_describe_falls_back_to_none_without_plan():
    assert _describe_operation("subtitle_remove", {"track_ids": ["x"]}, None) is None


# ---------------------------------------------------------------------------
# Bridge enrichment: title/file/message land in Job.progress + JobEvent.detail
# ---------------------------------------------------------------------------


async def test_run_media_enriches_mirrored_progress_with_subject_and_message(db, monkeypatch):
    movie = Movie(title="Dune", year=2021, folder_path="/movies/dune")
    db.add(movie)
    await db.flush()
    media_file = MediaFile(
        source="radarr",
        source_key="radarr:mf:enrich",
        movie_id=movie.id,
        path="/movies/dune/Dune.2021.mkv",
    )
    db.add(media_file)
    await db.commit()

    media_job = MediaJob(
        job_id="mj-enrich",
        operation="subtitle_remove",
        media_file_id=media_file.id,
        status="queued",
    )
    db.add(media_job)
    await db.commit()
    job = await job_manager.create(
        db, job_type="subtitle_remove", payload={"media_job_id": media_job.job_id}
    )

    async def fake_dispatch(_db, _job, emit):
        await emit(
            _db,
            _job.job_id,
            "remux",
            "start",
            message="Removing English subtitle (SDH) — mkvmerge",
        )
        await emit(_db, _job.job_id, "remux", "running", progress={"percent": 40})
        return {"ok": True}

    monkeypatch.setattr("marquee.core.media_jobs.handlers.dispatch", fake_dispatch)

    await legacy_media._run_media(job)

    await db.refresh(job)
    assert job.progress["title"] == "Dune"
    assert job.progress["file"] == "Dune.2021.mkv"
    # The message-only stage tick landed AND the later percent tick merged in.
    assert job.progress["message"] == "Removing English subtitle (SDH) — mkvmerge"
    assert job.progress["percent"] == 40

    events = (
        (await db.execute(select(JobEvent).where(JobEvent.job_id == job.id).order_by(JobEvent.id)))
        .scalars()
        .all()
    )
    remux_start = next(e for e in events if e.stage == "remux" and e.state == "start")
    assert remux_start.detail["message"] == "Removing English subtitle (SDH) — mkvmerge"
    assert remux_start.detail["title"] == "Dune"


# ---------------------------------------------------------------------------
# Handler helpers put the message inside the progress dict (poll + SSE parity)
# ---------------------------------------------------------------------------


async def test_update_progress_and_emit_stage_carry_message_in_detail(db):
    job = await job_manager.create(db, job_type="system_noop")
    await db.commit()

    await _update_progress(job.id, "scan", 3, 10, message="Scanning file 3/10")
    await db.refresh(job)
    assert job.progress["message"] == "Scanning file 3/10"
    assert job.progress["done"] == 3 and job.progress["total"] == 10

    await _emit_stage(job.id, "sync_movies", "Syncing movies from Radarr…")
    await db.refresh(job)
    assert job.progress["message"] == "Syncing movies from Radarr…"
    assert job.current_stage == "sync_movies"

    detail_messages = [
        (e.detail or {}).get("message")
        for e in (
            (await db.execute(select(JobEvent).where(JobEvent.job_id == job.id))).scalars().all()
        )
        if e.state == "running"
    ]
    assert "Scanning file 3/10" in detail_messages
    assert "Syncing movies from Radarr…" in detail_messages


# ---------------------------------------------------------------------------
# sync_all fires phase callbacks
# ---------------------------------------------------------------------------


async def test_sync_all_reports_phases(db):
    from marquee.core.sync_service import SyncService

    calls: list[tuple[str, str]] = []

    async def record(stage: str, message: str) -> None:
        calls.append((stage, message))

    class _FakeRadarr:
        pass

    service = SyncService(db, radarr=_FakeRadarr())  # type: ignore[arg-type]

    async def fake_sync_movies():
        return json.loads('{"added": 0}')

    service._sync_movies = fake_sync_movies  # type: ignore[method-assign]
    report = await service.sync_all(progress=record)
    assert report is not None
    assert calls == [("sync_movies", "Syncing movies from Radarr…")]
