"""Runner progress: typed frames, independent scopes, and durable journeys.

Real runner measurements must reach durable, fenced, typed JobProgress snapshots — a
determinate current scope with true counts, survivor counts as bounded metrics, and a
monotonic overall scope — with no fabricated percentages. Covers the bridge that parses
runner frames, the scope/epoch semantics underneath it, and the journey a snapshot takes
across a fresh session, the presenter projection, and terminal failure."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import select

from marquee.core.jobs.execution_io import ExecutionIO
from marquee.core.jobs.execution_progress import ExecutionProgress, ScopeObservation
from marquee.core.jobs.handlers_ml import execute_taste_rebuild
from marquee.core.jobs.handlers_posters import execute_poster_analysis
from marquee.core.jobs.manifest import JOB_DEFINITION_REGISTRY
from marquee.core.jobs.presenters.base import load_context, present_compact_progress
from marquee.core.jobs.progress import JobProgress, MeasurementMode, ProgressFreshness
from marquee.core.jobs.progress_service import progress_writer
from marquee.core.jobs.runner_progress import (
    RunnerFrameError,
    RunnerProgressBridge,
    parse_runner_progress_frame,
)
from marquee.core.jobs.workspaces import AttemptWorkspaceManager
from marquee.database import _get_session_factory
from marquee.models import Job, Movie
from marquee.models.job import JobAttempt
from tests.support.process_harness import Fence

_FENCE = 11


async def _progress_context(
    db,
    data_dir,
    *,
    job_type: str,
    request: dict,
    feature_area: str,
    subject: dict,
):
    """The product-effect harness surface, but with a *real* ExecutionProgress."""
    job_id = uuid4().hex
    db.add(
        Job(
            id=job_id,
            type=job_type,
            payload_version=1,
            request=request,
            phase="running",
            desired_state="run",
            fence_token=_FENCE,
            root_id=job_id,
            trigger_kind="manual",
            feature_area=feature_area,
            presentation_family=feature_area,
            subject_kind=subject["kind"],
            subject_reference=subject["display_id"],
            subject_snapshot=subject,
        )
    )
    await db.flush()
    attempt = JobAttempt(
        job_id=job_id,
        number=1,
        fence_token=_FENCE,
        phase="running",
        started_at=datetime.now(UTC),
    )
    db.add(attempt)
    await db.flush()
    job = await db.get(Job, job_id)
    assert job is not None
    job.current_attempt_id = attempt.id
    await db.commit()

    workspace = AttemptWorkspaceManager.for_data_dir(str(data_dir)).create(
        job_id=job_id, attempt_id=attempt.id, fence_token=_FENCE
    )
    fence = Fence()

    async def owns_fence() -> bool:
        return await fence.owns_current_attempt(None)

    definition = JOB_DEFINITION_REGISTRY.get(job_type)
    progress = ExecutionProgress.create(
        job_id=job_id,
        attempt_id=attempt.id,
        fence_token=_FENCE,
        definition=definition,
        subject=subject,
    )
    return SimpleNamespace(
        delivery=SimpleNamespace(canonical_job_id=job_id),
        attempt=SimpleNamespace(attempt_id=attempt.id, fence_token=_FENCE),
        request=request,
        configuration={},
        subject=subject,
        definition=definition,
        cancellation=SimpleNamespace(cancel_called=False, is_cancelled=lambda: False),
        writer=fence,
        io=ExecutionIO(cancelled=lambda: False, owns_fence=owns_fence),
        workspace=workspace,
        process_launcher=None,
        progress=progress,
        session_factory=_get_session_factory(),
    )


def _spy_durable_writes(monkeypatch) -> list[JobProgress]:
    """Record every *accepted* durable progress snapshot without changing behavior."""
    accepted: list[JobProgress] = []
    original = progress_writer.write

    async def recording_write(**kwargs):
        result = await original(**kwargs)
        accepted.append(result)
        return result

    monkeypatch.setattr(progress_writer, "write", recording_write)
    return accepted


def _movie_subject(movie_id: int) -> dict:
    return {
        "version": 1,
        "kind": "movie",
        "display_id": f"movie:{movie_id}",
        "display_name": "Blade Runner 2049",
        "movie_id": movie_id,
        "title": "Blade Runner 2049",
    }


_ML_SUBJECT = {
    "version": 1,
    "kind": "model_profile_training",
    "display_id": "taste_profile:movies",
    "display_name": "Movie taste profile",
    "subject_type": "profile",
    "name": "taste_profile:movies",
}


@pytest.mark.asyncio
async def test_poster_runner_counts_reach_durable_progress(db, data_dir, monkeypatch) -> None:
    """§6.1: real poster done/total and survivor counts must reach typed durable
    snapshots — not collapse to bare stage-start labels."""
    from marquee.core.jobs.internal_runner_host import RunnerFile, RunnerOutcome

    movie = Movie(title="Blade Runner 2049", year=2017, folder_path=str(data_dir), tmdb_id=1)
    db.add(movie)
    await db.flush()
    context = await _progress_context(
        db,
        data_dir,
        job_type="poster_pipeline",
        request={
            "movie_id": movie.id,
            "title": "Blade Runner 2049",
            "source_descriptors": [{"provider": "tmdb", "reference": "111"}],
        },
        feature_area="ai_posters",
        subject=_movie_subject(movie.id),
    )
    workspace_dir = (
        context.workspace.directory.root.resolved() / context.workspace.directory.key.value
    )
    accepted = _spy_durable_writes(monkeypatch)

    async def fake_run(launcher, *, operation, manifest, on_progress=None, **kwargs):
        # The exact measurement sequence the real contained pipeline emits.
        assert on_progress is not None
        await on_progress(
            {"v": 1, "type": "progress", "stage": "fetch", "state": "start", "total": 6}
        )
        await on_progress(
            {"v": 1, "type": "progress", "stage": "ocr", "state": "start", "total": 12}
        )
        await on_progress(
            {
                "v": 1,
                "type": "progress",
                "stage": "ocr",
                "state": "progress",
                "done": 5,
                "total": 12,
            }
        )
        await on_progress(
            {"v": 1, "type": "progress", "stage": "ocr", "state": "end", "survivors": 9}
        )
        await on_progress(
            {"v": 1, "type": "progress", "stage": "rank", "state": "start", "total": 9}
        )
        (workspace_dir / "run.json").write_text('{"run_id": "progressrun01", "candidates": []}')
        return RunnerOutcome(
            outcome="succeeded",
            summary={
                "pipeline_status": "completed",
                "run_id": "progressrun01",
                "counts": {"posters_found": 6, "ranked": 2},
                "recommendation": None,
                "scorer_name": "weighted",
                "source_count": 6,
                "candidate_count": 2,
                "candidate_files": {},
            },
            files=(RunnerFile("run.json", "0" * 64, 2),),
            ready=True,
            exit_code=0,
        )

    monkeypatch.setattr("marquee.core.jobs.internal_runner_host.run_internal_operation", fake_run)
    result = await execute_poster_analysis(context)
    assert result["outcome"] in {"succeeded", "review_required", "no_change"}

    determinate_current = [
        snapshot
        for snapshot in accepted
        if snapshot.current.mode == MeasurementMode.DETERMINATE
        and snapshot.current.completed == 5
        and snapshot.current.total == 12
    ]
    assert determinate_current, (
        "the runner's real done=5/total=12 measurement must reach a durable "
        "determinate current scope; stage-start labels alone are not progress"
    )
    survivor_writes = [snapshot for snapshot in accepted if snapshot.metrics.items_survived == 9]
    assert survivor_writes, (
        "the runner's survivor count must reach durable snapshots as a bounded metric"
    )
    policy = context.definition.progress_policy
    for snapshot in accepted:
        assert snapshot.stage.key in policy.stage_keys
    overall_percents = [
        snapshot.overall.percent for snapshot in accepted if snapshot.overall.percent is not None
    ]
    assert overall_percents == sorted(overall_percents), (
        "overall progress must remain monotonic within the attempt"
    )

    async with _get_session_factory()() as session:
        job = await session.scalar(select(Job).where(Job.id == context.delivery.canonical_job_id))
        assert job is not None and job.progress is not None
        stored = JobProgress.model_validate(job.progress)
    assert stored.fence_token == _FENCE


@pytest.mark.asyncio
async def test_taste_rebuild_trainer_counts_reach_durable_progress(
    db, data_dir, monkeypatch
) -> None:
    """§6.2: taste rebuild must attach the shared progress adapter and forward the
    trainer's processed/total measurements to durable typed snapshots."""
    import numpy as np

    from marquee.core.jobs.internal_runner_host import RunnerFile, RunnerOutcome

    context = await _progress_context(
        db,
        data_dir,
        job_type="taste_rebuild",
        request={
            "source": "training_dir",
            "library": "movies",
            "expected_generation": 0,
            "seed": 0,
        },
        feature_area="ml_taste",
        subject=_ML_SUBJECT,
    )
    workspace_dir = (
        context.workspace.directory.root.resolved() / context.workspace.directory.key.value
    )
    accepted = _spy_durable_writes(monkeypatch)

    def _write_profile(path) -> None:
        embeddings = np.zeros((2, 512), dtype=np.float32)
        embeddings[0, 0] = 0.1
        embeddings[1, 0] = 0.2
        np.savez(
            path,
            model_name="clip-vit-b-32",
            embeddings=embeddings,
            centroid_emb=embeddings.mean(axis=0),
            poster_names=np.array(["a.jpg", "b.jpg"]),
            asset_kinds=np.array(["movie", "movie"]),
        )

    async def fake_run(launcher, *, operation, manifest, on_progress=None, **kwargs):
        assert on_progress is not None, (
            "taste_rebuild must attach the shared runner progress adapter"
        )
        # The trainer's real numeric emission (CLIP embedding then calibration counts).
        await on_progress(
            {
                "v": 1,
                "type": "progress",
                "stage": "clip",
                "state": "progress",
                "done": 2,
                "total": 3,
            }
        )
        await on_progress(
            {
                "v": 1,
                "type": "progress",
                "stage": "calibration",
                "state": "progress",
                "done": 1,
                "total": 3,
            }
        )
        _write_profile(workspace_dir / "profile.npz")
        return RunnerOutcome(
            outcome="succeeded",
            summary={"family": "taste_profile", "library": "movies", "exemplars": 2},
            files=(RunnerFile("profile.npz", "0" * 64, 1),),
            ready=True,
            exit_code=0,
        )

    monkeypatch.setattr("marquee.core.jobs.internal_runner_host.run_internal_operation", fake_run)
    result = await execute_taste_rebuild(context)
    assert result["outcome"] == "succeeded"

    determinate_current = [
        snapshot
        for snapshot in accepted
        if snapshot.current.mode == MeasurementMode.DETERMINATE
        and snapshot.current.completed in {1, 2}
        and snapshot.current.total == 3
    ]
    assert determinate_current, (
        "the trainer's processed/total measurements must reach a durable determinate "
        "current scope; today the handler attaches no progress callback at all"
    )
    policy = context.definition.progress_policy
    for snapshot in accepted:
        assert snapshot.stage.key in policy.stage_keys


