"""Subject-aware semantic progress owned by one admitted execution attempt.

JMC6I §6 extends this facade with independent *overall* and *current* observation
scopes. The overall scope is stable and monotonic regardless of current work; the
current scope may reset only when its caller-stable scope key changes. Legitimate
mode/unit transitions allocate a new epoch-suffixed scope id (the durable contract
requires a new ``scope_id`` for any mode/unit change), while regressions, shrinking
totals, and non-finite values are recorded as operational degradation instead of
being silently swallowed. The server remains the sole percentage authority.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from marquee.core.jobs.contracts import ProgressStrategy
from marquee.core.jobs.definitions import JobDefinition
from marquee.core.jobs.progress import MeasurementMode, ProgressMeasurementUpdate, ProgressWait
from marquee.core.jobs.progress_service import (
    ProgressCoalescer,
    ProgressMetricObservation,
    ProgressObservation,
    progress_writer,
)
from marquee.core.jobs.subjects import SUBJECT_SNAPSHOT_ADAPTER, SubjectSnapshot

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ScopeObservation:
    """One caller-supplied measurement for a single progress scope."""

    mode: MeasurementMode
    completed: float | None = None
    total: float | None = None
    unit: str | None = None
    label: str | None = None
    # Caller-stable discriminator for the *current* scope; a new key legitimately
    # resets the current measurement. Ignored for the overall scope.
    scope_key: str | None = None

    @classmethod
    def determinate(
        cls,
        *,
        completed: float,
        total: float,
        unit: str | None = None,
        label: str | None = None,
        scope_key: str | None = None,
    ) -> ScopeObservation:
        return cls(
            mode=MeasurementMode.DETERMINATE,
            completed=completed,
            total=total,
            unit=unit,
            label=label,
            scope_key=scope_key,
        )

    @classmethod
    def indeterminate(
        cls,
        *,
        unit: str | None = None,
        label: str | None = None,
        scope_key: str | None = None,
    ) -> ScopeObservation:
        return cls(mode=MeasurementMode.INDETERMINATE, unit=unit, label=label, scope_key=scope_key)


@dataclass(slots=True)
class ExecutionProgress:
    """Emit fenced progress with stable scopes and the frozen current subject."""

    job_id: str
    attempt_id: int
    fence_token: int
    definition: JobDefinition
    subject: SubjectSnapshot
    started_at: datetime
    current_stage_key: str
    degraded_observations: int = 0
    _overall: ProgressMeasurementUpdate | None = None
    _current: ProgressMeasurementUpdate | None = None
    _overall_epoch: int = 0
    _current_epoch: int = 0
    _current_scope_key: str | None = None
    _last_overall_fraction: float | None = None
    _survivors: int | None = None
    _coalescer: ProgressCoalescer | None = field(default=None, repr=False)

    @classmethod
    def create(
        cls,
        *,
        job_id: str,
        attempt_id: int,
        fence_token: int,
        definition: JobDefinition,
        subject: dict[str, Any],
    ) -> ExecutionProgress:
        return cls(
            job_id=job_id,
            attempt_id=attempt_id,
            fence_token=fence_token,
            definition=definition,
            subject=SUBJECT_SNAPSHOT_ADAPTER.validate_python(subject),
            started_at=datetime.now(UTC),
            current_stage_key=definition.progress_policy.stages[0][0],
        )

    # ------------------------------------------------------------------ #
    # Degradation accounting (observable, never fatal to the operation)
    # ------------------------------------------------------------------ #

    def _degrade(self, reason: str) -> None:
        self.degraded_observations += 1
        logger.warning(
            "progress observation degraded for %s attempt %s: %s",
            self.job_id,
            self.attempt_id,
            reason,
        )

    # ------------------------------------------------------------------ #
    # Scope-state transitions
    # ------------------------------------------------------------------ #

    @staticmethod
    def _finite(value: float | None) -> bool:
        return value is None or (math.isfinite(value) and value >= 0)

    def _default_mode(self, allowed_unit: str | None) -> MeasurementMode:
        # A scope whose policy declares no unit is a "none" scope by contract;
        # the durable writer rejects any other mode for it.
        if allowed_unit is None:
            return MeasurementMode.NONE
        if self.definition.progress_policy.strategy == ProgressStrategy.NONE:
            return MeasurementMode.NONE
        return MeasurementMode.INDETERMINATE

    def _overall_update(self, observed: ScopeObservation | None) -> ProgressMeasurementUpdate | None:
        """Fold one overall observation into the stable monotonic overall scope."""
        policy_unit = self.definition.progress_policy.overall_unit
        base_scope = f"{self.definition.job_type}:overall"
        if observed is None:
            if self._overall is not None:
                return self._overall
            return ProgressMeasurementUpdate(
                scope_id=base_scope, mode=self._default_mode(policy_unit)
            )
        if not self._finite(observed.completed) or not self._finite(observed.total):
            self._degrade("overall measurement is not finite and non-negative")
            return None
        if observed.mode == MeasurementMode.DETERMINATE:
            if policy_unit is None:
                self._degrade("this definition declares no overall measurement unit")
                return None
            if observed.completed is None or observed.total is None or observed.total <= 0:
                self._degrade("determinate overall requires completed and a positive total")
                return None
            if observed.completed > observed.total:
                self._degrade("overall completed exceeds its total")
                return None
            unit = observed.unit or policy_unit
            fraction = observed.completed / observed.total
            if self._last_overall_fraction is not None and fraction < self._last_overall_fraction:
                self._degrade("overall progress cannot regress within an attempt")
                return None
            previous = self._overall
            if previous is not None and previous.mode == MeasurementMode.DETERMINATE:
                if previous.unit != unit:
                    self._overall_epoch += 1
                elif previous.total is not None and observed.total < previous.total:
                    self._degrade("overall total cannot shrink within a scope")
                    return None
            elif previous is not None and previous.mode != MeasurementMode.DETERMINATE:
                self._overall_epoch += 1
            scope = base_scope if self._overall_epoch == 0 else f"{base_scope}:{self._overall_epoch}"
            self._last_overall_fraction = fraction
            return ProgressMeasurementUpdate(
                scope_id=scope,
                mode=MeasurementMode.DETERMINATE,
                unit=unit,
                completed=observed.completed,
                total=observed.total,
                label=observed.label,
            )
        if observed.completed is not None or observed.total is not None:
            self._degrade("indeterminate overall cannot carry numeric completion")
            return None
        previous = self._overall
        if previous is not None and previous.mode == MeasurementMode.DETERMINATE:
            # Never downgrade a measured overall scope back to indeterminate;
            # keep the last honest measurement instead.
            return previous
        mode = observed.mode if policy_unit is not None else MeasurementMode.NONE
        return ProgressMeasurementUpdate(
            scope_id=base_scope,
            mode=mode,
            unit=observed.unit if mode != MeasurementMode.NONE else None,
            label=observed.label,
        )

    def _current_update(
        self, observed: ScopeObservation | None, stage_key: str
    ) -> ProgressMeasurementUpdate | None:
        """Fold one current observation; a new scope key legitimately resets it."""
        policy_unit = self.definition.progress_policy.current_unit
        if observed is None:
            if self._current is not None:
                return self._current
            observed = ScopeObservation(mode=self._default_mode(policy_unit))
        scope_key = observed.scope_key or stage_key
        base_scope = f"{self.definition.job_type}:{self.subject.display_id}:{scope_key}"
        if not self._finite(observed.completed) or not self._finite(observed.total):
            self._degrade("current measurement is not finite and non-negative")
            return None
        same_scope = scope_key == self._current_scope_key
        previous = self._current if same_scope else None
        if not same_scope:
            self._current_epoch = 0
        if observed.mode == MeasurementMode.DETERMINATE:
            if policy_unit is None:
                self._degrade("this definition declares no current measurement unit")
                return None
            if observed.completed is None or observed.total is None or observed.total <= 0:
                self._degrade("determinate current requires completed and a positive total")
                return None
            if observed.completed > observed.total:
                self._degrade("current completed exceeds its total")
                return None
            unit = observed.unit or policy_unit
            if previous is not None:
                if previous.mode != MeasurementMode.DETERMINATE or previous.unit != unit:
                    self._current_epoch += 1
                else:
                    if previous.total is not None and observed.total < previous.total:
                        self._degrade("current total cannot shrink without a new scope")
                        return None
                    if previous.completed is not None and observed.completed < previous.completed:
                        self._degrade("current progress reset requires a new scope key")
                        return None
            self._current_scope_key = scope_key
            scope = (
                base_scope
                if self._current_epoch == 0
                else f"{base_scope}:{self._current_epoch}"
            )
            return ProgressMeasurementUpdate(
                scope_id=scope,
                mode=MeasurementMode.DETERMINATE,
                unit=unit,
                completed=observed.completed,
                total=observed.total,
                label=observed.label,
            )
        if observed.completed is not None or observed.total is not None:
            self._degrade("indeterminate current cannot carry numeric completion")
            return None
        if previous is not None and previous.mode == MeasurementMode.DETERMINATE:
            # A liveness beat inside a measured scope keeps the last measurement.
            return previous
        self._current_scope_key = scope_key
        mode = observed.mode if policy_unit is not None else MeasurementMode.NONE
        scope = base_scope if self._current_epoch == 0 else f"{base_scope}:{self._current_epoch}"
        return ProgressMeasurementUpdate(
            scope_id=scope,
            mode=mode,
            unit=observed.unit if mode != MeasurementMode.NONE else None,
            label=observed.label,
        )

    # ------------------------------------------------------------------ #
    # Durable observation
    # ------------------------------------------------------------------ #

    def _ensure_coalescer(self) -> ProgressCoalescer:
        if self._coalescer is None:
            policy = self.definition.progress_policy
            self._coalescer = ProgressCoalescer(
                writer=progress_writer,
                job_id=self.job_id,
                attempt_id=self.attempt_id,
                fence_token=self.fence_token,
                cadence_seconds=policy.persistence_cadence_seconds,
                max_staleness_seconds=policy.max_snapshot_staleness_seconds,
                meaningful_delta_percent=policy.meaningful_delta_percent,
            )
        return self._coalescer

    async def observe(
        self,
        stage_key: str | None = None,
        *,
        overall: ScopeObservation | None = None,
        current: ScopeObservation | None = None,
        survivors: int | None = None,
        wait_kind: str | None = None,
        wait_label_key: str | None = None,
        bytes_processed: int | None = None,
        bytes_total: int | None = None,
        throughput: float | None = None,
        speed: float | None = None,
        fps: float | None = None,
        durable: bool = True,
    ) -> None:
        """Publish one validated observation with independent scope semantics.

        ``durable=True`` flushes through the coalescer immediately (stage
        boundaries, terminal-adjacent writes); ``durable=False`` submits a
        high-frequency sample that the coalescer persists on cadence, meaningful
        delta, forced transition, or maximum staleness. Invalid observations are
        recorded as degradation and never raise into the product operation.
        """
        stage = stage_key or self.current_stage_key
        if stage not in self.definition.progress_policy.stage_keys:
            self._degrade(f"stage {stage!r} is not owned by {self.definition.job_type}")
            return
        overall_update = self._overall_update(overall)
        current_update = self._current_update(current, stage)
        if overall_update is None or current_update is None:
            return
        if survivors is not None:
            if survivors < 0:
                self._degrade("survivor counts must be non-negative")
                return
            self._survivors = survivors
        self.current_stage_key = stage
        self._overall = overall_update
        self._current = current_update
        observation = ProgressObservation(
            stage_key=stage,
            overall=overall_update,
            current=current_update,
            current_subject=self.subject,
            metrics=ProgressMetricObservation(
                elapsed_seconds=max(0.0, (datetime.now(UTC) - self.started_at).total_seconds()),
                speed=speed,
                fps=fps,
                bytes_processed=bytes_processed,
                bytes_total=bytes_total,
                throughput=throughput,
                items_survived=self._survivors,
            ),
            wait=(
                ProgressWait(kind=wait_kind, label_key=wait_label_key or wait_kind)
                if wait_kind
                else None
            ),
        )
        coalescer = self._ensure_coalescer()
        result = await coalescer.submit(observation)
        if durable and result is None:
            result = await coalescer.flush()
            if result is None:
                self._degrade("durable progress observation could not be persisted")

    async def stage(
        self,
        stage_key: str,
        *,
        label: str | None = None,
        completed: float | None = None,
        total: float | None = None,
        unit: str | None = None,
        wait_kind: str | None = None,
        wait_label_key: str | None = None,
        bytes_processed: int | None = None,
        bytes_total: int | None = None,
        throughput: float | None = None,
        speed: float | None = None,
        fps: float | None = None,
    ) -> None:
        """Compatibility surface: one measurement mirrored into both scopes.

        Handlers with a single work axis (for example seconds encoded of one
        file) keep this API; the measurement now genuinely persists across mode
        transitions because scope epochs replace the old fixed scope id.
        """
        policy = self.definition.progress_policy
        if stage_key not in policy.stage_keys:
            raise ValueError(f"stage {stage_key!r} is not owned by {self.definition.job_type}")
        stage_label = label or dict(policy.stages)[stage_key]
        measured = completed is not None and total is not None and total > 0
        overall = None
        if measured and policy.overall_unit is not None:
            overall = ScopeObservation.determinate(
                completed=max(0.0, min(float(completed), float(total))),
                total=float(total),
                unit=unit if unit == policy.overall_unit else None,
                label=stage_label,
            )
        if measured and policy.current_unit is not None:
            current = ScopeObservation.determinate(
                completed=max(0.0, min(float(completed), float(total))),
                total=float(total),
                unit=unit if unit == policy.current_unit else None,
                label=stage_label,
                scope_key=stage_key,
            )
        elif policy.current_unit is not None:
            current = ScopeObservation.indeterminate(
                unit=unit, label=stage_label, scope_key=stage_key
            )
        else:
            current = ScopeObservation(
                mode=MeasurementMode.NONE, label=stage_label, scope_key=stage_key
            )
        await self.observe(
            stage_key,
            overall=overall,
            current=current,
            wait_kind=wait_kind,
            wait_label_key=wait_label_key,
            bytes_processed=bytes_processed,
            bytes_total=bytes_total,
            throughput=throughput,
            speed=speed,
            fps=fps,
            durable=True,
        )

    async def io_bytes(self, processed: int, total: int) -> None:
        """Attach chunked execution I/O metrics to the active semantic stage."""
        await self.stage(
            self.current_stage_key,
            bytes_processed=processed,
            bytes_total=total,
        )

    async def flush_pending(self) -> None:
        """Flush any coalesced tail sample (bridge/handler shutdown boundary)."""
        if self._coalescer is not None:
            await self._coalescer.close()
