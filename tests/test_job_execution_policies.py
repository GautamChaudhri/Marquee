from __future__ import annotations

import pytest

from marquee.core.jobs.contracts import EffectSafety, JobAction
from marquee.core.jobs.execution_io import ExecutionIOCancelledError
from marquee.core.jobs.policies import (
    ActionContext,
    ActionPolicy,
    ParentAggregationPolicy,
    RetryClassification,
    RetryPolicy,
    aggregate_parent,
    allowed_actions,
    default_failure_classifier,
)


def test_unsafe_mutation_defaults_to_one_attempt_without_proof() -> None:
    RetryPolicy(max_attempts=1).validate_safety(EffectSafety.UNSAFE_MUTATION)
    with pytest.raises(ValueError, match="idempotency proof"):
        RetryPolicy(max_attempts=2, transient_delays_seconds=(5,)).validate_safety(
            EffectSafety.UNSAFE_MUTATION
        )
    proven = RetryPolicy(
        max_attempts=2,
        transient_delays_seconds=(5,),
        idempotency_proof="attempt staging plus fence-checked coordinator publish",
    )
    proven.validate_safety(EffectSafety.UNSAFE_MUTATION)


def test_retry_classifier_is_bounded() -> None:
    policy = RetryPolicy(max_attempts=3, transient_delays_seconds=(5, 30))
    assert policy.classify(RetryClassification.TRANSIENT, 1).delay_seconds == 5
    assert policy.classify(RetryClassification.TRANSIENT, 2).delay_seconds == 30
    assert policy.classify(RetryClassification.TRANSIENT, 3).classification == "permanent"
    assert policy.classify(RetryClassification.CANCELLED, 1).classification == "cancelled"


def test_execution_io_cancellation_is_terminal_cancellation() -> None:
    assert default_failure_classifier(ExecutionIOCancelledError("execution I/O cancelled")) == (
        RetryClassification.CANCELLED
    )


def test_actions_are_computed_from_policy_and_state() -> None:
    policy = ActionPolicy(pause=True, logs=True, artifacts=True)
    queued = ActionContext(
        phase="queued",
        desired_state="run",
        outcome=None,
        active_attempt=False,
        retryable=False,
        logs_available=False,
        artifacts_available=False,
    )
    assert allowed_actions(policy, queued) == {
        JobAction.CANCEL,
        JobAction.PAUSE,
        JobAction.CHANGE_PRIORITY,
        JobAction.OPEN_DETAIL,
    }
    paused = ActionContext(**{**queued.__dict__, "desired_state": "pause"})
    assert JobAction.RESUME in allowed_actions(policy, paused)
    terminal = ActionContext(
        phase="terminal",
        desired_state="run",
        outcome="failed",
        active_attempt=False,
        retryable=True,
        logs_available=True,
        artifacts_available=True,
    )
    assert allowed_actions(policy, terminal) == {
        JobAction.RETRY,
        JobAction.OPEN_LOGS,
        JobAction.OPEN_ARTIFACTS,
        JobAction.OPEN_DETAIL,
    }


def test_parent_aggregation_requires_sealing_and_preserves_outcome_meaning() -> None:
    policy = ParentAggregationPolicy(fixed_children=True)
    unsealed = aggregate_parent(("succeeded", "no_change"), sealed=False, policy=policy)
    assert not unsealed.terminal
    assert unsealed.outcome is None
    assert (
        aggregate_parent(("no_change", "no_change"), sealed=True, policy=policy).outcome
        == "no_change"
    )
    assert (
        aggregate_parent(("succeeded", "failed"), sealed=True, policy=policy).outcome
        == "partially_succeeded"
    )
    assert aggregate_parent(("failed", "failed"), sealed=True, policy=policy).outcome == "failed"
    assert aggregate_parent(("cancelled",), sealed=True, policy=policy).outcome == "cancelled"