async def _ml_context(db, data_dir):
    return await _progress_context(
        db,
        data_dir,
        job_type="taste_rebuild",
        request={
            "source": "training_dir",
            "library": "movies",
            "expected_generation": 0,
            "seed": 0,
        },
        feature_area="ml_taste",
        subject=_ML_SUBJECT,
    )


# --------------------------------------------------------------------------- #
# Facade: independent scopes, epochs, monotonicity, resets, degradation
# --------------------------------------------------------------------------- #


async def test_mode_transition_persists_through_epoch_scopes(db, data_dir, monkeypatch) -> None:
    """The I0-reproduced defect: an indeterminate first write no longer swallows
    every later determinate measurement on the fixed overall scope."""
    context = await _ml_context(db, data_dir)
    accepted = _spy_durable_writes(monkeypatch)
    progress = context.progress

    await progress.stage("loading")  # the delivery-style indeterminate first write
    await progress.observe(
        "features",
        overall=ScopeObservation.determinate(completed=2, total=8, unit="stages"),
        current=ScopeObservation.determinate(completed=1, total=3, scope_key="features"),
    )
    assert progress.degraded_observations == 0
    assert len(accepted) == 2
    final = accepted[-1]
    assert final.overall.mode == MeasurementMode.DETERMINATE
    assert final.overall.completed == 2 and final.overall.total == 8
    assert final.overall.scope_id != accepted[0].overall.scope_id, (
        "a legitimate mode transition must allocate a new overall scope epoch"
    )
    assert final.current.mode == MeasurementMode.DETERMINATE
    assert final.current.percent == 33.3333


