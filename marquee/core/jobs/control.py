"""Optimistic capability-checked commands for canonical product jobs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from marquee.core.configuration_cache import configuration_provider
from marquee.core.jobs.contracts import JobAction
from marquee.core.jobs.manifest import JOB_DEFINITION_REGISTRY
from marquee.core.jobs.pgqueuer_gateway import (
    ENTRYPOINT_CONTROL,
    MAX_PRIORITY,
    MIN_PRIORITY,
    PAYLOAD_VERSION,
    PgQueuerGatewayError,
    pgqueuer_gateway,
)
from marquee.core.jobs.policies import ActionContext, allowed_actions
from marquee.models.job import Job, JobDispatch, JobEvent


@dataclass(frozen=True)
class JobControlError(Exception):
    code: str
    message: str
    job_id: str
    current_phase: str | None = None
    current_outcome: str | None = None
    current_desired_state: str | None = None
    current_version: int | None = None
    context: dict[str, str | int | float | bool | None] | None = None

    def __str__(self) -> str:
        return self.message


@dataclass(frozen=True)
class JobControlResult:
    action: JobAction
    job: Job
    execution_class: str
    original_job_id: str | None = None
    replacement_job_id: str | None = None


def _conflict(job: Job, code: str, message: str, **context: Any) -> JobControlError:
    return JobControlError(
        code=code,
        message=message,
        job_id=job.id,
        current_phase=job.phase,
        current_outcome=job.outcome,
        current_desired_state=job.desired_state,
        current_version=job.fence_token,
        context=context or None,
    )


async def _lock_job(session: AsyncSession, job_id: str) -> Job:
    job = await session.scalar(select(Job).where(Job.id == job_id).with_for_update())
    if job is None:
        raise JobControlError("job_not_found", "Job not found", job_id)
    return job


def _check_expected(job: Job, expected_fence_token: int) -> None:
    if job.fence_token != expected_fence_token:
        raise _conflict(
            job,
            "stale_job_version",
            "The job changed after this command was prepared.",
        )


def _available_actions(job: Job) -> frozenset[JobAction]:
    definition = JOB_DEFINITION_REGISTRY.find(job.type)
    if definition is None:
        raise _conflict(
            job,
            "unmigrated_job_command",
            "The stored job type has no command definition.",
        )
    context = ActionContext(
        phase=job.phase,
        desired_state=job.desired_state,
        outcome=job.outcome,
        active_attempt=job.current_attempt_id is not None,
        retryable=definition.enabled,
        logs_available=False,
        artifacts_available=False,
    )
    return allowed_actions(definition.action_policy, context)


def _require_action(job: Job, action: JobAction) -> None:
    if action not in _available_actions(job):
        raise _conflict(
            job,
            "action_not_allowed",
            f"{action.value} is not available in the current job state.",
            action=action.value,
        )


async def cancel(
    session: AsyncSession, *, job_id: str, expected_fence_token: int
) -> JobControlResult:
    async with session.begin():
        job = await _lock_job(session, job_id)
        _check_expected(job, expected_fence_token)
        _require_action(job, JobAction.CANCEL)
        if job.pgq_job_id is None:
            raise _conflict(
                job,
                "unmigrated_job_command",
                "This job has no canonical transport ticket to cancel.",
            )
        definition = JOB_DEFINITION_REGISTRY.get(job.type)
        try:
            await pgqueuer_gateway.cancel_known_ticket(session, job_id=job.id)
        except PgQueuerGatewayError as exc:
            raise _conflict(job, "action_not_allowed", str(exc), action="cancel") from exc
        job.fence_token += 1
    await session.refresh(job)
    return JobControlResult(JobAction.CANCEL, job, definition.execution_class.value)


async def set_paused(
    session: AsyncSession,
    *,
    job_id: str,
    expected_fence_token: int,
    paused: bool,
) -> JobControlResult:
    action = JobAction.PAUSE if paused else JobAction.RESUME
    async with session.begin():
        job = await _lock_job(session, job_id)
        _check_expected(job, expected_fence_token)
        _require_action(job, action)
        definition = JOB_DEFINITION_REGISTRY.get(job.type)
        job.desired_state = "pause" if paused else "run"
        job.fence_token += 1
        session.add(
            JobEvent(
                job_id=job.id,
                event_key=f"job.{action.value}d",
                state=job.phase,
                message=f"Job {action.value} requested",
            )
        )
    await session.refresh(job)
    return JobControlResult(action, job, definition.execution_class.value)


async def change_priority(
    session: AsyncSession,
    *,
    job_id: str,
    expected_fence_token: int,
    priority: int,
) -> JobControlResult:
    async with session.begin():
        job = await _lock_job(session, job_id)
        _check_expected(job, expected_fence_token)
        _require_action(job, JobAction.CHANGE_PRIORITY)
        if not MIN_PRIORITY <= priority <= MAX_PRIORITY:
            raise _conflict(
                job,
                "action_not_allowed",
                f"priority must be between {MIN_PRIORITY} and {MAX_PRIORITY}",
                action=JobAction.CHANGE_PRIORITY.value,
            )
        definition = JOB_DEFINITION_REGISTRY.get(job.type)
        try:
            await pgqueuer_gateway.reprioritize_known_ticket(
                session, job_id=job.id, priority=priority
            )
        except PgQueuerGatewayError as exc:
            raise _conflict(
                job,
                "action_not_allowed",
                str(exc),
                action=JobAction.CHANGE_PRIORITY.value,
                execution_class=definition.execution_class.value,
            ) from exc
        if job.pgq_job_id is None:
            session.add(
                JobEvent(
                    job_id=job.id,
                    event_key="job.priority_changed",
                    state=job.phase,
                    message="Job priority changed within its execution class",
                    detail={
                        "priority": priority,
                        "execution_class": definition.execution_class.value,
                    },
                )
            )
        job.fence_token += 1
    await session.refresh(job)
    return JobControlResult(JobAction.CHANGE_PRIORITY, job, definition.execution_class.value)


async def retry(
    session: AsyncSession, *, job_id: str, expected_fence_token: int
) -> JobControlResult:
    async with session.begin():
        original = await _lock_job(session, job_id)
        _check_expected(original, expected_fence_token)
        _require_action(original, JobAction.RETRY)
        definition = JOB_DEFINITION_REGISTRY.get(original.type)
        if original.type != "system_noop":
            raise _conflict(
                original,
                "unmigrated_job_command",
                "Retry dispatch is not enabled for this job definition.",
            )

        request = definition.request.validate(
            original.request, version=original.payload_version
        ).model_dump(mode="json", exclude_none=True)
        now = datetime.now(UTC)
        replacement_id = uuid4().hex
        generation = 1
        priority = original.priority
        dedupe_key = f"marquee:{replacement_id}:{generation}"
        configuration = configuration_provider.snapshot_for(definition.configuration_keys)
        replacement = Job(
            id=replacement_id,
            type=original.type,
            payload_version=PAYLOAD_VERSION,
            request=request,
            phase="queued",
            desired_state="run",
            dispatch_generation=generation,
            priority=priority,
            eligible_at=now,
            idempotency_key=f"system_noop:retry-{original.id}-{expected_fence_token}",
            configuration_version=configuration.version,
            configuration_snapshot=configuration.values,
            parent_id=original.parent_id,
            root_id=original.root_id,
            correlation_id=original.correlation_id,
            retry_of_job_id=original.id,
            trigger_kind=original.trigger_kind,
            initiator=original.initiator,
            feature_area=original.feature_area,
            presentation_family=original.presentation_family,
            subject_kind=original.subject_kind,
            subject_reference=original.subject_reference,
            subject_snapshot=original.subject_snapshot,
            queued_at=now,
        )
        dispatch = JobDispatch(
            job_id=replacement_id,
            generation=generation,
            pgq_job_id=None,
            entrypoint=ENTRYPOINT_CONTROL,
            dedupe_key=dedupe_key,
            priority=priority,
            eligible_at=now,
            disposition="active",
        )
        event = JobEvent(
            job_id=replacement_id,
            event_key="job.retried",
            state="queued",
            message="Retry successor queued",
            detail={"original_job_id": original.id},
        )
        session.add_all([replacement, dispatch, event])
        await pgqueuer_gateway.enqueue(
            session,
            job_id=replacement_id,
            entrypoint=ENTRYPOINT_CONTROL,
            payload_version=PAYLOAD_VERSION,
            dispatch_generation=generation,
            priority=priority,
            execute_after=None,
            dedupe_key=dedupe_key,
        )
        original.fence_token += 1
    await session.refresh(replacement)
    return JobControlResult(
        JobAction.RETRY,
        replacement,
        definition.execution_class.value,
        original_job_id=original.id,
        replacement_job_id=replacement.id,
    )
