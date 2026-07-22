"""Pure JMC2B semantic progress contracts and invariants."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal

from pydantic import Field, field_validator, model_validator

from marquee.core.jobs.contracts import ProgressStrategy
from marquee.core.jobs.documents import StrictDocument
from marquee.core.jobs.subjects import SubjectSnapshot


class MeasurementMode(StrEnum):
    DETERMINATE = "determinate"
    INDETERMINATE = "indeterminate"
    NONE = "none"


class ProgressFreshness(StrEnum):
    LIVE = "live"
    STALE = "stale"
    TERMINAL = "terminal"


class ProgressInvariantError(ValueError):
    pass


class StaleProgressSequenceError(ProgressInvariantError):
    pass


class WrongProgressFenceError(ProgressInvariantError):
    pass


class ProgressStage(StrictDocument):
    key: str = Field(pattern=r"^[a-z][a-z0-9_.-]*$", max_length=100)
    label_key: str = Field(pattern=r"^[a-z][a-z0-9_.-]*$", max_length=120)
    detail: str | None = Field(default=None, max_length=500)


class ProgressMeasurement(StrictDocument):
    scope_id: str = Field(min_length=1, max_length=160)
    mode: MeasurementMode
    unit: str | None = Field(default=None, max_length=40)
    completed: float | None = None
    total: float | None = None
    percent: float | None = None
    label: str | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def validate_measurement(self) -> ProgressMeasurement:
        if self.mode == MeasurementMode.DETERMINATE:
            if self.unit is None or self.completed is None or self.total is None:
                raise ValueError("determinate progress requires unit, completed, and total")
            if not all(math.isfinite(item) for item in (self.completed, self.total)):
                raise ValueError("progress measurements must be finite")
            if self.completed < 0 or self.total <= 0 or self.completed > self.total:
                raise ValueError("determinate progress requires 0 <= completed <= total")
            expected = round(100 * self.completed / self.total, 4)
            if self.percent != expected:
                raise ValueError("percentage must be server-computed from completed and total")
        elif any(item is not None for item in (self.completed, self.total, self.percent)):
            raise ValueError("indeterminate/none progress cannot carry numeric completion")
        return self

    @classmethod
    def determinate(
        cls,
        *,
        scope_id: str,
        unit: str,
        completed: float,
        total: float,
        label: str | None = None,
    ) -> ProgressMeasurement:
        if not math.isfinite(completed) or not math.isfinite(total) or total <= 0:
            raise ProgressInvariantError("determinate progress requires a finite positive total")
        if completed < 0 or completed > total:
            raise ProgressInvariantError("completed must remain within the determinate total")
        return cls(
            scope_id=scope_id,
            mode=MeasurementMode.DETERMINATE,
            unit=unit,
            completed=completed,
            total=total,
            percent=round(100 * completed / total, 4),
            label=label,
        )

    @classmethod
    def indeterminate(
        cls, *, scope_id: str, unit: str | None = None, label: str | None = None
    ) -> ProgressMeasurement:
        return cls(
            scope_id=scope_id,
            mode=MeasurementMode.INDETERMINATE,
            unit=unit,
            label=label,
        )

    @classmethod
    def none(cls, *, scope_id: str, label: str | None = None) -> ProgressMeasurement:
        return cls(scope_id=scope_id, mode=MeasurementMode.NONE, label=label)


class ProgressMeasurementUpdate(StrictDocument):
    """Handler input contract; percentage is deliberately not a client field."""

    scope_id: str = Field(min_length=1, max_length=160)
    mode: MeasurementMode
    unit: str | None = Field(default=None, max_length=40)
    completed: float | None = None
    total: float | None = None
    label: str | None = Field(default=None, max_length=200)

    def materialize(self) -> ProgressMeasurement:
        if self.mode == MeasurementMode.DETERMINATE:
            if self.unit is None or self.completed is None or self.total is None:
                raise ProgressInvariantError(
                    "determinate progress update requires unit, completed, and total"
                )
            return ProgressMeasurement.determinate(
                scope_id=self.scope_id,
                unit=self.unit,
                completed=self.completed,
                total=self.total,
                label=self.label,
            )
        if self.completed is not None or self.total is not None:
            raise ProgressInvariantError(
                "indeterminate/none updates cannot carry numeric completion"
            )
        if self.mode == MeasurementMode.INDETERMINATE:
            return ProgressMeasurement.indeterminate(
                scope_id=self.scope_id, unit=self.unit, label=self.label
            )
        return ProgressMeasurement.none(scope_id=self.scope_id, label=self.label)


class ProgressMetrics(StrictDocument):
    elapsed_seconds: float | None = None
    eta_seconds: float | None = None
    speed: float | None = None
    fps: float | None = None
    bytes_processed: int | None = None
    bytes_total: int | None = None
    throughput: float | None = None
    encoder: str | None = Field(default=None, max_length=100)
    decoder: str | None = Field(default=None, max_length=100)
    # Bounded survivor count from real gate/dedup stages (JMC6I §6.1); never a
    # percentage input, purely an observed measurement.
    items_survived: int | None = None

    @field_validator(
        "elapsed_seconds", "eta_seconds", "speed", "fps", "throughput"
    )
    @classmethod
    def finite_nonnegative(cls, value: float | None) -> float | None:
        if value is not None and (not math.isfinite(value) or value < 0):
            raise ValueError("progress metrics must be finite and non-negative")
        return value

    @field_validator("bytes_processed", "bytes_total", "items_survived")
    @classmethod
    def bytes_nonnegative(cls, value: int | None) -> int | None:
        if value is not None and value < 0:
            raise ValueError("byte metrics must be non-negative")
        return value


class ProgressWait(StrictDocument):
    kind: str = Field(pattern=r"^[a-z][a-z0-9_]*$", max_length=80)
    label_key: str = Field(pattern=r"^[a-z][a-z0-9_.-]*$", max_length=120)
    eligible_at: datetime | None = None


class ConcurrentSubject(StrictDocument):
    subject: SubjectSnapshot
    stage_key: str | None = Field(default=None, max_length=100)


class JobProgress(StrictDocument):
    version: Literal[1] = 1
    sequence: int = Field(ge=1)
    job_id: str = Field(min_length=1, max_length=32)
    attempt_id: int
    attempt_number: int = Field(ge=1)
    fence_token: int = Field(ge=1)
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    headline: str = Field(min_length=1, max_length=500)
    stage: ProgressStage
    freshness: ProgressFreshness = ProgressFreshness.LIVE
    current_subject: SubjectSnapshot | None = None
    overall: ProgressMeasurement
    current: ProgressMeasurement
    metrics: ProgressMetrics = Field(default_factory=ProgressMetrics)
    wait: ProgressWait | None = None
    concurrent_subjects: tuple[ConcurrentSubject, ...] = ()
    warning_count: int = Field(default=0, ge=0)
    failure_count: int = Field(default=0, ge=0)

    @field_validator("concurrent_subjects")
    @classmethod
    def bound_concurrent_subjects(
        cls, value: tuple[ConcurrentSubject, ...]
    ) -> tuple[ConcurrentSubject, ...]:
        if len(value) > 8:
            raise ValueError("concurrent subject summaries are bounded to eight")
        return value


@dataclass(frozen=True)
class ProgressPolicy:
    strategy: ProgressStrategy
    overall_unit: str | None
    denominator_source: str
    current_unit: str | None
    aggregation_strategy: str
    stages: tuple[tuple[str, str], ...]
    tool_adapter: Literal["ffmpeg_progress", "mkvmerge_gui"] | None
    persistence_cadence_seconds: float
    meaningful_delta_percent: float | None
    max_snapshot_staleness_seconds: float
    eta_capability: bool
    eta_requires_rate: bool = True
    allow_concurrent_subjects: bool = False

    def __post_init__(self) -> None:
        stage_keys = [key for key, _label in self.stages]
        if len(stage_keys) != len(set(stage_keys)) or not stage_keys:
            raise ValueError("progress policies require unique stable stages")
        if self.strategy == ProgressStrategy.NONE and (
            self.overall_unit is not None or self.current_unit is not None or self.eta_capability
        ):
            raise ValueError("no-progress policies cannot declare units or ETA")
        if self.persistence_cadence_seconds <= 0 or self.max_snapshot_staleness_seconds <= 0:
            raise ValueError("progress persistence bounds must be positive")
        if self.meaningful_delta_percent is not None and not (
            0 < self.meaningful_delta_percent <= 100
        ):
            raise ValueError("meaningful progress delta must be in (0, 100]")

    @property
    def stage_keys(self) -> frozenset[str]:
        return frozenset(key for key, _label in self.stages)


def validate_progress_transition(
    previous: JobProgress | None,
    current: JobProgress,
    policy: ProgressPolicy,
) -> JobProgress:
    if current.stage.key not in policy.stage_keys:
        raise ProgressInvariantError(f"stage {current.stage.key} is not allowed by policy")
    if current.metrics.eta_seconds is not None:
        has_rate = any(
            value is not None
            for value in (current.metrics.speed, current.metrics.fps, current.metrics.throughput)
        )
        determinate = any(
            scope.mode == MeasurementMode.DETERMINATE
            for scope in (current.overall, current.current)
        )
        if not policy.eta_capability or not determinate or (policy.eta_requires_rate and not has_rate):
            raise ProgressInvariantError("ETA is not credible for this progress policy/sample")
    if previous is None:
        return current
    if (
        current.job_id != previous.job_id
        or current.attempt_id != previous.attempt_id
        or current.fence_token != previous.fence_token
    ):
        raise WrongProgressFenceError("progress attempt/fence identity changed")
    if current.sequence <= previous.sequence:
        raise StaleProgressSequenceError("progress sequence is stale or duplicate")
    for name in ("overall", "current"):
        before = getattr(previous, name)
        after = getattr(current, name)
        if before.scope_id == after.scope_id:
            if before.mode != after.mode or before.unit != after.unit:
                raise ProgressInvariantError(
                    f"{name} measurement mode/unit change requires a new scope_id"
                )
            if (
                before.total is not None
                and after.total is not None
                and after.total < before.total
            ):
                raise ProgressInvariantError(
                    f"{name} total cannot shrink without a new scope_id"
                )
    if (
        previous.overall.percent is not None
        and current.overall.percent is not None
        and current.overall.percent < previous.overall.percent
    ):
        raise ProgressInvariantError("overall progress cannot regress within an attempt")
    if (
        previous.current.scope_id == current.current.scope_id
        and previous.current.percent is not None
        and current.current.percent is not None
        and current.current.percent < previous.current.percent
    ):
        raise ProgressInvariantError("current progress reset requires a new scope_id")
    return current


def terminal_progress(progress: JobProgress, outcome: str) -> JobProgress:
    updates: dict[str, object] = {"freshness": ProgressFreshness.TERMINAL}
    if outcome in {"succeeded", "no_change"}:
        scopes = {}
        for name in ("overall", "current"):
            scope = getattr(progress, name)
            scopes[name] = (
                ProgressMeasurement.determinate(
                    scope_id=scope.scope_id,
                    unit=scope.unit or "items",
                    completed=scope.total,
                    total=scope.total,
                    label=scope.label,
                )
                if scope.mode == MeasurementMode.DETERMINATE and scope.total is not None
                else scope
            )
        updates.update(scopes)
    return progress.model_copy(update=updates)
