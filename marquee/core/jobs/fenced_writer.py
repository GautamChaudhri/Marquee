"""Compare-and-set canonical job writes for one admitted attempt owner."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import select, update

from marquee.core.jobs.definitions import JobDefinition
from marquee.core.jobs.process_identity import ProcessIdentity
from marquee.core.jobs.process_launcher import ExecutionSummary
from marquee.database import _get_session_factory
from marquee.models.job import Job, JobAttempt, JobDispatch, JobEvent


class WriteDisposition(StrEnum):
    APPLIED = "applied"
    STALE = "stale"
    CONFLICT = "conflict"


@dataclass(frozen=True, slots=True)
class AttemptOwnership:
    job_id: str
    attempt_id: int
    fence_token: int
    dispatch_generation: int


class FencedWriter:
    def __init__(self, ownership: AttemptOwnership, definition: JobDefinition) -> None:
        self.ownership = ownership
        self.definition = definition

    async def succeed(self, result: dict[str, Any]) -> WriteDisposition:
        validated = self.definition.result.validate(
            result, version=self.definition.result.current_version
        ).model_dump(mode="json")
        return await self._terminal(
            outcome="succeeded",
            dispatch_disposition="succeeded",
            result=validated,
            error=None,
            expected_desired_states=("run",),
        )

    async def fail(
        self, exc: BaseException, *, cancelled: bool = False
    ) -> WriteDisposition:
        outcome = "cancelled" if cancelled else "failed"
        error = self.definition.error.validate(
            _safe_error(exc, cancelled=cancelled),
            version=self.definition.error.current_version,
        ).model_dump(mode="json")
        return await self._terminal(
            outcome=outcome,
            dispatch_disposition=outcome,
            result=None,
            error=error,
            expected_desired_states=("run", "cancel") if cancelled else ("run",),
        )

    async def retry(self, *, reason: str, delay_seconds: float) -> WriteDisposition:
        error = self.definition.error.validate(
            {
                "code": "retry_requested",
                "summary": (reason or "retry requested")[:500],
                "diagnostics": {"delay_seconds": delay_seconds},
            },
            version=self.definition.error.current_version,
        ).model_dump(mode="json")
        owner = self.ownership
        factory = _get_session_factory()
        async with factory() as session, session.begin():
            disposition = await _claim_job(
                session,
                owner,
                expected_phases=("running", "stopping"),
                expected_desired_states=("run",),
                values={"phase": "queued", "error": error},
            )
            if disposition != WriteDisposition.APPLIED:
                return disposition
            attempt_result = await session.execute(
                update(JobAttempt)
                .where(
                    JobAttempt.id == owner.attempt_id,
                    JobAttempt.job_id == owner.job_id,
                    JobAttempt.fence_token == owner.fence_token,
                    JobAttempt.phase.in_(("running", "stopping")),
                )
                .values(
                    phase="finished",
                    outcome="retrying",
                    failure_class="transient",
                    finished_at=datetime.now(UTC),
                    error=error,
                )
            )
            if attempt_result.rowcount != 1:
                raise RuntimeError("fenced attempt changed during retry transaction")
            session.add(
                JobEvent(
                    job_id=owner.job_id,
                    attempt_id=owner.attempt_id,
                    event_key="job.retry_requested",
                    state="retrying",
                    message=f"{self.definition.job_type} retry requested",
                    detail=error,
                )
            )
        return WriteDisposition.APPLIED

    async def stopping(self) -> WriteDisposition:
        owner = self.ownership
        now = datetime.now(UTC)
        factory = _get_session_factory()
        async with factory() as session, session.begin():
            disposition = await _claim_job(
                session,
                owner,
                expected_phases=("running",),
                expected_desired_states=("run", "cancel"),
                values={"phase": "stopping", "stopping_at": now},
            )
            if disposition != WriteDisposition.APPLIED:
                return disposition
            result = await session.execute(
                update(JobAttempt)
                .where(
                    JobAttempt.id == owner.attempt_id,
                    JobAttempt.job_id == owner.job_id,
                    JobAttempt.fence_token == owner.fence_token,
                    JobAttempt.phase == "running",
                )
                .values(phase="stopping", stopping_at=now)
            )
            if result.rowcount != 1:
                raise RuntimeError("fenced attempt changed during stopping transaction")
        return WriteDisposition.APPLIED

    async def record_process_identity(
        self, identity: ProcessIdentity
    ) -> WriteDisposition:
        """Persist the complete durable identity before the child start barrier opens."""
        owner = self.ownership
        factory = _get_session_factory()
        async with factory() as session, session.begin():
            disposition = await _claim_job(
                session,
                owner,
                expected_phases=("running",),
                expected_desired_states=("run",),
                values={"updated_at": datetime.now(UTC)},
            )
            if disposition != WriteDisposition.APPLIED:
                return disposition
            attempt = await session.scalar(
                select(JobAttempt)
                .where(
                    JobAttempt.id == owner.attempt_id,
                    JobAttempt.job_id == owner.job_id,
                    JobAttempt.fence_token == owner.fence_token,
                    JobAttempt.phase == "running",
                )
                .with_for_update()
            )
            if attempt is None:
                raise RuntimeError("fenced attempt changed while recording process identity")
            metrics = dict(attempt.metrics or {})
            metrics["process_start_ticks"] = identity.process_start_ticks
            attempt.process_id = identity.pid
            attempt.process_group_id = identity.process_group_id
            attempt.cgroup_path = identity.cgroup_path
            attempt.host_boot_id = identity.host_boot_id
            attempt.process_started_at = datetime.now(UTC)
            attempt.metrics = metrics
        return WriteDisposition.APPLIED

    async def record_process_exit(self, summary: ExecutionSummary) -> WriteDisposition:
        owner = self.ownership
        factory = _get_session_factory()
        async with factory() as session, session.begin():
            disposition = await _claim_job(
                session,
                owner,
                expected_phases=("running", "stopping"),
                expected_desired_states=("run", "cancel"),
                values={"updated_at": datetime.now(UTC)},
            )
            if disposition != WriteDisposition.APPLIED:
                return disposition
            attempt = await session.scalar(
                select(JobAttempt)
                .where(
                    JobAttempt.id == owner.attempt_id,
                    JobAttempt.job_id == owner.job_id,
                    JobAttempt.fence_token == owner.fence_token,
                    JobAttempt.phase.in_(("running", "stopping")),
                )
                .with_for_update()
            )
            if attempt is None:
                raise RuntimeError("fenced attempt changed while recording process exit")
            metrics = dict(attempt.metrics or {})
            metrics["process"] = {
                "stdout_bytes": summary.stdout.total_bytes,
                "stderr_bytes": summary.stderr.total_bytes,
                "stdout_truncated": summary.stdout.truncated,
                "stderr_truncated": summary.stderr.truncated,
                "termination_stage": summary.termination_stage.value,
            }
            attempt.exit_code = summary.exit_code
            attempt.exit_signal = summary.exit_signal
            attempt.metrics = metrics
        return WriteDisposition.APPLIED

    async def unsafe(self, reason: str) -> WriteDisposition:
        error = self.definition.error.validate(
            {
                "code": "unsafe_process_identity",
                "summary": reason[:500],
                "diagnostics": {},
            },
            version=self.definition.error.current_version,
        ).model_dump(mode="json")
        return await self._terminal(
            outcome="unsafe",
            dispatch_disposition="failed",
            result=None,
            error=error,
            expected_desired_states=("run", "cancel"),
            attempt_outcome="interrupted",
        )

    async def interrupt(self, reason: str) -> WriteDisposition:
        """Return a positively dead orphan to queued for PgQueuer redelivery."""
        owner = self.ownership
        now = datetime.now(UTC)
        error = self.definition.error.validate(
            {
                "code": "worker_lost",
                "summary": reason[:500],
                "diagnostics": {},
            },
            version=self.definition.error.current_version,
        ).model_dump(mode="json")
        factory = _get_session_factory()
        async with factory() as session, session.begin():
            disposition = await _claim_job(
                session,
                owner,
                expected_phases=("running", "stopping"),
                expected_desired_states=("run",),
                values={"phase": "queued", "error": error},
            )
            if disposition != WriteDisposition.APPLIED:
                return disposition
            attempt_result = await session.execute(
                update(JobAttempt)
                .where(
                    JobAttempt.id == owner.attempt_id,
                    JobAttempt.job_id == owner.job_id,
                    JobAttempt.fence_token == owner.fence_token,
                    JobAttempt.phase.in_(("running", "stopping")),
                )
                .values(
                    phase="finished",
                    outcome="interrupted",
                    failure_class="worker_lost",
                    finished_at=now,
                    error=error,
                )
            )
            if attempt_result.rowcount != 1:
                raise RuntimeError("fenced attempt changed during orphan reconciliation")
            session.add(
                JobEvent(
                    job_id=owner.job_id,
                    attempt_id=owner.attempt_id,
                    event_key="attempt.interrupted",
                    state="queued",
                    message=f"{self.definition.job_type} interrupted after worker loss",
                    detail=error,
                )
            )
        return WriteDisposition.APPLIED

    async def record_publish_intent(self, intent: dict[str, Any]) -> WriteDisposition:
        """Durably record bounded publication intent before any destination mutation."""
        owner = self.ownership
        factory = _get_session_factory()
        async with factory() as session, session.begin():
            disposition = await _claim_job(
                session,
                owner,
                expected_phases=("running",),
                expected_desired_states=("run",),
                values={"updated_at": datetime.now(UTC)},
            )
            if disposition != WriteDisposition.APPLIED:
                return disposition
            attempt = await session.scalar(
                select(JobAttempt)
                .where(
                    JobAttempt.id == owner.attempt_id,
                    JobAttempt.job_id == owner.job_id,
                    JobAttempt.fence_token == owner.fence_token,
                    JobAttempt.phase == "running",
                )
                .with_for_update()
            )
            if attempt is None:
                raise RuntimeError("fenced attempt changed while recording publish intent")
            metrics = dict(attempt.metrics or {})
            metrics["publish_intent"] = intent
            attempt.metrics = metrics
        return WriteDisposition.APPLIED

    async def publish_atomic(
        self,
        action: Callable[[], None],
        evidence: dict[str, Any],
    ) -> WriteDisposition:
        """Recheck the fence under row lock, atomically replace, then seal evidence."""
        owner = self.ownership
        factory = _get_session_factory()
        async with factory() as session, session.begin():
            disposition = await _claim_job(
                session,
                owner,
                expected_phases=("running",),
                expected_desired_states=("run",),
                values={"updated_at": datetime.now(UTC)},
            )
            if disposition != WriteDisposition.APPLIED:
                return disposition
            attempt = await session.scalar(
                select(JobAttempt)
                .where(
                    JobAttempt.id == owner.attempt_id,
                    JobAttempt.job_id == owner.job_id,
                    JobAttempt.fence_token == owner.fence_token,
                    JobAttempt.phase == "running",
                )
                .with_for_update()
            )
            if attempt is None:
                raise RuntimeError("fenced attempt changed at publication boundary")
            action()
            metrics = dict(attempt.metrics or {})
            metrics["publication"] = evidence
            attempt.metrics = metrics
        return WriteDisposition.APPLIED

    async def _terminal(
        self,
        *,
        outcome: str,
        dispatch_disposition: str,
        result: dict[str, Any] | None,
        error: dict[str, Any] | None,
        expected_desired_states: tuple[str, ...],
        attempt_outcome: str | None = None,
    ) -> WriteDisposition:
        owner = self.ownership
        now = datetime.now(UTC)
        factory = _get_session_factory()
        async with factory() as session, session.begin():
            disposition = await _claim_job(
                session,
                owner,
                expected_phases=("running", "stopping"),
                expected_desired_states=expected_desired_states,
                values={
                    "phase": "terminal",
                    "outcome": outcome,
                    "result": result,
                    "error": error,
                    "terminal_at": now,
                },
            )
            if disposition != WriteDisposition.APPLIED:
                return disposition
            attempt_result = await session.execute(
                update(JobAttempt)
                .where(
                    JobAttempt.id == owner.attempt_id,
                    JobAttempt.job_id == owner.job_id,
                    JobAttempt.fence_token == owner.fence_token,
                    JobAttempt.phase.in_(("running", "stopping")),
                )
                .values(
                    phase="finished",
                    outcome=attempt_outcome or outcome,
                    finished_at=now,
                    error=error,
                )
            )
            dispatch_result = await session.execute(
                update(JobDispatch)
                .where(
                    JobDispatch.job_id == owner.job_id,
                    JobDispatch.generation == owner.dispatch_generation,
                    JobDispatch.disposition == "active",
                )
                .values(disposition=dispatch_disposition, ended_at=now)
            )
            if attempt_result.rowcount != 1 or dispatch_result.rowcount != 1:
                raise RuntimeError("fenced audit changed during terminal transaction")
            session.add(
                JobEvent(
                    job_id=owner.job_id,
                    attempt_id=owner.attempt_id,
                    event_key=f"job.{outcome}",
                    state=outcome,
                    message=f"{self.definition.job_type} {outcome}",
                    detail={"result": result} if result is not None else error,
                )
            )
        return WriteDisposition.APPLIED


async def _claim_job(
    session: Any,
    owner: AttemptOwnership,
    *,
    expected_phases: tuple[str, ...],
    expected_desired_states: tuple[str, ...],
    values: dict[str, Any],
) -> WriteDisposition:
    result = await session.execute(
        update(Job)
        .where(
            Job.id == owner.job_id,
            Job.current_attempt_id == owner.attempt_id,
            Job.fence_token == owner.fence_token,
            Job.dispatch_generation == owner.dispatch_generation,
            Job.phase.in_(expected_phases),
            Job.desired_state.in_(expected_desired_states),
            Job.outcome.is_(None),
        )
        .values(**values)
    )
    if result.rowcount == 1:
        return WriteDisposition.APPLIED
    current = await session.scalar(select(Job).where(Job.id == owner.job_id))
    if (
        current is None
        or current.current_attempt_id != owner.attempt_id
        or current.fence_token != owner.fence_token
        or current.dispatch_generation != owner.dispatch_generation
    ):
        return WriteDisposition.STALE
    return WriteDisposition.CONFLICT


def _safe_error(exc: BaseException, *, cancelled: bool) -> dict[str, Any]:
    if cancelled:
        code = "cancelled"
    else:
        code = re.sub(r"(?<!^)(?=[A-Z])", "_", type(exc).__name__).lower()
        code = re.sub(r"[^a-z0-9_]+", "_", code).strip("_") or "execution_error"
    return {"code": code[:80], "summary": (str(exc) or code)[:500]}
