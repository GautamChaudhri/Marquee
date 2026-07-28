from __future__ import annotations

import asyncio
import contextlib
import logging
import math
from collections import OrderedDict
from datetime import UTC, datetime
from typing import Any

from pydantic import Field
from sqlalchemy import select

from marquee.core.jobs.documents import StrictDocument
from marquee.core.jobs.event_service import job_event_writer
from marquee.core.jobs.manifest import JOB_DEFINITION_REGISTRY
from marquee.core.jobs.progress import (
    ConcurrentSubject,
    JobProgress,
    MeasurementMode,
    ProgressInvariantError,
    ProgressMeasurement,
    ProgressMeasurementUpdate,
    ProgressMetrics,
    ProgressStage,
    ProgressWait,
    terminal_progress,
    validate_progress_transition,
)
from marquee.core.jobs.subjects import SubjectSnapshot
from marquee.database import _get_session_factory
from marquee.models import Job, JobAttempt

logger = logging.getLogger(__name__)


class ProgressWriteError(RuntimeError):
    """A progress observation cannot replace the canonical snapshot."""


class ProgressMetricObservation(StrictDocument):
    elapsed_seconds: float | None = None
    speed: float | None = None
    fps: float | None = None
    bytes_processed: int | None = None
    bytes_total: int | None = None
    throughput: float | None = None
    encoder: str | None = Field(default=None, max_length=100)
    decoder: str | None = Field(default=None, max_length=100)
    items_survived: int | None = Field(default=None, ge=0)


class ProgressObservation(StrictDocument):
    """Handler input. Sequence, percentage, stage labels, and ETA are server-owned."""

    stage_key: str = Field(pattern=r"^[a-z][a-z0-9_.-]*$", max_length=100)
    overall: ProgressMeasurementUpdate
    current: ProgressMeasurementUpdate
    current_subject: SubjectSnapshot | None = None
    metrics: ProgressMetricObservation = Field(default_factory=ProgressMetricObservation)
    wait: ProgressWait | None = None
    concurrent_subjects: tuple[ConcurrentSubject, ...] = ()
    warning_count: int = Field(default=0, ge=0)
    failure_count: int = Field(default=0, ge=0)
    producer_ordinal: int | None = Field(default=None, ge=1)


