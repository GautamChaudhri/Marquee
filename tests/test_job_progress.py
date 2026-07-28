from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select, update

from marquee.core.jobs.progress import MeasurementMode, ProgressMeasurementUpdate
from marquee.core.jobs.progress_service import (
    ProgressCoalescer,
    ProgressObservation,
    ProgressWriteError,
    ProgressWriter,
)
from marquee.models import Job, JobAttempt, JobEvent


def _batch_snapshot(*, sealed: bool = True) -> dict:
    return {
        "version": 1,
        "kind": "aggregate_batch",
        "display_id": "batch:test",
        "display_name": "Synthetic batch",
        "batch_type": "poster_pipeline_batch",
        "child_count": 2,
        "sealed": sealed,
    }


def _movie_snapshot(index: int) -> dict:
    return {
        "version": 1,
        "kind": "movie",
        "display_id": f"movie:{index}",
        "display_name": f"Movie {index}",
        "movie_id": index,
        "title": f"Movie {index}",
    }


async def _running_batch(db, label: str):
    job_id = uuid.uuid5(uuid.NAMESPACE_URL, f"progress:{label}").hex
    job = Job(
        id=job_id,
        type="poster_pipeline_batch",
        payload_version=1,
        request={"version": 1, "intent": "test"},
        phase="running",
        root_id=job_id,
        subject_kind="aggregate_batch",
        subject_reference=f"batch:{label}",
        subject_snapshot=_batch_snapshot(),
        fence_token=1,
    )
    db.add(job)
    await db.flush()
    attempt = JobAttempt(
        job_id=job_id,
        number=1,
        fence_token=1,
        phase="running",
        started_at=datetime.now(UTC),
    )
    db.add(attempt)
    await db.flush()
    job.current_attempt_id = attempt.id
    await db.commit()
    return job_id, attempt.id


def _observation(completed: float, *, ordinal: int = 1) -> ProgressObservation:
    return ProgressObservation(
        stage_key="execute",
        overall=ProgressMeasurementUpdate(
            scope_id="sealed:4",
            mode=MeasurementMode.DETERMINATE,
            unit="children",
            completed=completed,
            total=4,
        ),
        current=ProgressMeasurementUpdate(scope_id="child:current", mode=MeasurementMode.NONE),
        producer_ordinal=ordinal,
    )


async def test_progress_writer_owns_sequence_percent_and_event(db) -> None:
    job_id, attempt_id = await _running_batch(db, "writer")
    writer = ProgressWriter()
    progress = await writer.write(
        job_id=job_id,
        attempt_id=attempt_id,
        fence_token=1,
        observation=_observation(1),
    )
    assert progress.sequence == 1
    assert progress.overall.percent == 25
    db.expire_all()
    row = await db.get(Job, job_id)
    assert row.progress_sequence == 1
    assert row.progress["overall"]["percent"] == 25
    event = (
        await db.scalars(
            select(JobEvent).where(
                JobEvent.job_id == job_id, JobEvent.event_key == "progress.updated"
            )
        )
    ).one()
    assert event.detail == {"progress_sequence": 1, "_canonical_version": 1}


async def test_progress_rejects_duplicate_regression_wrong_fence_and_terminal(db) -> None:
    job_id, attempt_id = await _running_batch(db, "reject")
    writer = ProgressWriter()
    await writer.write(
        job_id=job_id,
        attempt_id=attempt_id,
        fence_token=1,
        observation=_observation(2),
    )
    with pytest.raises(ProgressWriteError, match="stale or duplicate"):
        await writer.write(
            job_id=job_id,
            attempt_id=attempt_id,
            fence_token=1,
            observation=_observation(3),
        )
    with pytest.raises(ProgressWriteError, match="regress"):
        await writer.write(
            job_id=job_id,
            attempt_id=attempt_id,
            fence_token=1,
            observation=_observation(1, ordinal=2),
        )
    with pytest.raises(ProgressWriteError, match="stale"):
        await writer.write(
            job_id=job_id,
            attempt_id=attempt_id,
            fence_token=2,
            observation=_observation(3, ordinal=3),
        )
    await db.execute(
        update(Job)
        .where(Job.id == job_id)
        .values(phase="terminal", outcome="cancelled", terminal_at=datetime.now(UTC))
    )
    await db.commit()
    with pytest.raises(ProgressWriteError, match="terminal"):
        await writer.write(
            job_id=job_id,
            attempt_id=attempt_id,
            fence_token=1,
            observation=_observation(3, ordinal=3),
        )


async def test_coalescer_keeps_one_latest_tick_and_forces_shutdown_flush(db) -> None:
    job_id, attempt_id = await _running_batch(db, "coalesce")
    coalescer = ProgressCoalescer(
        writer=ProgressWriter(),
        job_id=job_id,
        attempt_id=attempt_id,
        fence_token=1,
        cadence_seconds=60,
        max_staleness_seconds=120,
        meaningful_delta_percent=50,
    )
    assert await coalescer.submit(_observation(1)) is not None
    assert await coalescer.submit(_observation(2, ordinal=2)) is None
    final = await coalescer.close()
    assert final is not None and final.sequence == 2 and final.overall.percent == 50
