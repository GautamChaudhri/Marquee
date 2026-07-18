"""Subject-aware semantic progress owned by one admitted execution attempt."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from marquee.core.jobs.contracts import ProgressStrategy
from marquee.core.jobs.definitions import JobDefinition
from marquee.core.jobs.progress import MeasurementMode, ProgressMeasurementUpdate, ProgressWait
from marquee.core.jobs.progress_service import (
    ProgressMetricObservation,
    ProgressObservation,
    progress_writer,
)
from marquee.core.jobs.subjects import SUBJECT_SNAPSHOT_ADAPTER, SubjectSnapshot


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

    def _measurement(
        self,
        *,
        scope: str,
        label: str,
        completed: float | None,
        total: float | None,
        unit: str | None,
    ) -> ProgressMeasurementUpdate:
        if total is not None and total > 0 and completed is not None:
            return ProgressMeasurementUpdate(
                scope_id=scope,
                mode=MeasurementMode.DETERMINATE,
                unit=unit or "items",
                completed=max(0.0, min(completed, total)),
                total=total,
                label=label,
            )
        mode = (
            MeasurementMode.NONE
            if self.definition.progress_policy.strategy == ProgressStrategy.NONE
            else MeasurementMode.INDETERMINATE
        )
        return ProgressMeasurementUpdate(scope_id=scope, mode=mode, unit=unit, label=label)

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
        if stage_key not in self.definition.progress_policy.stage_keys:
            raise ValueError(f"stage {stage_key!r} is not owned by {self.definition.job_type}")
        self.current_stage_key = stage_key
        stage_label = label or dict(self.definition.progress_policy.stages)[stage_key]
        overall = self._measurement(
            scope=f"{self.definition.job_type}:overall",
            label=stage_label,
            completed=completed,
            total=total,
            unit=unit,
        )
        current = self._measurement(
            scope=f"{self.definition.job_type}:{self.subject.display_id}:{stage_key}",
            label=stage_label,
            completed=completed,
            total=total,
            unit=unit,
        )
        await progress_writer.safe_write(
            job_id=self.job_id,
            attempt_id=self.attempt_id,
            fence_token=self.fence_token,
            observation=ProgressObservation(
                stage_key=stage_key,
                overall=overall,
                current=current,
                current_subject=self.subject,
                metrics=ProgressMetricObservation(
                    elapsed_seconds=max(
                        0.0, (datetime.now(UTC) - self.started_at).total_seconds()
                    ),
                    speed=speed,
                    fps=fps,
                    bytes_processed=bytes_processed,
                    bytes_total=bytes_total,
                    throughput=throughput,
                ),
                wait=(
                    ProgressWait(kind=wait_kind, label_key=wait_label_key or wait_kind)
                    if wait_kind
                    else None
                ),
            ),
        )

    async def io_bytes(self, processed: int, total: int) -> None:
        """Attach chunked execution I/O metrics to the active semantic stage."""
        await self.stage(
            self.current_stage_key,
            bytes_processed=processed,
            bytes_total=total,
        )