class ProgressWriter:
    """Sole authority for durable fenced progress snapshots and their semantic event."""

    def __init__(self) -> None:
        self._ordinals: OrderedDict[tuple[str, int, int], int] = OrderedDict()
        self._locks = tuple(asyncio.Lock() for _ in range(64))

    async def write(
        self,
        *,
        job_id: str,
        attempt_id: int,
        fence_token: int,
        observation: ProgressObservation,
    ) -> JobProgress:
        identity = (job_id, attempt_id, fence_token)
        async with self._locks[hash(identity) % len(self._locks)]:
            prior_ordinal = self._ordinals.get(identity)
            if (
                observation.producer_ordinal is not None
                and prior_ordinal is not None
                and observation.producer_ordinal <= prior_ordinal
            ):
                raise ProgressWriteError("producer progress sample is stale or duplicate")
            progress = await self._write_transaction(
                job_id=job_id,
                attempt_id=attempt_id,
                fence_token=fence_token,
                observation=observation,
            )
            if observation.producer_ordinal is not None:
                self._ordinals[identity] = observation.producer_ordinal
                self._ordinals.move_to_end(identity)
                while len(self._ordinals) > 4096:
                    self._ordinals.popitem(last=False)
            return progress

    async def terminalize(self, session, job: Job, *, outcome: str, occurred_at: datetime) -> None:
        """Preserve and seal the last good snapshot inside the fenced terminal transaction."""
        if job.progress is None:
            return
        try:
            previous = JobProgress.model_validate(job.progress)
        except ValueError:
            logger.exception("stored progress document could not be finalized for job %s", job.id)
            return
        sequence = job.progress_sequence + 1
        progress = terminal_progress(previous, outcome).model_copy(
            update={"sequence": sequence, "updated_at": occurred_at}
        )
        job.progress_sequence = sequence
        job.progress = progress.model_dump(mode="json")
        job.progress_updated_at = occurred_at
        job.current_stage = progress.stage.key
        job.current_subject = (
            progress.current_subject.model_dump(mode="json")
            if progress.current_subject is not None
            else None
        )
        await job_event_writer.append(
            session,
            job_id=job.id,
            attempt_id=progress.attempt_id,
            event_key="progress.updated",
            state="terminal",
            message="Job progress finalized",
            detail={"progress_sequence": sequence},
            canonical_version=progress.fence_token,
        )

    async def safe_write(self, **kwargs: Any) -> JobProgress | None:
        """Isolate evidence failure from the handler's media effect."""
        try:
            return await self.write(**kwargs)
        except Exception:
            logger.exception("progress observation could not be persisted")
            return None

    async def _write_transaction(
        self,
        *,
        job_id: str,
        attempt_id: int,
        fence_token: int,
        observation: ProgressObservation,
    ) -> JobProgress:
        factory = _get_session_factory()
        async with factory() as session, session.begin():
            job = await session.scalar(select(Job).where(Job.id == job_id).with_for_update())
            attempt = await session.scalar(
                select(JobAttempt).where(
                    JobAttempt.id == attempt_id,
                    JobAttempt.job_id == job_id,
                    JobAttempt.fence_token == fence_token,
                )
            )
            if (
                job is None
                or attempt is None
                or job.current_attempt_id != attempt_id
                or job.fence_token != fence_token
            ):
                raise ProgressWriteError("progress attempt ownership is stale")
            if job.phase == "terminal":
                raise ProgressWriteError("terminal progress is immutable")

            definition = JOB_DEFINITION_REGISTRY.get(job.type)
            policy = definition.progress_policy
            if policy is None:
                raise ProgressWriteError("job definition has no progress policy")
            if observation.stage_key not in policy.stage_keys:
                raise ProgressWriteError("progress stage is not allowed by policy")
            self._validate_units(observation, policy.overall_unit, policy.current_unit)
            if (
                observation.current_subject is not None
                and observation.current_subject.kind not in definition.subject_kinds
                and definition.parent_policy is None
            ):
                raise ProgressWriteError("progress subject kind is not allowed by definition")
            if observation.concurrent_subjects and not policy.allow_concurrent_subjects:
                raise ProgressWriteError("concurrent progress subjects are not allowed")

            previous: JobProgress | None = None
            if job.progress is not None:
                try:
                    previous = JobProgress.model_validate(job.progress)
                except ValueError as exc:
                    raise ProgressWriteError("stored progress document is invalid") from exc
            sequence = job.progress_sequence + 1
            stage_label = dict(policy.stages)[observation.stage_key]
            overall = observation.overall.materialize()
            current = observation.current.materialize()
            metrics = self._metrics(observation.metrics, overall, current, policy.eta_capability)
            progress = JobProgress(
                sequence=sequence,
                job_id=job_id,
                attempt_id=attempt_id,
                attempt_number=attempt.number,
                fence_token=fence_token,
                updated_at=datetime.now(UTC),
                headline=stage_label,
                stage=ProgressStage(key=observation.stage_key, label_key=stage_label),
                current_subject=(
                    observation.current_subject
                    if observation.current_subject is not None
                    else previous.current_subject
                    if previous is not None
                    else None
                ),
                overall=overall,
                current=current,
                metrics=metrics,
                wait=observation.wait,
                concurrent_subjects=observation.concurrent_subjects,
                warning_count=observation.warning_count,
                failure_count=observation.failure_count,
            )
            try:
                validate_progress_transition(previous, progress, policy)
            except ProgressInvariantError as exc:
                raise ProgressWriteError(str(exc)) from exc

            job.progress_sequence = sequence
            job.progress = progress.model_dump(mode="json")
            job.progress_updated_at = progress.updated_at
            job.current_stage = progress.stage.key
            job.current_subject = (
                progress.current_subject.model_dump(mode="json")
                if progress.current_subject is not None
                else None
            )
            await job_event_writer.append(
                session,
                job_id=job_id,
                attempt_id=attempt_id,
                event_key="progress.updated",
                state=job.phase,
                message="Job progress updated",
                detail={"progress_sequence": sequence},
                canonical_version=fence_token,
            )
            return progress

    @staticmethod
    def _validate_units(
        observation: ProgressObservation,
        overall_unit: str | None,
        current_unit: str | None,
    ) -> None:
        for update, allowed, label in (
            (observation.overall, overall_unit, "overall"),
            (observation.current, current_unit, "current"),
        ):
            if allowed is None:
                if update.mode != MeasurementMode.NONE:
                    raise ProgressWriteError(f"{label} progress must be none for this policy")
            elif update.mode == MeasurementMode.DETERMINATE and update.unit != allowed:
                raise ProgressWriteError(f"{label} progress unit is incompatible with policy")

    @staticmethod
    def _metrics(
        observed: ProgressMetricObservation,
        overall: ProgressMeasurement,
        current: ProgressMeasurement,
        eta_capability: bool,
    ) -> ProgressMetrics:
        values = observed.model_dump()
        eta: float | None = None
        if eta_capability:
            scope = current if current.mode == MeasurementMode.DETERMINATE else overall
            if scope.mode == MeasurementMode.DETERMINATE:
                rate = observed.throughput
                if rate is None and scope.unit == "seconds":
                    rate = observed.speed
                if (
                    rate is not None
                    and math.isfinite(rate)
                    and rate > 0
                    and scope.total is not None
                    and scope.completed is not None
                ):
                    eta = (scope.total - scope.completed) / rate
        return ProgressMetrics(**values, eta_seconds=eta)


