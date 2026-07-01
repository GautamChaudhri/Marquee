"""API-level tests for jobs.py additions: subject-title resolution, the
active/queued_only and since/until filters, and the by-type aggregate
endpoint."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from marquee.core.jobs.manager import job_manager
from marquee.core.media_files import ResolvedMediaFile
from marquee.main import app
from marquee.models import (
    Episode,
    EpisodeMediaFile,
    Job,
    JobResource,
    JobResourceReservation,
    JobWorker,
    MediaFile,
    MediaJob,
    Movie,
    Series,
)


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


async def test_movie_subject_title_resolved_in_list_and_detail(db, client):
    movie = Movie(title="Dune", year=2021, folder_path="/movies/dune")
    db.add(movie)
    await db.flush()
    job = await job_manager.create(
        db, job_type="poster_pipeline", subject_type="movie", subject_id=str(movie.id)
    )
    await db.commit()

    list_resp = await client.get("/api/jobs", params={"subject_type": "movie"})
    assert list_resp.status_code == 200
    found = next(j for j in list_resp.json()["jobs"] if j["job_id"] == job.id)
    assert found["label"] == "Poster Pipeline"
    assert found["subject"]["title"] == "Dune (2021)"

    detail_resp = await client.get(f"/api/jobs/{job.id}")
    assert detail_resp.status_code == 200
    assert detail_resp.json()["label"] == "Poster Pipeline"
    assert detail_resp.json()["subject"]["title"] == "Dune (2021)"


async def test_episode_backed_media_file_subject_title_resolved(db, client):
    series = Series(title="Breaking Bad", year=2008, series_path="/tv/breaking-bad")
    db.add(series)
    await db.flush()
    episode = Episode(series_id=series.id, season_number=2, episode_number=5)
    media_file = MediaFile(source="sonarr", source_key="sonarr:ep:1", path="/tv/bb/s02e05.mkv")
    db.add_all([episode, media_file])
    await db.flush()
    db.add(EpisodeMediaFile(episode_id=episode.id, media_file_id=media_file.id))
    await db.commit()

    job = await job_manager.create(
        db, job_type="subtitle_scan", subject_type="media_file", subject_id=str(media_file.id)
    )
    await db.commit()

    resp = await client.get(f"/api/jobs/{job.id}")
    assert resp.status_code == 200
    assert resp.json()["subject"]["title"] == "Breaking Bad S02E05"


async def test_movie_backed_media_file_subject_title_resolved(db, client):
    movie = Movie(title="Arrival", year=2016, folder_path="/movies/arrival")
    db.add(movie)
    await db.flush()
    media_file = MediaFile(
        source="radarr", source_key="radarr:mf:1", path="/movies/arrival/file.mkv", movie_id=movie.id
    )
    db.add(media_file)
    await db.commit()

    job = await job_manager.create(
        db, job_type="subtitle_scan", subject_type="media_file", subject_id=str(media_file.id)
    )
    await db.commit()

    resp = await client.get(f"/api/jobs/{job.id}")
    assert resp.status_code == 200
    assert resp.json()["subject"]["title"] == "Arrival (2016)"


async def test_active_filter_matches_manager_active_statuses_only(db, client):
    running = await job_manager.create(db, job_type="system_noop")
    running.status = "running"
    queued = await job_manager.create(db, job_type="system_noop")
    await db.commit()
    assert queued.status == "queued"

    resp = await client.get("/api/jobs", params={"active": "true"})
    ids = {j["job_id"] for j in resp.json()["jobs"]}
    assert running.id in ids
    assert queued.id not in ids


async def test_queued_only_filter_matches_queued_ish_statuses_only(db, client):
    running = await job_manager.create(db, job_type="system_noop")
    running.status = "running"
    waiting = await job_manager.create(db, job_type="system_noop")
    waiting.status = "waiting_resource"
    await db.commit()

    resp = await client.get("/api/jobs", params={"queued_only": "true"})
    ids = {j["job_id"] for j in resp.json()["jobs"]}
    assert waiting.id in ids
    assert running.id not in ids


async def test_queued_only_orders_by_priority_and_priority_patch_reorders(db, client):
    low = await job_manager.create(db, job_type="system_noop", priority=10)
    high = await job_manager.create(db, job_type="system_noop", priority=90)
    await db.commit()

    listed = await client.get("/api/jobs", params={"queued_only": "true"})
    ids = [job["job_id"] for job in listed.json()["jobs"] if job["job_id"] in {low.id, high.id}]
    assert ids[:2] == [high.id, low.id]

    patched = await client.patch(f"/api/jobs/{low.id}/priority", json={"priority": 100})
    assert patched.status_code == 200
    assert patched.json()["priority"] == 100

    reordered = await client.get("/api/jobs", params={"queued_only": "true"})
    ids = [job["job_id"] for job in reordered.json()["jobs"] if job["job_id"] in {low.id, high.id}]
    assert ids[:2] == [low.id, high.id]


async def test_priority_patch_rejects_non_queued_jobs(db, client):
    running = await job_manager.create(db, job_type="system_noop")
    running.status = "running"
    await db.commit()

    resp = await client.patch(f"/api/jobs/{running.id}/priority", json={"priority": 5})
    assert resp.status_code == 409


async def test_since_until_filters_narrow_by_created_at(db, client):
    old = await job_manager.create(db, job_type="system_noop")
    old.created_at = datetime.now(UTC) - timedelta(days=10)
    recent = await job_manager.create(db, job_type="system_noop")
    await db.commit()

    since_ts = int((datetime.now(UTC) - timedelta(days=1)).timestamp())
    resp = await client.get("/api/jobs", params={"since": since_ts})
    ids = {j["job_id"] for j in resp.json()["jobs"]}
    assert recent.id in ids
    assert old.id not in ids


async def test_metrics_by_type_reports_success_rate_and_duration(db, client):
    started = datetime.now(UTC) - timedelta(seconds=20)
    finished = datetime.now(UTC)
    for _ in range(3):
        job = await job_manager.create(db, job_type="system_noop")
        job.status = "succeeded"
        job.started_at = started
        job.finished_at = finished
        await db.commit()
    failed = await job_manager.create(db, job_type="system_noop")
    failed.status = "failed"
    failed.started_at = started
    failed.finished_at = finished
    await db.commit()

    resp = await client.get("/api/jobs/metrics/by-type")
    assert resp.status_code == 200
    stats = resp.json()["by_type"]["system_noop"]
    assert stats["sample_size"] == 4
    assert stats["success_rate"] == 0.75
    assert stats["duration_seconds"]["avg"] == pytest.approx(20.0, abs=1.0)


async def test_job_metrics_hides_idle_media_file_rows_and_stale_workers(db, client):
    await job_manager.bootstrap_resources(db)
    db.add_all(
        [
            JobResource(key="media-file:101", capacity=1),
            JobResource(key="media-file:202", capacity=1),
            JobWorker(
                id="worker-live",
                status="running",
                heartbeat_at=datetime.now(UTC),
            ),
            JobWorker(
                id="worker-dead",
                status="dead",
                heartbeat_at=datetime.now(UTC) - timedelta(hours=2),
            ),
        ]
    )
    active_job = await job_manager.create(db, job_type="system_noop")
    db.add(
        JobResourceReservation(
            job_id=active_job.id,
            resource_key="media-file:202",
            units=1,
        )
    )
    await db.commit()

    resp = await client.get("/api/jobs/metrics")
    assert resp.status_code == 200
    body = resp.json()

    resource_keys = {row["key"] for row in body["resources"]}
    assert "gpu" in resource_keys
    assert "media-file:202" in resource_keys
    assert "media-file:101" not in resource_keys
    assert body["workers"] == [
        {
            "id": "worker-live",
            "status": "running",
            "heartbeat_at": body["workers"][0]["heartbeat_at"],
        }
    ]


async def test_job_detail_hydrates_linked_media_request_plan_and_error(db, client):
    media_file = MediaFile(
        source="radarr",
        source_key="radarr:mf:detail",
        path="/movies/detail/file.mkv",
    )
    db.add(media_file)
    await db.commit()
    media_job = MediaJob(
        job_id="mj-detail-1",
        operation="subtitle_remove",
        media_file_id=media_file.id,
        status="failed",
        request_json='{"track_ids":["sub-en"],"backup":true}',
        plan_json='{"operation":"subtitle_remove","before":{"tracks":[{"id":"sub-en"}],"audio_streams":[]},"after":{"tracks":[],"audio_streams":[]}}',
        result_json='{"operation":"subtitle_remove","selection":{"tracks_removed":[{"id":"sub-en"}]}}',
        error_json='{"type":"PreflightError","code":"remux_failed","error":"mkvmerge exited 2"}',
    )
    db.add(media_job)
    await db.commit()
    job = await job_manager.create(
        db,
        job_type="subtitle_remove",
        payload={"media_job_id": media_job.job_id},
        subject_type="media_file",
        subject_id=str(media_file.id),
        status="failed",
    )
    job.result = {"generic": True}
    job.error = {"type": "RuntimeError", "message": "generic failure"}
    await db.commit()

    resp = await client.get(f"/api/jobs/{job.id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["media_job_id"] == "mj-detail-1"
    assert body["request"] == {"track_ids": ["sub-en"], "backup": True}
    assert body["plan"]["operation"] == "subtitle_remove"
    assert body["result"]["selection"]["tracks_removed"] == [{"id": "sub-en"}]
    assert body["error"]["code"] == "remux_failed"


async def test_job_children_endpoint_and_detail_include_child_context(db, client):
    movie = Movie(title="Batch Child", year=2022, folder_path="/movies/batch-child")
    db.add(movie)
    await db.flush()
    media_file = MediaFile(
        source="radarr",
        source_key="radarr:mf:child",
        path="/movies/batch-child/file.mkv",
        movie_id=movie.id,
    )
    db.add(media_file)
    await db.commit()

    parent = await job_manager.create(
        db,
        job_type="letterbox_detect_batch",
        status="waiting_external",
        subject_type="letterbox_batch",
        subject_id="batch-1",
    )
    media_job = MediaJob(
        job_id="mj-child-1",
        operation="subtitle_remove",
        media_file_id=media_file.id,
        status="succeeded",
        request_json='{"track_ids":["sub-fr"]}',
        plan_json='{"operation":"subtitle_remove"}',
        result_json='{"selection":{"tracks_removed":[{"id":"sub-fr"}]}}',
    )
    db.add(media_job)
    await db.commit()
    child = await job_manager.create(
        db,
        job_type="subtitle_remove",
        payload={"media_job_id": media_job.job_id},
        parent_id=parent.id,
        subject_type="media_file",
        subject_id=str(media_file.id),
        status="succeeded",
    )
    await db.commit()

    children_resp = await client.get(f"/api/jobs/{parent.id}/children")
    assert children_resp.status_code == 200
    children = children_resp.json()["children"]
    assert len(children) == 1
    assert children[0]["job_id"] == child.id
    assert children[0]["subject"]["title"] == "Batch Child (2022)"
    assert children[0]["request"] == {"track_ids": ["sub-fr"]}
    assert children[0]["result"]["selection"]["tracks_removed"] == [{"id": "sub-fr"}]

    detail_resp = await client.get(f"/api/jobs/{parent.id}")
    assert detail_resp.status_code == 200
    detail_children = detail_resp.json()["children"]
    assert len(detail_children) == 1
    assert detail_children[0]["job_id"] == child.id


async def test_subtitle_plan_supersedes_old_paired_generic_job(db, client, monkeypatch, tmp_path):
    media = tmp_path / "Movie.mkv"
    media.write_bytes(b"x")
    media_file = MediaFile(
        source="radarr",
        source_key="radarr:mf:subtitle-plan",
        path=str(media),
    )
    db.add(media_file)
    await db.commit()

    resolved = ResolvedMediaFile(
        media_file_id=media_file.id,
        source="radarr",
        path=media,
        size_bytes=media.stat().st_size,
        mtime_ns=media.stat().st_mtime_ns,
        st_nlink=1,
        signature="sig-subtitle-plan",
        container="mkv",
        movie_id=None,
    )

    async def fake_resolve_media_file(_db, media_file_id):
        assert media_file_id == media_file.id
        return resolved

    async def fake_inventory(_db, media_file_id):
        assert media_file_id == media_file.id
        return {
            "inventory_id": 1,
            "tracks": [],
            "audio_streams": [],
            "coverage": {},
            "container_family": "mkv",
            "capabilities": {"can_remove": True},
        }

    async def fake_build_plan(_db, _resolved, _inventory, **_kwargs):
        return {
            "operation": "subtitle_remove",
            "before": {"tracks": [], "audio_streams": []},
            "after": {"tracks": [], "audio_streams": []},
            "warnings": [],
            "capabilities": {"can_execute": True},
        }

    monkeypatch.setattr("marquee.api.routes.subtitles._require_ffprobe", lambda: None)
    monkeypatch.setattr("marquee.api.routes.subtitles.resolve_media_file", fake_resolve_media_file)
    monkeypatch.setattr("marquee.api.routes.subtitles.service.get_inventory_dict", fake_inventory)
    monkeypatch.setattr("marquee.api.routes.subtitles.mutation.build_plan", fake_build_plan)

    first = await client.post(
        f"/api/media-files/{media_file.id}/subtitle-plans",
        json={"operation": "subtitle_remove", "track_ids": ["sub-en"]},
    )
    second = await client.post(
        f"/api/media-files/{media_file.id}/subtitle-plans",
        json={"operation": "subtitle_remove", "track_ids": ["sub-en"]},
    )

    assert first.status_code == 201
    assert second.status_code == 201

    first_id = first.json()["job_id"]
    second_id = second.json()["job_id"]
    generic_jobs = (await db.execute(select(Job).where(Job.type == "subtitle_remove"))).scalars().all()
    generic_by_media_id = {
        row.payload.get("media_job_id"): row
        for row in generic_jobs
        if isinstance(row.payload, dict) and row.payload.get("media_job_id")
    }

    assert (await db.get(MediaJob, first_id)).status == "cancelled"
    assert generic_by_media_id[first_id].status == "cancelled"
    assert generic_by_media_id[first_id].finished_at is not None
    assert (await db.get(MediaJob, second_id)).status == "planned"
    assert generic_by_media_id[second_id].status == "planned"