async def test_overall_is_monotonic_and_regressions_degrade(db, data_dir, monkeypatch) -> None:
    context = await _ml_context(db, data_dir)
    accepted = _spy_durable_writes(monkeypatch)
    progress = context.progress

    await progress.observe(
        "features",
        overall=ScopeObservation.determinate(completed=3, total=8, unit="stages"),
        current=ScopeObservation.indeterminate(scope_key="features"),
    )
    await progress.observe(
        "features",
        overall=ScopeObservation.determinate(completed=2, total=8, unit="stages"),
        current=ScopeObservation.indeterminate(scope_key="features"),
    )
    assert progress.degraded_observations == 1, "an overall regression must degrade"
    assert len(accepted) == 1
    await progress.observe(
        "training",
        overall=ScopeObservation.determinate(completed=4, total=8, unit="stages"),
        current=ScopeObservation.indeterminate(scope_key="training"),
    )
    assert [snapshot.overall.completed for snapshot in accepted] == [3, 4]
    percents = [snapshot.overall.percent for snapshot in accepted]
    assert percents == sorted(percents)


async def test_current_resets_only_with_new_scope_key(db, data_dir, monkeypatch) -> None:
    context = await _ml_context(db, data_dir)
    accepted = _spy_durable_writes(monkeypatch)
    progress = context.progress

    await progress.observe(
        "features",
        current=ScopeObservation.determinate(completed=2, total=3, scope_key="features"),
    )
    # Regression inside the same scope key must degrade, not reset.
    await progress.observe(
        "features",
        current=ScopeObservation.determinate(completed=1, total=3, scope_key="features"),
    )
    assert progress.degraded_observations == 1
    # A new scope key is a legitimate reset.
    await progress.observe(
        "evaluating",
        current=ScopeObservation.determinate(completed=0.0, total=5, scope_key="evaluating"),
    )
    assert len(accepted) == 2
    assert accepted[0].current.scope_id != accepted[1].current.scope_id
    assert accepted[1].current.completed == 0.0 and accepted[1].current.total == 5