class ProgressCoalescer:
    """One latest-only bounded coalescer scoped to an immutable attempt identity."""

    def __init__(
        self,
        *,
        writer: ProgressWriter,
        job_id: str,
        attempt_id: int,
        fence_token: int,
        cadence_seconds: float,
        max_staleness_seconds: float,
        meaningful_delta_percent: float | None,
    ) -> None:
        self.writer = writer
        self.identity = {
            "job_id": job_id,
            "attempt_id": attempt_id,
            "fence_token": fence_token,
        }
        self.cadence = cadence_seconds
        self.max_staleness = max_staleness_seconds
        self.meaningful_delta = meaningful_delta_percent
        self._pending: ProgressObservation | None = None
        self._durable: ProgressObservation | None = None
        self._last_flush = asyncio.get_running_loop().time()
        self._task: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()

    async def submit(self, observation: ProgressObservation) -> JobProgress | None:
        async with self._lock:
            forced = self._forced(observation)
            self._pending = observation
            overdue = asyncio.get_running_loop().time() - self._last_flush >= self.max_staleness
            if forced or overdue:
                result = await self._flush_locked()
                if result is None:
                    self._ensure_flush_task()
                return result
            if self._task is None or self._task.done():
                self._ensure_flush_task()
            return None

    async def flush(self) -> JobProgress | None:
        async with self._lock:
            return await self._flush_locked()

    async def close(self, *, timeout: float = 2.0) -> JobProgress | None:
        task = self._task
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        try:
            return await asyncio.wait_for(self.flush(), timeout=timeout)
        except TimeoutError:
            logger.error("bounded progress shutdown flush timed out")
            return None

    async def _delayed_flush(self, delay: float) -> None:
        try:
            while self._pending is not None:
                await asyncio.sleep(delay)
                if await self.flush() is not None:
                    return
        except asyncio.CancelledError:
            raise

    def _ensure_flush_task(self) -> None:
        if self._task is None or self._task.done():
            delay = min(self.cadence, self.max_staleness)
            self._task = asyncio.create_task(self._delayed_flush(delay))

    async def _flush_locked(self) -> JobProgress | None:
        observation = self._pending
        if observation is None:
            return None
        result = await self.writer.safe_write(observation=observation, **self.identity)
        if result is not None:
            self._durable = observation
            self._pending = None
            self._last_flush = asyncio.get_running_loop().time()
        return result

    def _forced(self, current: ProgressObservation) -> bool:
        previous = self._durable
        if previous is None:
            return True
        if (
            current.stage_key != previous.stage_key
            or current.current_subject != previous.current_subject
            or current.overall.scope_id != previous.overall.scope_id
            or current.current.scope_id != previous.current.scope_id
            or current.overall.mode != previous.overall.mode
            or current.current.mode != previous.current.mode
            or (current.wait is None) != (previous.wait is None)
            or current.warning_count != previous.warning_count
            or current.failure_count != previous.failure_count
        ):
            return True
        if self.meaningful_delta is None:
            return False
        for before, after in (
            (previous.overall, current.overall),
            (previous.current, current.current),
        ):
            if (
                before.completed is not None
                and before.total
                and after.completed is not None
                and after.total
            ):
                old = 100 * before.completed / before.total
                new = 100 * after.completed / after.total
                if new - old >= self.meaningful_delta:
                    return True
        return False


progress_writer = ProgressWriter()
