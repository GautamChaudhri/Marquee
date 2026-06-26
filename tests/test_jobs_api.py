"""API-level tests for jobs.py additions: subject-title resolution, the
active/queued_only and since/until filters, and the by-type aggregate
endpoint."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient

from marquee.core.jobs.manager import job_manager
from marquee.main import app
from marquee.models import (
    Episode,
    EpisodeMediaFile,
    JobResource,
    JobResourceReservation,
    JobWorker,
    MediaFile,
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
