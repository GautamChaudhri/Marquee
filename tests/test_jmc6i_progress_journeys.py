"""JMC6I I3 — durable progress journeys across restart, presenter, and terminal states.

The canonical database snapshot is authoritative: a fresh session (the API-restart
equivalent), the shared presenter projection, and terminal failure must all retain
the same job identity and its last real measurements. Nothing here invents a
percentage — every value asserted below was produced by validated runner frames.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from marquee.core.jobs.presenters.base import load_context, present_compact_progress
from marquee.core.jobs.progress import JobProgress, MeasurementMode, ProgressFreshness
from marquee.core.jobs.progress_service import progress_writer
from marquee.core.jobs.runner_progress import RunnerProgressBridge
from marquee.database import _get_session_factory
from marquee.models import Job
from tests.test_jmc6i_runner_progress import _ML_SUBJECT, _progress_context

_FRAMES = (
    {"v": 1, "type": "progress", "stage": "starting", "state": "start", "cursor": 1},
    {"v": 1, "type": "progress", "stage": "clip", "state": "progress",
     "done": 2, "total": 3, "cursor": 2},
    {"v": 1, "type": "progress", "stage": "calibration", "state": "progress",
     "done": 3, "total": 3, "cursor": 3},
)
_STAGE_MAP = {"starting": "collecting", "clip": "features", "calibration": "evaluating"}


async def _run_bridge_frames(context, *, handler_stage: bool = True) -> None:
    bridge = RunnerProgressBridge(context.progress, stage_map=_STAGE_MAP)
    for frame in _FRAMES:
        await bridge.on_frame(frame)
    if handler_stage:
        await bridge.stage("validating")
    await bridge.close()


@pytest.mark.asyncio
async def test_snapshot_survives_fresh_session_and_presenter_projection(db, data_dir) -> None:
    """Refresh/API-restart equivalence: a brand-new session reads the same job and
    the same real measurements, and the shared presenter projects them typed."""
    context = await _progress_context(
        db,
        data_dir,
        job_type="taste_rebuild",
        request={"source": "training_dir", "library": "movies", "expected_generation": 0,
                 "seed": 0},
        feature_area="ml_taste",
        subject=_ML_SUBJECT,
    )
    await _run_bridge_frames(context)

    # A completely fresh session factory models an API restart / page refresh.
    async with _get_session_factory()() as session:
        job = await session.scalar(
            select(Job).where(Job.id == context.delivery.canonical_job_id)
        )
        assert job is not None and job.progress is not None
        stored = JobProgress.model_validate(job.progress)
        assert stored.job_id == context.delivery.canonical_job_id
        assert stored.fence_token == context.attempt.fence_token
        assert stored.stage.key == "validating"
        assert stored.overall.mode == MeasurementMode.DETERMINATE
        assert stored.overall.completed == 6 and stored.overall.total == 8
        assert stored.current_subject is not None
        assert stored.current_subject.display_id == _ML_SUBJECT["display_id"]

        presenter_context = load_context(
            job=job,
            definition=context.definition,
            live={},
        )
        compact = present_compact_progress(presenter_context)
    assert compact is not None
    assert compact.overall is not None and compact.overall.percent == 75.0
    assert compact.stage_key == "validating"
    assert compact.sequence == stored.sequence


@pytest.mark.asyncio
async def test_terminal_failure_retains_last_measured_values(db, data_dir) -> None:
    """A failed job keeps its last real measurements (tone changes, values stay)."""
    context = await _progress_context(
        db,
        data_dir,
        job_type="taste_rebuild",
        request={"source": "training_dir", "library": "movies", "expected_generation": 0,
                 "seed": 0},
        feature_area="ml_taste",
        subject=_ML_SUBJECT,
    )
    await _run_bridge_frames(context, handler_stage=False)

    factory = _get_session_factory()
    async with factory() as session, session.begin():
        job = await session.scalar(
            select(Job).where(Job.id == context.delivery.canonical_job_id).with_for_update()
        )
        assert job is not None
        await progress_writer.terminalize(
            session, job, outcome="failed", occurred_at=datetime.now(UTC)
        )

    async with factory() as session:
        job = await session.scalar(
            select(Job).where(Job.id == context.delivery.canonical_job_id)
        )
        assert job is not None and job.progress is not None
        stored = JobProgress.model_validate(job.progress)
    assert stored.freshness == ProgressFreshness.TERMINAL
    assert stored.overall.completed == 5 and stored.overall.total == 8, (
        "failure must retain the last measured values, not jump to completion"
    )
    assert stored.current.mode == MeasurementMode.DETERMINATE
    assert stored.current.completed == 3 and stored.current.total == 3
