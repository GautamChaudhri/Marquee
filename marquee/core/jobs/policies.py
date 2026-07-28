"""Pure retry, effect-safety, action, and parent aggregation policies."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from enum import StrEnum

from marquee.core.jobs.contracts import EffectSafety, JobAction


class RetryMode(StrEnum):
    """Definition-owned user retry contract."""

    UNSUPPORTED = "unsupported"
    GENERIC = "generic"
    DOMAIN_COORDINATED = "domain_coordinated"


class RetryClassification(StrEnum):
    PERMANENT = "permanent"
    TRANSIENT = "transient"
    CANCELLED = "cancelled"
    UNSAFE = "unsafe"


class ClassifiedExecutionError(RuntimeError):
    """A domain execution failure with a definition-consumable classification."""

    def __init__(self, message: str, classification: RetryClassification) -> None:
        super().__init__(message)
        self.classification = classification


@dataclass(frozen=True)
class RetryDecision:
    classification: RetryClassification
    delay_seconds: float | None = None

    def __post_init__(self) -> None:
        if self.classification == RetryClassification.TRANSIENT:
            if self.delay_seconds is None or self.delay_seconds <= 0:
                raise ValueError("transient retries require a positive delay")
        elif self.delay_seconds is not None:
            raise ValueError("only transient retries carry a delay")


def default_failure_classifier(exc: BaseException) -> RetryClassification:
    """Bounded definition classifier for execution-kernel failures."""
    from pgqueuer import RetryRequested  # noqa: PLC0415

    from marquee.core.jobs.execution_io import ExecutionIOCancelledError  # noqa: PLC0415

    if isinstance(exc, ClassifiedExecutionError):
        return exc.classification
    if isinstance(exc, (RetryRequested, TimeoutError)):
        return RetryClassification.TRANSIENT
    if isinstance(exc, (asyncio.CancelledError, ExecutionIOCancelledError)):
        return RetryClassification.CANCELLED
    return RetryClassification.PERMANENT


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int
    transient_delays_seconds: tuple[float, ...] = ()
    idempotency_proof: str | None = None

    def __post_init__(self) -> None:
        if self.max_attempts < 1 or self.max_attempts > 10:
            raise ValueError("retry attempts must be bounded between one and ten")
        if len(self.transient_delays_seconds) < max(0, self.max_attempts - 1):
            raise ValueError("retry policy requires a delay for every additional attempt")
        if any(delay <= 0 for delay in self.transient_delays_seconds):
            raise ValueError("retry delays must be positive")

    def validate_safety(self, safety: EffectSafety) -> None:
        if (
            safety == EffectSafety.UNSAFE_MUTATION
            and self.max_attempts > 1
            and not self.idempotency_proof
        ):
            raise ValueError("unsafe mutation retries require a staged/fenced idempotency proof")

    def classify(self, classification: RetryClassification, attempt_number: int) -> RetryDecision:
        if classification != RetryClassification.TRANSIENT:
            return RetryDecision(classification)
        if attempt_number >= self.max_attempts:
            return RetryDecision(RetryClassification.PERMANENT)
        return RetryDecision(
            RetryClassification.TRANSIENT,
            self.transient_delays_seconds[attempt_number - 1],
        )


@dataclass(frozen=True)
class ActionPolicy:
    cancel: bool = True
    pause: bool = False
    change_priority: bool = True
    retry: bool = True
    logs: bool = False
    artifacts: bool = False
    detail: bool = True


@dataclass(frozen=True)
class ActionContext:
    phase: str
    desired_state: str
    outcome: str | None
    active_attempt: bool
    retryable: bool
    logs_available: bool
    artifacts_available: bool


def allowed_actions(policy: ActionPolicy, context: ActionContext) -> frozenset[JobAction]:
    actions: set[JobAction] = set()
    if policy.detail:
        actions.add(JobAction.OPEN_DETAIL)
    if policy.logs and context.logs_available:
        actions.add(JobAction.OPEN_LOGS)
    if policy.artifacts and context.artifacts_available:
        actions.add(JobAction.OPEN_ARTIFACTS)
    if context.phase != "terminal":
        if policy.cancel and context.desired_state != "cancel":
            actions.add(JobAction.CANCEL)
        if policy.change_priority and context.phase in {"planned", "queued"}:
            actions.add(JobAction.CHANGE_PRIORITY)
        if policy.pause and context.phase in {"planned", "queued"}:
            actions.add(JobAction.RESUME if context.desired_state == "pause" else JobAction.PAUSE)
    elif policy.retry and context.retryable and context.outcome not in {"succeeded", "no_change"}:
        actions.add(JobAction.RETRY)
    return frozenset(actions)


@dataclass(frozen=True)
class ParentAggregationPolicy:
    fixed_children: bool
    require_sealed: bool = True
    retry_children: str = "failed"
    pause_children: bool = False

    def __post_init__(self) -> None:
        if self.retry_children not in {"all", "failed"}:
            raise ValueError("parent retry scope must be all or failed")


@dataclass(frozen=True)
class ParentAggregate:
    terminal: bool
    outcome: str | None
    completed: int
    total: int
    succeeded: int
    no_change: int
    failed: int
    cancelled: int


def aggregate_parent(
    child_outcomes: tuple[str | None, ...],
    *,
    sealed: bool,
    policy: ParentAggregationPolicy,
) -> ParentAggregate:
    total = len(child_outcomes)
    counts = {
        "succeeded": child_outcomes.count("succeeded"),
        "no_change": child_outcomes.count("no_change"),
        "failed": child_outcomes.count("failed") + child_outcomes.count("dead_letter"),
        "cancelled": child_outcomes.count("cancelled"),
    }
    completed = sum(counts.values())
    terminal = (sealed or not policy.require_sealed) and completed == total and total > 0
    outcome = None
    if terminal:
        positive = counts["succeeded"] + counts["no_change"]
        if counts["failed"] and positive:
            outcome = "partially_succeeded"
        elif counts["failed"]:
            outcome = "failed"
        elif counts["cancelled"] and positive:
            outcome = "partially_succeeded"
        elif counts["cancelled"]:
            outcome = "cancelled"
        elif counts["succeeded"]:
            outcome = "succeeded"
        else:
            outcome = "no_change"
    return ParentAggregate(
        terminal=terminal,
        outcome=outcome,
        completed=completed,
        total=total,
        **counts,
    )