async def test_current_mode_change_same_key_allocates_epoch(db, data_dir, monkeypatch) -> None:
    context = await _ml_context(db, data_dir)
    accepted = _spy_durable_writes(monkeypatch)
    progress = context.progress

    await progress.observe("features", current=ScopeObservation.indeterminate(scope_key="features"))
    await progress.observe(
        "features",
        current=ScopeObservation.determinate(completed=1, total=4, scope_key="features"),
    )
    assert progress.degraded_observations == 0
    assert len(accepted) == 2
    assert accepted[0].current.scope_id != accepted[1].current.scope_id
    assert accepted[1].current.mode == MeasurementMode.DETERMINATE


async def test_survivor_metrics_persist_and_carry_forward(db, data_dir, monkeypatch) -> None:
    context = await _ml_context(db, data_dir)
    accepted = _spy_durable_writes(monkeypatch)
    progress = context.progress

    await progress.observe(
        "features",
        current=ScopeObservation.indeterminate(scope_key="features"),
        survivors=9,
    )
    await progress.observe("training", current=ScopeObservation.indeterminate(scope_key="training"))
    assert [snapshot.metrics.items_survived for snapshot in accepted] == [9, 9]


async def test_write_failure_degrades_without_failing_operation(db, data_dir, monkeypatch) -> None:
    context = await _ml_context(db, data_dir)
    progress = context.progress

    async def broken_write(**_kwargs):
        raise RuntimeError("durable progress store unavailable")

    monkeypatch.setattr(progress_writer, "write", broken_write)
    await progress.observe(
        "features",
        current=ScopeObservation.determinate(completed=1, total=2, scope_key="features"),
    )
    assert progress.degraded_observations == 1, (
        "publication failure must be observable degradation, never an exception"
    )


