"""JMC6I I2 — independent typed progress scopes and the bounded runner bridge.

Certifies the extended ``ExecutionProgress`` observation API and the typed runner
progress-frame model: legitimate mode transitions persist through epoch-scoped
ids (the previously swallowed writes), overall stays monotonic, the current scope
resets only with a new scope key, invalid frames/observations degrade without
failing the operation, survivor counts ride as bounded metrics, and
high-frequency samples coalesce while stage boundaries stay durable.
"""

from __future__ import annotations

import asyncio

import pytest

from marquee.core.jobs.execution_progress import ScopeObservation
from marquee.core.jobs.progress import MeasurementMode
from marquee.core.jobs.progress_service import progress_writer
from marquee.core.jobs.runner_progress import (
    RunnerFrameError,
    RunnerProgressBridge,
    parse_runner_progress_frame,
)
from tests.test_jmc6i_runner_progress import (
    _ML_SUBJECT,
    _progress_context,
    _spy_durable_writes,
)


async def _ml_context(db, data_dir):
    return await _progress_context(
        db,
        data_dir,
        job_type="taste_rebuild",
        request={"source": "training_dir", "library": "movies", "expected_generation": 0,
                 "seed": 0},
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

    await progress.observe(
        "features", current=ScopeObservation.indeterminate(scope_key="features")
    )
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
    await progress.observe(
        "training", current=ScopeObservation.indeterminate(scope_key="training")
    )
    assert [snapshot.metrics.items_survived for snapshot in accepted] == [9, 9]


async def test_write_failure_degrades_without_failing_operation(
    db, data_dir, monkeypatch
) -> None:
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
    good = {"v": 1, "type": "progress", "stage": "ocr", "state": "progress",
            "done": 5, "total": 12}
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
        {"v": 1, "type": "progress", "stage": "ocr", "total": 10 ** 12},
        {"v": 1, "type": "progress", "stage": "ocr", "survivors": True},
        {"v": 1, "type": "progress", "stage": "x" * 500},
        {"v": 1, "type": "progress", "stage": "ocr", "payload": "not-allowlisted"},
    ]
    for frame in bad_frames:
        with pytest.raises(RunnerFrameError):
            parse_runner_progress_frame(frame)


async def test_bridge_maps_counts_and_rejects_unknown_and_stale(
    db, data_dir, monkeypatch
) -> None:
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
        {"v": 1, "type": "progress", "stage": "clip", "state": "progress",
         "done": 2, "total": 3, "cursor": 2}
    )
    # Unknown runner stage and an out-of-order cursor degrade without writing.
    await bridge.on_frame(
        {"v": 1, "type": "progress", "stage": "mystery", "state": "start", "cursor": 3}
    )
    await bridge.on_frame(
        {"v": 1, "type": "progress", "stage": "clip", "state": "progress",
         "done": 3, "total": 3, "cursor": 2}
    )
    assert bridge.degraded_frames == 2
    await bridge.on_frame(
        {"v": 1, "type": "progress", "stage": "calibration", "state": "start",
         "done": 0, "total": 5, "cursor": 4}
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
        if snapshot.current.mode == MeasurementMode.DETERMINATE
        and snapshot.current.total == 3
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
        {"v": 1, "type": "progress", "stage": "clip", "state": "progress",
         "done": 1, "total": 1000}
    )
    baseline = len(accepted)
    for done in (2, 3):
        await bridge.on_frame(
            {"v": 1, "type": "progress", "stage": "clip", "state": "progress",
             "done": done, "total": 1000}
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
