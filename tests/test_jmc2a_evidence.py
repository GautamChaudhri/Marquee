"""JMC2A durable-evidence, confinement, and history-survival contracts."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from marquee.models import (
    ArtworkEvent,
    Episode,
    Job,
    JobArtifact,
    JobAttempt,
    JobEvent,
    JobLog,
    LetterboxEvent,
    MediaFile,
    MediaOperationDetail,
    Movie,
    PipelineRun,
    Season,
    Series,
    WorkerNode,
)
from tests.support.canonical_poster import seed_canonical_pipeline_run


def _job(job_id: str = "evidence-job") -> Job:
    return Job(
        id=job_id,
        type="media_test",
        payload_version=1,
        request={},
        phase="running",
        root_id=job_id,
        subject_kind="movie",
        subject_reference="tmdb:42",
        subject_snapshot={"version": 1, "kind": "movie", "title": "Snapshot Movie"},
    )


async def test_evidence_survives_live_subject_deletion(db):
    movie = Movie(
        title="Live Movie",
        year=2026,
        folder_path="/movies/live",
        radarr_id=42,
    )
    db.add(movie)
    await db.flush()
    media_file = MediaFile(
        source="radarr",
        source_key="radarr:movie-file:42",
        movie_id=movie.id,
        path="/movies/live/movie.mkv",
    )
    job = _job()
    db.add_all([media_file, job])
    await db.flush()
    attempt = JobAttempt(job_id=job.id, number=1, fence_token=1, phase="running")
    db.add(attempt)
    await db.flush()
    db.add_all(
        [
            JobEvent(
                job_id=job.id,
                attempt_id=attempt.id,
                event_key="attempt.started",
                state="running",
            ),
            JobLog(
                job_id=job.id,
                attempt_id=attempt.id,
                segment=0,
                storage_key="jobs/evidence-job/attempt-1.log",
            ),
            JobArtifact(
                job_id=job.id,
                attempt_id=attempt.id,
                kind="report",
                name="validation",
                virtual_source={"document": "job.result.validation"},
            ),
            MediaOperationDetail(
                job_id=job.id,
                operation_kind="test",
                media_file_id=media_file.id,
                media_snapshot={"path": media_file.path, "title": movie.title},
                target_snapshot={"kind": "movie", "title": movie.title},
                input_signature="sha256:input",
            ),
        ]
    )
    await db.commit()
    job_id = job.id
    attempt_id = attempt.id

    await db.delete(movie)
    await db.commit()
    db.expire_all()

    assert await db.get(Job, job_id) is not None
    assert await db.get(JobAttempt, attempt_id) is not None
    assert (await db.scalars(select(JobEvent).where(JobEvent.job_id == job_id))).one()
    assert (await db.scalars(select(JobLog).where(JobLog.job_id == job_id))).one()
    assert (await db.scalars(select(JobArtifact).where(JobArtifact.job_id == job_id))).one()
    detail = await db.get(MediaOperationDetail, job_id)
    assert detail is not None
    assert detail.media_file_id is None
    assert detail.media_snapshot["title"] == "Live Movie"


async def test_deliberate_job_purge_cascades_owned_evidence(db):
    job = _job("purged-job")
    db.add(job)
    await db.flush()
    attempt = JobAttempt(job_id=job.id, number=1, fence_token=1, phase="running")
    db.add(attempt)
    await db.flush()
    db.add_all(
        [
            JobLog(
                job_id=job.id,
                attempt_id=attempt.id,
                segment=0,
                storage_key="jobs/purged-job/attempt-1.log",
            ),
            JobArtifact(
                job_id=job.id,
                kind="summary",
                name="summary",
                virtual_source={"document": "job.result"},
            ),
            MediaOperationDetail(
                job_id=job.id,
                operation_kind="test",
                media_snapshot={"path": "/retired/file.mkv"},
                target_snapshot={"kind": "movie", "title": "Retired"},
                input_signature="sha256:retired",
            ),
        ]
    )
    await db.commit()
    job_id = job.id
    attempt_id = attempt.id

    await db.delete(job)
    await db.commit()
    db.expire_all()

    assert await db.get(JobAttempt, attempt_id) is None
    assert not (await db.scalars(select(JobLog))).all()
    assert not (await db.scalars(select(JobArtifact))).all()
    assert await db.get(MediaOperationDetail, job_id) is None


async def test_tv_history_survives_series_season_and_episode_deletion(db):
    series = Series(title="Snapshot Show", year=2026, series_path="/tv/snapshot")
    db.add(series)
    await db.flush()
    season = Season(series_id=series.id, season_number=1)
    episode = Episode(
        series_id=series.id,
        season_number=1,
        episode_number=1,
        title="Snapshot Episode",
    )
    db.add_all([season, episode])
    await db.flush()
    snapshot = {"kind": "series", "title": series.title, "season": 1, "episode": 1}
    await seed_canonical_pipeline_run(
        db,
        run_id="history-series",
        archive={"run_id": "history-series", "title": series.title, "candidates": []},
        media_type="series",
        series_id=series.id,
    )
    await seed_canonical_pipeline_run(
        db,
        run_id="history-season",
        archive={"run_id": "history-season", "title": series.title, "candidates": []},
        media_type="season",
        series_id=series.id,
        season_id=season.id,
    )
    episode_event = LetterboxEvent(
        media_type="episode",
        episode_id=episode.id,
        subject_snapshot=snapshot,
        action="detect",
        source="api",
    )
    db.add(episode_event)
    await db.commit()
    episode_event_id = episode_event.id

    await db.delete(series)
    await db.commit()
    db.expire_all()

    surviving_series_run = await db.get(PipelineRun, "history-series")
    surviving_season_run = await db.get(PipelineRun, "history-season")
    surviving_episode_event = await db.get(LetterboxEvent, episode_event_id)
    assert surviving_series_run is not None and surviving_series_run.series_id is None
    assert surviving_season_run is not None and surviving_season_run.season_id is None
    assert surviving_episode_event is not None and surviving_episode_event.episode_id is None
    assert surviving_episode_event.subject_snapshot["title"] == "Snapshot Show"


async def test_log_and_artifact_storage_keys_are_confined(db):
    job = _job("confined-job")
    db.add(job)
    await db.flush()
    attempt = JobAttempt(job_id=job.id, number=1, fence_token=1, phase="running")
    db.add(attempt)
    await db.flush()
    db.add(
        JobLog(
            job_id=job.id,
            attempt_id=attempt.id,
            segment=0,
            storage_key="/etc/passwd",
        )
    )
    with pytest.raises(IntegrityError):
        await db.commit()


def test_worker_nodes_are_observation_only():
    columns = set(WorkerNode.__table__.columns.keys())
    assert not columns & {"job_id", "claim", "lease_expires_at", "heartbeat_at", "reservation"}
    node = WorkerNode(
        id="worker:test",
        readiness="ready",
        capabilities={"roles": ["worker"]},
        last_seen_at=datetime.now(UTC),
    )
    assert node.readiness == "ready"


def test_historical_subject_foreign_keys_null_instead_of_deleting_evidence():
    historical_subjects = {
        ArtworkEvent: {"movie_id", "series_id", "season_id"},
        LetterboxEvent: {"movie_id", "episode_id"},
        MediaOperationDetail: {"media_file_id"},
        PipelineRun: {"movie_id", "series_id", "season_id"},
    }

    for model, subject_columns in historical_subjects.items():
        subject_foreign_keys = {
            foreign_key
            for column_name in subject_columns
            for foreign_key in model.__table__.columns[column_name].foreign_keys
        }
        assert subject_foreign_keys
        assert {foreign_key.ondelete for foreign_key in subject_foreign_keys} == {"SET NULL"}