async def test_high_frequency_samples_coalesce_and_flush(db, data_dir, monkeypatch) -> None:
    context = await _ml_context(db, data_dir)
    accepted = _spy_durable_writes(monkeypatch)
    progress = context.progress

    await progress.observe(
        "features",
        current=ScopeObservation.determinate(completed=1, total=1000, scope_key="features"),
        durable=True,
    )
    # Rapid same-scope samples below the meaningful delta stay pending.
    for completed in (2, 3, 4):
        await progress.observe(
            "features",
            current=ScopeObservation.determinate(
                completed=completed, total=1000, scope_key="features"
            ),
            durable=False,
        )
    assert len(accepted) == 1
    await progress.flush_pending()
    assert len(accepted) == 2
    assert accepted[-1].current.completed == 4, "the flushed tail must be the latest sample"


# --------------------------------------------------------------------------- #
# Typed runner frames
# --------------------------------------------------------------------------- #


def test_parse_rejects_malformed_frames() -> None:
    good = {"v": 1, "type": "progress", "stage": "ocr", "state": "progress", "done": 5, "total": 12}
    parsed = parse_runner_progress_frame(good)
    assert parsed.done == 5 and parsed.total == 12
    bad_frames = [
        {"v": 1, "type": "result"},
        {"v": 1, "type": "progress"},
        {"v": 1, "type": "progress", "stage": "ocr", "state": "exploded"},
        {"v": 1, "type": "progress", "stage": "ocr", "done": 13, "total": 12},
        {"v": 1, "type": "progress", "stage": "ocr", "done": -1},
        {"v": 1, "type": "progress", "stage": "ocr", "done": float("nan")},
        {"v": 1, "type": "progress", "stage": "ocr", "done": float("inf")},
        {"v": 1, "type": "progress", "stage": "ocr", "total": 10**12},
        {"v": 1, "type": "progress", "stage": "ocr", "survivors": True},
        {"v": 1, "type": "progress", "stage": "x" * 500},
        {"v": 1, "type": "progress", "stage": "ocr", "payload": "not-allowlisted"},
    ]
    for frame in bad_frames:
        with pytest.raises(RunnerFrameError):
            parse_runner_progress_frame(frame)


async def test_bridge_maps_counts_and_rejects_unknown_and_stale(db, data_dir, monkeypatch) -> None:
    context = await _ml_context(db, data_dir)
    accepted = _spy_durable_writes(monkeypatch)
    bridge = RunnerProgressBridge(
        context.progress,
        stage_map={"clip": "features", "calibration": "evaluating", "saving": "training"},
    )

    await bridge.on_frame(
        {"v": 1, "type": "progress", "stage": "clip", "state": "start", "cursor": 1}
    )
    await bridge.on_frame(
        {
            "v": 1,
            "type": "progress",
            "stage": "clip",
            "state": "progress",
            "done": 2,
            "total": 3,
            "cursor": 2,
        }
    )
    # Unknown runner stage and an out-of-order cursor degrade without writing.
    await bridge.on_frame(
        {"v": 1, "type": "progress", "stage": "mystery", "state": "start", "cursor": 3}
    )
    await bridge.on_frame(
        {
            "v": 1,
            "type": "progress",
            "stage": "clip",
            "state": "progress",
            "done": 3,
            "total": 3,
            "cursor": 2,
        }
    )
    assert bridge.degraded_frames == 2
    await bridge.on_frame(
        {
            "v": 1,
            "type": "progress",
            "stage": "calibration",
            "state": "start",
            "done": 0,
            "total": 5,
            "cursor": 4,
        }
    )
    # A later runner stage that maps to an *earlier* registered stage cannot
    # regress the overall stage-position measurement.
    await bridge.on_frame(
        {"v": 1, "type": "progress", "stage": "saving", "state": "start", "cursor": 5}
    )
    await bridge.close()

    assert context.progress.degraded_observations == 0
    overall = [(snapshot.overall.completed, snapshot.overall.total) for snapshot in accepted]
    assert all(total == 8 for _completed, total in overall)
    completed_values = [completed for completed, _total in overall]
    assert completed_values == sorted(completed_values), "stage-position overall is monotonic"
    determinate = [
        snapshot
        for snapshot in accepted
        if snapshot.current.mode == MeasurementMode.DETERMINATE and snapshot.current.total == 3
    ]
    assert determinate, "runner done/total must become a determinate current measurement"
    stage_keys = {snapshot.stage.key for snapshot in accepted}
    assert stage_keys <= set(context.definition.progress_policy.stage_keys)


