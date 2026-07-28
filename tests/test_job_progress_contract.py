from __future__ import annotations

import pytest
from pydantic import ValidationError

from marquee.core.jobs.contracts import ProgressStrategy
from marquee.core.jobs.progress import (
    JobProgress,
    ProgressInvariantError,
    ProgressMeasurement,
    ProgressMeasurementUpdate,
    ProgressMetrics,
    ProgressPolicy,
    ProgressStage,
    StaleProgressSequenceError,
    WrongProgressFenceError,
    terminal_progress,
    validate_progress_transition,
)


def _policy(*, eta: bool = False) -> ProgressPolicy:
    return ProgressPolicy(
        strategy=ProgressStrategy.HYBRID,
        overall_unit="files",
        denominator_source="sealed_files",
        current_unit="seconds",
        aggregation_strategy="nested",
        stages=(("batch_created", "jobs.stage.batch_created"), ("encode", "jobs.stage.encode")),
        tool_adapter="ffmpeg_progress",
        persistence_cadence_seconds=1,
        meaningful_delta_percent=1,
        max_snapshot_staleness_seconds=5,
        eta_capability=eta,
    )


def _progress(
    *, sequence: int = 1, overall: float = 1, current: float = 1, scope: str = "file:1"
) -> JobProgress:
    return JobProgress(
        sequence=sequence,
        job_id="job-1",
        attempt_id=1,
        attempt_number=1,
        fence_token=1,
        headline="Encoding library",
        stage=ProgressStage(key="encode", label_key="jobs.stage.encode"),
        overall=ProgressMeasurement.determinate(
            scope_id="batch:1", unit="files", completed=overall, total=10
        ),
        current=ProgressMeasurement.determinate(
            scope_id=scope, unit="seconds", completed=current, total=100
        ),
    )


def test_percentages_are_server_owned_and_counts_are_validated() -> None:
    assert "percent" not in ProgressMeasurementUpdate.model_fields
    update = ProgressMeasurementUpdate(
        scope_id="batch", mode="determinate", unit="items", completed=1, total=4
    )
    assert update.materialize().percent == 25
    with pytest.raises(ValidationError):
        ProgressMeasurementUpdate(
            scope_id="batch",
            mode="determinate",
            unit="items",
            completed=1,
            total=4,
            percent=25,
        )
    value = ProgressMeasurement.determinate(scope_id="batch", unit="items", completed=1, total=4)
    assert value.percent == 25
    with pytest.raises(ValidationError, match="server-computed"):
        ProgressMeasurement(
            scope_id="batch",
            mode="determinate",
            unit="items",
            completed=1,
            total=4,
            percent=80,
        )
    with pytest.raises(ProgressInvariantError):
        ProgressMeasurement.determinate(scope_id="batch", unit="items", completed=2, total=0)
    with pytest.raises(ProgressInvariantError):
        ProgressMeasurement.determinate(scope_id="batch", unit="items", completed=5, total=4)


def test_overall_is_monotonic_and_current_reset_requires_new_scope() -> None:
    previous = _progress(sequence=1, overall=5, current=80)
    with pytest.raises(ProgressInvariantError, match="overall"):
        validate_progress_transition(
            previous, _progress(sequence=2, overall=4, current=90), _policy()
        )
    with pytest.raises(ProgressInvariantError, match="current"):
        validate_progress_transition(
            previous, _progress(sequence=2, overall=6, current=10), _policy()
        )
    changed = _progress(sequence=2, overall=6, current=10, scope="file:2")
    assert validate_progress_transition(previous, changed, _policy()) == changed


def test_stale_sequence_and_fence_are_distinguishable() -> None:
    previous = _progress(sequence=2)
    with pytest.raises(StaleProgressSequenceError):
        validate_progress_transition(previous, _progress(sequence=2), _policy())
    with pytest.raises(WrongProgressFenceError):
        validate_progress_transition(
            previous, _progress(sequence=3).model_copy(update={"fence_token": 2}), _policy()
        )


def test_eta_requires_policy_denominator_and_credible_rate() -> None:
    update = _progress().model_copy(update={"metrics": ProgressMetrics(eta_seconds=30)})
    with pytest.raises(ProgressInvariantError, match="ETA"):
        validate_progress_transition(None, update, _policy(eta=False))
    with pytest.raises(ProgressInvariantError, match="ETA"):
        validate_progress_transition(None, update, _policy(eta=True))
    credible = update.model_copy(update={"metrics": ProgressMetrics(eta_seconds=30, speed=1.5)})
    assert validate_progress_transition(None, credible, _policy(eta=True)) == credible


def test_terminal_failure_retains_measurement_and_success_completes() -> None:
    active = _progress(overall=6, current=20)
    failed = terminal_progress(active, "failed")
    assert failed.overall.percent == 60
    assert failed.current.percent == 20
    succeeded = terminal_progress(active, "succeeded")
    assert succeeded.overall.percent == 100
    assert succeeded.current.percent == 100
    no_change = terminal_progress(active, "no_change")
    assert no_change.overall.percent == 100


def test_batch_created_can_advance_without_fake_current_percent() -> None:
    progress = _progress().model_copy(
        update={
            "stage": ProgressStage(key="batch_created", label_key="jobs.stage.batch_created"),
            "current": ProgressMeasurement.indeterminate(scope_id="probe:movie:1"),
        }
    )
    assert validate_progress_transition(None, progress, _policy()).current.percent is None
