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
from marquee.core.jobs.event_service import job_event_writer
from marquee.core.jobs.process_identity import ProcessIdentity
from marquee.core.jobs.process_launcher import ExecutionSummary
from marquee.core.jobs.progress_service import progress_writer
from marquee.core.jobs.terminal_decision import TerminalDecision
from marquee.database import _get_session_factory
from marquee.models.job import Job, JobAttempt, JobDispatch


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

    async def owns_current_attempt(self, session) -> bool:
        """Return whether this context still owns a running canonical attempt.

        Domain handlers use this read-only check inside their short projection
        transaction.  It prevents an obsolete delivery from publishing derived
        state after a newer fence has been admitted, without granting handlers
        any canonical job lifecycle mutation authority.
        """
        owner = self.ownership
        return (
            await session.scalar(
                select(Job.id)
                .join(JobAttempt, JobAttempt.job_id == Job.id)
                .where(
                    Job.id == owner.job_id,
                    Job.fence_token == owner.fence_token,
                    Job.phase.in_(("running", "stopping")),
                    JobAttempt.id == owner.attempt_id,
                    JobAttempt.fence_token == owner.fence_token,
                    JobAttempt.phase.in_(("running", "stopping")),
                )
                .limit(1)
            )
        ) is not None

    def terminal_decision(self, result: dict[str, Any]) -> TerminalDecision:
        document = self.definition.result.validate(
            result, version=self.definition.result.current_version
        )
        return self.definition.terminal_policy.decide(
            document,
            job_type=self.definition.job_type,
        )

    async def succeed(
        self,
        result: dict[str, Any],
        *,
        decision: TerminalDecision | None = None,
    ) -> WriteDisposition:
        document = self.definition.result.validate(
            result, version=self.definition.result.current_version
        )
        derived = self.definition.terminal_policy.decide(
            document,
            job_type=self.definition.job_type,
        )
        if decision is not None and decision != derived:
            raise ValueError("terminal decision changed between validation and persistence")
        decision = derived
        return await self._terminal(
            outcome=decision.job_outcome.value,
            dispatch_disposition=decision.dispatch_disposition.value,
            result=document.model_dump(mode="json"),
            error=None,
            expected_desired_states=("run",),
            attempt_outcome=decision.attempt_outcome.value,
            attention=decision.attention_document(),
        )

    async def fail(
        self, exc: BaseException, *, cancelled: bool = False
    ) -> WriteDisposition:
        outcome = "cancelled" if cancelled else "failed"
        error_payload = _safe_error(exc, cancelled=cancelled)
        error_model = self.definition.error.models[self.definition.error.current_version]
        attempt_has_intent = attempt_has_publication = False
        if "atomicity" in error_model.model_fields:
            attempt_has_intent, attempt_has_publication = await self._publication_state()
            uncertain = attempt_has_intent and not attempt_has_publication
            error_payload["atomicity"] = self._atomicity_evidence(
                published=attempt_has_publication,
                uncertain=uncertain,
            )
            if uncertain:
                outcome = "unsafe"
                error_payload.update(
                    code="publication_state_uncertain",
                    summary="Publication intent exists without sealed publication evidence",
                )
        if "stage" in error_model.model_fields:
            error_payload["stage"] = (
                "publication_reconciliation"
                if outcome == "unsafe"
                else ("cancelled" if cancelled else "execution")
            )
        error = self.definition.error.validate(
            error_payload,
            version=self.definition.error.current_version,
        ).model_dump(mode="json")
        return await self._terminal(
            outcome=outcome,
            dispatch_disposition="failed" if outcome == "unsafe" else outcome,
            result=None,
            error=error,
            expected_desired_states=(
                ("run", "cancel") if cancelled or outcome == "unsafe" else ("run",)
            ),
            attempt_outcome="interrupted" if outcome == "unsafe" else None,
        )

    async def retry(self, *, reason: str, delay_seconds: float) -> WriteDisposition:
        error_model = self.definition.error.models[self.definition.error.current_version]
        error_payload: dict[str, Any] = {
            "code": "retry_requested",
            "summary": (reason or "retry requested")[:500],
            "diagnostics": {"delay_seconds": delay_seconds},
        }
        if "atomicity" in error_model.model_fields:
            has_intent, has_publication = await self._publication_state()
            if has_intent or has_publication:
                return await self.unsafe(
                    "Mutation retry refused because publication cannot be proven absent",
                    code="mutation_retry_not_safe",
                    stage="retry_classification",
                )
            error_payload["stage"] = "retry_classification"
            error_payload["atomicity"] = self._atomicity_evidence(
                published=False,
                uncertain=False,
            )
        error = self.definition.error.validate(
            error_payload,
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
            await job_event_writer.append(
                session,
                job_id=owner.job_id,
                attempt_id=owner.attempt_id,
                event_key="job.retry_requested",
                state="retrying",
                message=f"{self.definition.job_type} retry requested",
                detail=error,
                canonical_version=owner.fence_token,
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

    async def unsafe(
        self,
        reason: str,
        *,
        code: str = "unsafe_process_identity",
        stage: str = "safety",
    ) -> WriteDisposition:
        error_model = self.definition.error.models[self.definition.error.current_version]
        payload: dict[str, Any] = {
            "code": code,
            "summary": reason[:500],
            "diagnostics": {},
        }
        if "atomicity" in error_model.model_fields:
            has_intent, has_publication = await self._publication_state()
            payload["atomicity"] = self._atomicity_evidence(
                published=has_publication,
                uncertain=has_intent and not has_publication,
            )
        if "stage" in error_model.model_fields:
            payload["stage"] = stage
        error = self.definition.error.validate(
            payload,
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
            await job_event_writer.append(
                session,
                job_id=owner.job_id,
                attempt_id=owner.attempt_id,
                event_key="attempt.interrupted",
                state="queued",
                message=f"{self.definition.job_type} interrupted after worker loss",
                detail=error,
                canonical_version=owner.fence_token,
            )
        return WriteDisposition.APPLIED

    async def _publication_state(self) -> tuple[bool, bool]:
        """Return durable intent/publication evidence for this exact fenced attempt."""
        owner = self.ownership
        factory = _get_session_factory()
        async with factory() as session:
            metrics = await session.scalar(
                select(JobAttempt.metrics).where(
                    JobAttempt.id == owner.attempt_id,
                    JobAttempt.job_id == owner.job_id,
                    JobAttempt.fence_token == owner.fence_token,
                )
            )
        values = metrics if isinstance(metrics, dict) else {}
        return values.get("publish_intent") is not None, values.get("publication") is not None

    def _atomicity_evidence(self, *, published: bool, uncertain: bool) -> dict[str, Any]:
        owner = self.ownership
        return {
            "group_id": f"job:{owner.job_id}:attempt:{owner.attempt_id}",
            "boundary": "single_target",
            "published": published,
            "rollback_available": False,
            "uncertain_state": uncertain,
        }

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
            # Only the most recent intent/publication pair is authoritative. Clearing
            # the prior publication makes a crash between a later intent and its
            # publication visibly ambiguous to workspace reconciliation.
            metrics.pop("publication", None)
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
        attention: dict[str, Any] | None = None,
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
                    "attention": attention,
                    "terminal_at": now,
                },
            )
            if disposition != WriteDisposition.APPLIED:
                return disposition
            job = await session.scalar(
                select(Job).where(Job.id == owner.job_id).with_for_update()
            )
            if job is None:
                raise RuntimeError("fenced terminal job disappeared")
            await progress_writer.terminalize(
                session, job, outcome=outcome, occurred_at=now
            )
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
            await job_event_writer.append(
                session,
                job_id=owner.job_id,
                attempt_id=owner.attempt_id,
                event_key=f"job.{outcome}",
                state=outcome,
                message=f"{self.definition.job_type} {outcome}",
                detail={"result": result} if result is not None else error,
                canonical_version=owner.fence_token,
            )
            from marquee.core.jobs.batches import project_terminal_child

            await project_terminal_child(session, job)
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
