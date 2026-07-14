from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select, update

from marquee.core.jobs.parent_progress import project_parent_progress
from marquee.core.jobs.progress import MeasurementMode, ProgressMeasurementUpdate
from marquee.core.jobs.progress_adapters import (
    FFmpegProgressAdapter,
    IndeterminateProgressAdapter,
    MkvmergeProgressAdapter,
)
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
        "batch_type": "dovi_analyze_batch",
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
    job_id = uuid.uuid5(uuid.NAMESPACE_URL, f"jmc3b-progress:{label}").hex
    job = Job(
        id=job_id,
        type="dovi_analyze_batch",
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
        current=ProgressMeasurementUpdate(
            scope_id="child:current", mode=MeasurementMode.NONE
        ),
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


def test_ffmpeg_adapter_handles_partial_invalid_and_regressive_packets() -> None:
    adapter = FFmpegProgressAdapter(duration_seconds=10)
    assert adapter.feed("out_time_us=250") == ()
    first = adapter.feed("0000\nspeed=2.0x\nfps=30\nprogress=continue\n")[0]
    assert (first.mode, first.completed, first.total, first.speed) == (
        "determinate",
        2.5,
        10,
        2.0,
    )
    assert adapter.feed("out_time_us=1000000\nprogress=continue\n") == ()
    unknown = FFmpegProgressAdapter(duration_seconds=float("nan"))
    sample = unknown.feed("out_time_us=1000000\nprogress=end\n")[0]
    assert sample.mode == "indeterminate" and sample.finished is True


def test_mkvmerge_and_indeterminate_adapters_never_invent_progress() -> None:
    adapter = MkvmergeProgressAdapter()
    assert adapter.feed("#GUI#progress 2") == ()
    samples = adapter.feed("5%\nordinary 99%\n#GUI#warning warning\n")
    assert samples[0].completed == 25 and samples[0].total == 100
    assert samples[1].warning is True
    assert adapter.feed("#GUI#progress 10%\n") == ()
    exit_sample = adapter.feed("#GUI#exit 2\n")[0]
    assert exit_sample.finished and exit_sample.error and exit_sample.exit_code == 2
    opaque = IndeterminateProgressAdapter()
    assert opaque.tick().mode == opaque.tick().mode == "indeterminate"


async def test_sealed_parent_projection_is_bounded_and_outcome_aware(db) -> None:
    parent_id, _attempt_id = await _running_batch(db, "parent")
    now = datetime.now(UTC)
    children = [
        Job(
            id=uuid.uuid4().hex,
            type="dovi_analyze",
            payload_version=1,
            request={"version": 1, "intent": "test"},
            phase="terminal" if index == 0 else "running",
            outcome="failed" if index == 0 else None,
            terminal_at=now if index == 0 else None,
            parent_id=parent_id,
            root_id=parent_id,
            subject_kind="movie",
            subject_reference=f"movie:{index}",
            subject_snapshot=_movie_snapshot(index + 1),
            current_stage="execute" if index else None,
        )
        for index in range(2)
    ]
    db.add_all(children)
    await db.commit()
    progress = await project_parent_progress(
        parent_id=parent_id, fence_token=1, writer=ProgressWriter()
    )
    assert progress.overall.completed == 1
    assert progress.overall.total == 2
    assert progress.failure_count == 1
    assert progress.current_subject.display_name == "Movie 2"