async def test_bridge_coalesces_intra_stage_samples(db, data_dir, monkeypatch) -> None:
    context = await _ml_context(db, data_dir)
    accepted = _spy_durable_writes(monkeypatch)
    bridge = RunnerProgressBridge(context.progress, stage_map={"clip": "features"})

    await bridge.on_frame({"v": 1, "type": "progress", "stage": "clip", "state": "start"})
    # The first measured sample opens a new determinate scope epoch (a forced
    # transition), then sub-delta samples in the same scope coalesce.
    await bridge.on_frame(
        {"v": 1, "type": "progress", "stage": "clip", "state": "progress", "done": 1, "total": 1000}
    )
    baseline = len(accepted)
    for done in (2, 3):
        await bridge.on_frame(
            {
                "v": 1,
                "type": "progress",
                "stage": "clip",
                "state": "progress",
                "done": done,
                "total": 1000,
            }
        )
    assert len(accepted) == baseline, "sub-delta intra-stage samples must coalesce"
    await bridge.on_frame({"v": 1, "type": "progress", "stage": "clip", "state": "end"})
    assert len(accepted) == baseline + 1, "a stage boundary must flush durably"
    assert accepted[-1].current.completed == 3


async def test_stage_compatibility_measurement_now_persists(db, data_dir, monkeypatch) -> None:
    """The legacy one-axis ``stage()`` surface (dovi/generation/re-encode style)
    persists determinate measurements after the delivery-style first write."""
    context = await _ml_context(db, data_dir)
    accepted = _spy_durable_writes(monkeypatch)
    progress = context.progress

    await progress.stage("loading")
    await progress.stage("features", completed=10, total=100)
    await progress.stage("features", completed=60, total=100)
    assert progress.degraded_observations == 0
    assert accepted[-1].overall.mode == MeasurementMode.DETERMINATE
    assert accepted[-1].overall.percent == 60.0
    assert accepted[-1].current.percent == 60.0
    await asyncio.sleep(0)  # let any scheduled coalescer task settle before teardown


_FRAMES = (
    {"v": 1, "type": "progress", "stage": "starting", "state": "start", "cursor": 1},
    {
        "v": 1,
        "type": "progress",
        "stage": "clip",
        "state": "progress",
        "done": 2,
        "total": 3,
        "cursor": 2,
    },
    {
        "v": 1,
        "type": "progress",
        "stage": "calibration",
        "state": "progress",
        "done": 3,
        "total": 3,
        "cursor": 3,
    },
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
        request={
            "source": "training_dir",
            "library": "movies",
            "expected_generation": 0,
            "seed": 0,
        },
        feature_area="ml_taste",
        subject=_ML_SUBJECT,
    )
    await _run_bridge_frames(context)

    # A completely fresh session factory models an API restart / page refresh.
    async with _get_session_factory()() as session:
        job = await session.scalar(select(Job).where(Job.id == context.delivery.canonical_job_id))
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
        request={
            "source": "training_dir",
            "library": "movies",
            "expected_generation": 0,
            "seed": 0,
        },
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
        job = await session.scalar(select(Job).where(Job.id == context.delivery.canonical_job_id))
        assert job is not None and job.progress is not None
        stored = JobProgress.model_validate(job.progress)
    assert stored.freshness == ProgressFreshness.TERMINAL
    assert stored.overall.completed == 5 and stored.overall.total == 8, (
        "failure must retain the last measured values, not jump to completion"
    )
    assert stored.current.mode == MeasurementMode.DETERMINATE
    assert stored.current.completed == 3 and stored.current.total == 3
