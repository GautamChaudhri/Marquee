"""Registry-driven, pre-admission-gated, fenced PgQueuer delivery kernel."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal

from pgqueuer import RetryRequested
from pgqueuer.models import Context
from pgqueuer.models import Job as PgQueuerJob
from sqlalchemy import func, select, update

from marquee.config import settings
from marquee.core.jobs.artifact_service import (
    artifact_boundary,
    register_existing_physical_artifact,
    register_physical_artifact,
    register_validation_result_artifact,
    register_virtual_artifact,
)
from marquee.core.jobs.batches import project_active_child
from marquee.core.jobs.contracts import EffectSafety
from marquee.core.jobs.definitions import JobDefinition
from marquee.core.jobs.event_service import job_event_writer
from marquee.core.jobs.execution_io import ExecutionIO
from marquee.core.jobs.execution_progress import ExecutionProgress
from marquee.core.jobs.fenced_writer import (
    AttemptOwnership,
    FencedWriter,
    WriteDisposition,
)
from marquee.core.jobs.log_capture import AttemptLogSink
from marquee.core.jobs.manifest import JOB_DEFINITION_REGISTRY
from marquee.core.jobs.orphan_reconciliation import assess_candidate, candidate_for_attempt
from marquee.core.jobs.policies import RetryClassification
from marquee.core.jobs.process_identity import read_boot_id
from marquee.core.jobs.process_launcher import ProcessLauncher
from marquee.core.jobs.safety_gates import (
    SafetyGateCancelledError,
    SafetyGateConnectionLostError,
    SafetyGateHandle,
    SafetyGateService,
    SafetyGateTimeoutError,
    SafetyRequirements,
    requirements_for_policy,
)
from marquee.core.jobs.terminal_decision import TerminalDecision, WorkspaceDisposition
from marquee.core.jobs.workspaces import AttemptWorkspace, AttemptWorkspaceManager
from marquee.database import _get_session_factory
from marquee.models import RuntimeInstance
from marquee.models.job import Job, JobAttempt, JobDispatch

TRANSPORT_KEYS = frozenset({"dispatch_generation", "job_id", "payload_version"})
logger = logging.getLogger(__name__)


class DeliveryRejectedError(RuntimeError):
    """Raise to make PgQueuer hold malformed or unsafe work."""


class HandlerExecutionTimeoutError(TimeoutError):
    """The definition-owned handler execution deadline expired."""


@dataclass(frozen=True, slots=True)
class TransportPayload:
    job_id: str
    payload_version: int
    dispatch_generation: int


@dataclass(frozen=True, slots=True)
class DeliveryIdentity:
    canonical_job_id: str
    dispatch_generation: int
    pgqueuer_job_id: int
    pgqueuer_attempt: int
    definition_key: str
    definition_version: int


@dataclass(frozen=True, slots=True)
class AttemptIdentity:
    attempt_id: int
    number: int
    fence_token: int
    worker_node: str
    host_boot_id: str


@dataclass(frozen=True, slots=True)
class PreflightDelivery:
    delivery: DeliveryIdentity
    definition: JobDefinition
    request: Mapping[str, Any]
    requirements: SafetyRequirements


@dataclass(frozen=True, slots=True)
class AdmittedDelivery:
    delivery: DeliveryIdentity
    attempt: AttemptIdentity
    definition: JobDefinition
    request: Mapping[str, Any]
    configuration: Mapping[str, Any]
    subject: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class ExecutionContext:
    delivery: DeliveryIdentity
    attempt: AttemptIdentity
    request: Mapping[str, Any]
    configuration: Mapping[str, Any]
    subject: Mapping[str, Any]
    definition: JobDefinition
    cancellation: Any
    safety_gates: SafetyGateHandle
    workspace: AttemptWorkspace
    process_launcher: ProcessLauncher
    progress: ExecutionProgress
    io: ExecutionIO
    log_sink: AttemptLogSink | None
    writer: FencedWriter
    # Domain-projection session factory. Handlers open short, idempotent transactions to
    # write derived projections only; they never hold one across a launcher or network wait,
    # and never touch canonical job lifecycle (the fenced writer owns that).
    session_factory: Callable[[], Any]


KernelHandler = Callable[[ExecutionContext], Awaitable[dict[str, Any]]]


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DeliveryRejectedError(f"duplicate transport payload key: {key}")
        result[key] = value
    return result


def parse_transport_payload(payload: bytes | None) -> TransportPayload:
    """Decode exact finite UTF-8 JSON and reject every contract deviation."""
    if payload is None:
        raise DeliveryRejectedError("control payload is required")
    try:
        decoded = payload.decode("utf-8", errors="strict")
        value = json.loads(
            decoded,
            object_pairs_hook=_strict_object,
            parse_constant=lambda constant: (_ for _ in ()).throw(
                DeliveryRejectedError(f"non-finite transport value: {constant}")
            ),
        )
    except UnicodeDecodeError as exc:
        raise DeliveryRejectedError("control payload must be UTF-8") from exc
    except json.JSONDecodeError as exc:
        raise DeliveryRejectedError("control payload must be valid JSON") from exc
    if not isinstance(value, dict) or set(value) != TRANSPORT_KEYS:
        raise DeliveryRejectedError("control payload has missing or extra keys")

    job_id = value["job_id"]
    payload_version = value["payload_version"]
    generation = value["dispatch_generation"]
    if not isinstance(job_id, str) or not job_id or len(job_id) > 32:
        raise DeliveryRejectedError("control job_id is invalid")
    if type(payload_version) is not int or payload_version != 1:
        raise DeliveryRejectedError("control payload_version is unsupported")
    if type(generation) is not int or generation < 1:
        raise DeliveryRejectedError("control dispatch_generation is invalid")
    return TransportPayload(job_id, payload_version, generation)


def _immutable(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType({key: _immutable(item) for key, item in value.items()})
    if isinstance(value, list | tuple):
        return tuple(_immutable(item) for item in value)
    return value


async def _preflight(
    transport_job: PgQueuerJob, payload: TransportPayload
) -> PreflightDelivery | None:
    factory = _get_session_factory()
    async with factory() as session:
        job = await session.get(Job, payload.job_id)
        if job is None:
            raise DeliveryRejectedError("canonical job does not exist")
        dispatch = await session.scalar(
            select(JobDispatch).where(
                JobDispatch.job_id == payload.job_id,
                JobDispatch.generation == payload.dispatch_generation,
            )
        )
        if dispatch is None:
            raise DeliveryRejectedError("canonical dispatch does not exist")
        if (
            job.phase == "terminal"
            or job.outcome is not None
            or dispatch.disposition != "active"
        ):
            if (
                job.outcome == "unsafe"
                and dispatch.disposition == "failed"
                and int(transport_job.id) == dispatch.pgq_job_id
            ):
                raise DeliveryRejectedError(
                    "unsafe canonical outcome is held for operator resolution"
                )
            return None
        if (
            payload.dispatch_generation != job.dispatch_generation
            or int(transport_job.id) != job.pgq_job_id
            or int(transport_job.id) != dispatch.pgq_job_id
        ):
            return None
        try:
            definition = JOB_DEFINITION_REGISTRY.for_dispatch(
                job.type, entrypoint=str(transport_job.entrypoint)
            )
            request = definition.request.validate(
                job.request, version=job.payload_version
            ).model_dump(mode="json")
        except (TypeError, ValueError, RuntimeError) as exc:
            raise DeliveryRejectedError("canonical definition or request is invalid") from exc
        delivery = DeliveryIdentity(
            canonical_job_id=job.id,
            dispatch_generation=payload.dispatch_generation,
            pgqueuer_job_id=int(transport_job.id),
            pgqueuer_attempt=transport_job.attempts,
            definition_key=definition.job_type,
            definition_version=job.payload_version,
        )
        requirements = requirements_for_policy(
            definition.safety_policy,
            allocation_identity=f"{job.id}:{payload.dispatch_generation}:{transport_job.attempts}",
            media_file_identity=(
                f"{job.subject_kind}:{job.subject_reference}:poster"
                if definition.safety_policy.media_file
                else None
            ),
        )
        return PreflightDelivery(
            delivery=delivery,
            definition=definition,
            request=_immutable(request),
            requirements=requirements,
        )


async def _publish_wait(job_id: str, generation: int, reason: str) -> None:
    factory = _get_session_factory()
    async with factory() as session, session.begin():
        await session.execute(
            update(Job)
            .where(
                Job.id == job_id,
                Job.dispatch_generation == generation,
                Job.phase == "queued",
                Job.outcome.is_(None),
            )
            .values(attention={"code": "safety_wait", "summary": reason})
        )


def _admission_deferral_delay(defer_count: int) -> timedelta | None:
    """Bound gate contention before it becomes a canonical operational failure."""
    delays = (timedelta(seconds=1), timedelta(seconds=5), timedelta(seconds=30))
    if defer_count < 1 or defer_count > len(delays):
        return None
    return delays[defer_count - 1]


async def _defer_pre_admission(
    payload: TransportPayload,
    preflight: PreflightDelivery,
    transport_job: PgQueuerJob,
    *,
    cause: str,
) -> timedelta | None:
    """Persist a pending deferral without creating or fencing an attempt.

    PgQueuer owns the ensuing retry transaction. The pending canonical event is
    deliberately recoverable: the transport-intent monitor either observes the
    queued retry or uses the supported PgQueuer retry primitive on the same
    ticket lineage if a callback dies between these two durability boundaries.
    """
    factory = _get_session_factory()
    exhausted = False
    async with factory() as session, session.begin():
        job = await session.scalar(
            select(Job).where(Job.id == payload.job_id).with_for_update()
        )
        dispatch = await session.scalar(
            select(JobDispatch)
            .where(
                JobDispatch.job_id == payload.job_id,
                JobDispatch.generation == payload.dispatch_generation,
            )
            .with_for_update()
        )
        if (
            job is None
            or dispatch is None
            or job.phase != "queued"
            or job.desired_state != "run"
            or job.dispatch_generation != payload.dispatch_generation
            or job.pgq_job_id != int(transport_job.id)
            or dispatch.pgq_job_id != int(transport_job.id)
            or dispatch.disposition != "active"
        ):
            return None
        existing = job.attention or {}
        previous_count = (
            int(existing.get("defer_count", 0))
            if existing.get("code") == "admission_deferral_pending"
            else 0
        )
        defer_count = previous_count + 1
        delay = _admission_deferral_delay(defer_count)
        now = datetime.now(UTC)
        if delay is None:
            error = preflight.definition.error.validate(
                {
                    "code": "admission_deferral_exhausted",
                    "summary": "Safety-gate admission exhausted its bounded deferral budget.",
                    "diagnostics": {"defer_count": defer_count, "cause": cause},
                },
                version=preflight.definition.error.current_version,
            ).model_dump(mode="json")
            job.phase = "terminal"
            job.outcome = "failed"
            job.error = error
            job.terminal_at = now
            job.attention = None
            dispatch.disposition = "failed"
            dispatch.ended_at = now
            await job_event_writer.append(
                session,
                job_id=job.id,
                event_key="job.failed",
                state="failed",
                message="Safety-gate admission deferral budget exhausted",
                detail=error,
            )
            exhausted = True
        else:
            next_eligible_at = now + delay
            job.eligible_at = next_eligible_at
            dispatch.eligible_at = next_eligible_at
            job.attention = {
                "code": "admission_deferral_pending",
                "summary": "Safety-gate admission will be deferred by PgQueuer",
                "cause": cause,
                "defer_count": defer_count,
                "delay_seconds": delay.total_seconds(),
                "next_eligible_at": next_eligible_at.isoformat(),
            }
            await job_event_writer.append(
                session,
                job_id=job.id,
                event_key="job.admission_deferral_requested",
                state="queued",
                message="Safety-gate admission requested bounded PgQueuer deferral",
                detail={
                    "cause": cause,
                    "defer_count": defer_count,
                    "delay_seconds": delay.total_seconds(),
                },
            )
            return delay
    if exhausted:
        raise DeliveryRejectedError("safety-gate admission deferral budget exhausted")
    return None


async def _admission_cancelled(
    payload: TransportPayload, context: Context
) -> bool:
    if context.cancellation.cancel_called:
        return True
    factory = _get_session_factory()
    async with factory() as session:
        row = (
            await session.execute(
                select(Job.desired_state, Job.phase, Job.dispatch_generation).where(
                    Job.id == payload.job_id
                )
            )
        ).one_or_none()
    return bool(
        row is None
        or row.dispatch_generation != payload.dispatch_generation
        or row.phase == "terminal"
        or row.desired_state != "run"
    )


async def _cancellation_intent_won(ownership: AttemptOwnership) -> bool:
    """Confirm this exact active writer lost completion to durable cancellation intent."""
    factory = _get_session_factory()
    async with factory() as session:
        return (
            await session.scalar(
                select(Job.id)
                .where(
                    Job.id == ownership.job_id,
                    Job.current_attempt_id == ownership.attempt_id,
                    Job.fence_token == ownership.fence_token,
                    Job.dispatch_generation == ownership.dispatch_generation,
                    Job.phase.in_(("running", "stopping")),
                    Job.desired_state == "cancel",
                    Job.outcome.is_(None),
                )
                .limit(1)
            )
        ) is not None


async def _apply_pre_admission_intent(payload: TransportPayload) -> None:
    factory = _get_session_factory()
    async with factory() as session, session.begin():
        job = await session.scalar(
            select(Job).where(Job.id == payload.job_id).with_for_update()
        )
        dispatch = await session.scalar(
            select(JobDispatch)
            .where(
                JobDispatch.job_id == payload.job_id,
                JobDispatch.generation == payload.dispatch_generation,
            )
            .with_for_update()
        )
        if job is None or dispatch is None or job.phase == "terminal":
            return
        if job.dispatch_generation != payload.dispatch_generation:
            return
        now = datetime.now(UTC)
        if job.desired_state == "cancel":
            job.phase = "terminal"
            job.outcome = "cancelled"
            job.terminal_at = now
            job.attention = None
            dispatch.disposition = "cancelled"
            dispatch.ended_at = now
            await job_event_writer.append(
                session,
                job_id=job.id,
                event_key="job.cancelled",
                state="cancelled",
                message="cancelled before admission",
            )
            from marquee.core.jobs.batches import project_terminal_child

            await project_terminal_child(session, job)
        elif job.desired_state == "pause":
            job.pgq_job_id = None
            job.attention = None
            dispatch.disposition = "cancelled"
            dispatch.ended_at = now


async def _start_attempt(
    session: Any,
    job: Job,
    transport_job: PgQueuerJob,
    preflight: PreflightDelivery,
    runtime_instance_id: str | None,
) -> AdmittedDelivery:
    now = datetime.now(UTC)
    last_attempt = await session.scalar(
        select(func.max(JobAttempt.number)).where(JobAttempt.job_id == job.id)
    )
    job.fence_token += 1
    attempt = JobAttempt(
        job_id=job.id,
        number=(last_attempt or 0) + 1,
        fence_token=job.fence_token,
        pgq_job_id=int(transport_job.id),
        transport_attempt=transport_job.attempts,
        worker_node_id=settings.JOB_WORKER_NODE_ID,
        runtime_instance_id=runtime_instance_id,
        host_boot_id=read_boot_id(),
        phase="running",
        admitted_at=now,
        started_at=now,
    )
    session.add(attempt)
    await session.flush()
    job.phase = "running"
    job.current_attempt_id = attempt.id
    job.started_at = job.started_at or now
    job.attention = None
    await job_event_writer.append(
        session,
        job_id=job.id,
        attempt_id=attempt.id,
        event_key="attempt.started",
        state="running",
        message=f"{preflight.definition.job_type} started",
        detail={"dispatch_generation": preflight.delivery.dispatch_generation},
    )
    await project_active_child(session, job)
    return AdmittedDelivery(
        delivery=preflight.delivery,
        attempt=AttemptIdentity(
            attempt_id=attempt.id,
            number=attempt.number,
            fence_token=attempt.fence_token,
            worker_node=str(attempt.worker_node_id),
            host_boot_id=str(attempt.host_boot_id),
        ),
        definition=preflight.definition,
        request=preflight.request,
        configuration=_immutable(job.configuration_snapshot or {}),
        subject=_immutable(job.subject_snapshot or {}),
    )


def _recovery_error(
    definition: JobDefinition,
    attempt: JobAttempt,
    reason: str,
) -> tuple[dict[str, Any], str, str]:
    error_model = definition.error.models[definition.error.current_version]
    payload: dict[str, Any] = {
        "code": "worker_lost",
        "summary": reason[:500],
        "diagnostics": {},
    }
    outcome = "interrupted"
    failure_class = "worker_lost"
    if "atomicity" in error_model.model_fields:
        payload.update(
            code="retry_requested",
            stage="retry_classification",
            atomicity={
                "group_id": f"job:{attempt.job_id}:attempt:{attempt.id}",
                "boundary": "single_target",
                "published": False,
                "rollback_available": False,
                "uncertain_state": False,
            },
        )
        payload["diagnostics"] = {"delay_seconds": 0}
        outcome = "retrying"
        failure_class = "transient"
    error = definition.error.validate(
        payload,
        version=definition.error.current_version,
    ).model_dump(mode="json")
    return error, outcome, failure_class


async def _take_over_attempt(
    transport_job: PgQueuerJob,
    preflight: PreflightDelivery,
    runtime_instance_id: str | None,
    *,
    attempt_id: int,
    fence_token: int,
    reason: str,
) -> AdmittedDelivery | None:
    """Atomically close one stale audit and admit its replay-safe replacement."""
    factory = _get_session_factory()
    async with factory() as session, session.begin():
        job = await session.scalar(
            select(Job).where(Job.id == preflight.delivery.canonical_job_id).with_for_update()
        )
        dispatch = await session.scalar(
            select(JobDispatch)
            .where(
                JobDispatch.job_id == preflight.delivery.canonical_job_id,
                JobDispatch.generation == preflight.delivery.dispatch_generation,
            )
            .with_for_update()
        )
        attempt = await session.scalar(
            select(JobAttempt).where(JobAttempt.id == attempt_id).with_for_update()
        )
        if (
            job is None
            or dispatch is None
            or attempt is None
            or job.current_attempt_id != attempt_id
            or job.fence_token != fence_token
            or attempt.fence_token != fence_token
            or job.phase not in {"running", "stopping"}
            or attempt.phase not in {"running", "stopping"}
            or job.desired_state != "run"
            or job.outcome is not None
            or dispatch.disposition != "active"
            or dispatch.pgq_job_id != int(transport_job.id)
        ):
            return None
        runtime = (
            await session.scalar(
                select(RuntimeInstance)
                .where(RuntimeInstance.id == attempt.runtime_instance_id)
                .with_for_update()
            )
            if attempt.runtime_instance_id is not None
            else None
        )
        now = datetime.now(UTC)
        if (
            runtime is not None
            and runtime.stopped_at is None
            and runtime.readiness != "stopped"
            and runtime.heartbeat_expires_at > now
        ):
            return None
        if preflight.definition.effect_safety == EffectSafety.UNSAFE_MUTATION:
            return None
        error, outcome, failure_class = _recovery_error(
            preflight.definition,
            attempt,
            reason,
        )
        attempt.phase = "finished"
        attempt.outcome = outcome
        attempt.failure_class = failure_class
        attempt.finished_at = now
        attempt.error = error
        job.error = error
        await job_event_writer.append(
            session,
            job_id=job.id,
            attempt_id=attempt.id,
            event_key="attempt.interrupted",
            state="queued",
            message=f"{preflight.definition.job_type} interrupted after worker loss",
            detail=error,
            canonical_version=fence_token,
        )
        return await _start_attempt(
            session,
            job,
            transport_job,
            preflight,
            runtime_instance_id,
        )


async def _admit_delivery(
    transport_job: PgQueuerJob,
    payload: TransportPayload,
    preflight: PreflightDelivery,
    runtime_instance_id: str | None,
) -> AdmittedDelivery | None:
    factory = _get_session_factory()
    rejection: DeliveryRejectedError | None = None
    retry_reason: str | None = None
    recovery_attempt_id: int | None = None
    admitted: AdmittedDelivery | None = None
    async with factory() as session, session.begin():
        job = await session.scalar(
            select(Job).where(Job.id == payload.job_id).with_for_update()
        )
        dispatch = await session.scalar(
            select(JobDispatch)
            .where(
                JobDispatch.job_id == payload.job_id,
                JobDispatch.generation == payload.dispatch_generation,
            )
            .with_for_update()
        )
        if job is None or dispatch is None:
            rejection = DeliveryRejectedError("canonical admission rows disappeared")
        elif (
            job.phase == "terminal"
            or job.outcome is not None
            or dispatch.disposition != "active"
        ):
            pass
        elif (
            job.dispatch_generation != preflight.delivery.dispatch_generation
            or job.pgq_job_id != preflight.delivery.pgqueuer_job_id
            or dispatch.pgq_job_id != preflight.delivery.pgqueuer_job_id
        ):
            dispatch.disposition = "stale"
            dispatch.ended_at = datetime.now(UTC)
        elif job.desired_state != "run":
            pass
        elif (
            job.type != preflight.definition.job_type
            or job.payload_version != preflight.delivery.definition_version
        ):
            rejection = DeliveryRejectedError("canonical definition changed before admission")
        elif job.phase == "running":
            active_attempt = await session.scalar(
                select(JobAttempt)
                .where(
                    JobAttempt.job_id == job.id,
                    JobAttempt.phase.in_(("running", "stopping")),
                )
                .order_by(JobAttempt.number.desc())
                .limit(1)
                .with_for_update()
            )
            # A running audit is reconciled at worker startup using durable process
            # identity. Delivery never guesses that another worker/process is dead.
            if active_attempt is None:
                rejection = DeliveryRejectedError("running job has no current attempt")
            else:
                runtime = (
                    await session.get(RuntimeInstance, active_attempt.runtime_instance_id)
                    if active_attempt.runtime_instance_id is not None
                    else None
                )
                now = datetime.now(UTC)
                if (
                    runtime is not None
                    and (
                        runtime.stopped_at is not None
                        or runtime.readiness == "stopped"
                        or runtime.heartbeat_expires_at <= now
                    )
                ):
                    recovery_attempt_id = active_attempt.id
                else:
                    retry_reason = "canonical job still has an active attempt"
        elif job.phase == "queued":
            admitted = await _start_attempt(
                session, job, transport_job, preflight, runtime_instance_id
            )
        else:
            rejection = DeliveryRejectedError(
                f"canonical job phase {job.phase!r} cannot be delivered"
            )
    if rejection is not None:
        raise rejection
    if recovery_attempt_id is not None:
        candidate = await candidate_for_attempt(recovery_attempt_id)
        if candidate is None:
            raise RetryRequested(reason="attempt ownership changed during recovery")
        assessment = await assess_candidate(
            candidate,
            cooperative_seconds=settings.JOB_PROCESS_COOPERATIVE_SECONDS,
            term_seconds=settings.JOB_PROCESS_TERM_SECONDS,
        )
        if assessment.disposition == "active":
            raise RetryRequested(
                timedelta(seconds=min(10.0, settings.JOB_RUNTIME_HEARTBEAT_SECONDS)),
                assessment.reason,
            )
        if assessment.disposition == "unsafe":
            disposition = await FencedWriter(
                candidate.ownership,
                preflight.definition,
            ).unsafe(
                assessment.reason,
                code="mutation_recovery_not_safe",
                stage="publication_reconciliation",
            )
            if disposition != WriteDisposition.APPLIED:
                raise RetryRequested(reason="attempt ownership changed during recovery")
            raise DeliveryRejectedError("unsafe stale attempt is held for operator resolution")
        admitted = await _take_over_attempt(
            transport_job,
            preflight,
            runtime_instance_id,
            attempt_id=candidate.ownership.attempt_id,
            fence_token=candidate.ownership.fence_token,
            reason=assessment.reason,
        )
        if admitted is None:
            raise RetryRequested(reason="attempt ownership changed during recovery")
    if retry_reason is not None:
        raise RetryRequested(
            timedelta(seconds=min(10.0, settings.JOB_RUNTIME_HEARTBEAT_SECONDS)),
            retry_reason,
        )
    return admitted


async def execute_system_noop(context: ExecutionContext) -> dict[str, Any]:
    """The only production handler; it has no ORM, transport row, or path authority."""
    if context.cancellation.cancel_called:
        raise asyncio.CancelledError
    await context.progress.stage("execute")
    return {
        "outcome": "succeeded",
        "summary": {"echo": context.request.get("echo")},
    }


_EXECUTION_HANDLERS: dict[str, KernelHandler] = {"system_noop": execute_system_noop}
# Live read-only view; migrated families register through ``register_execution_handler``.
EXECUTION_HANDLERS: Mapping[str, KernelHandler] = MappingProxyType(_EXECUTION_HANDLERS)


def register_execution_handler(job_type: str, handler: KernelHandler) -> None:
    """Bind a migrated read-only handler to its canonical job type.

    The definition registry still governs which types are dispatch-enabled; this only wires
    the in-process kernel handler resolved by ``_execute_delivery``.
    """
    if job_type in _EXECUTION_HANDLERS:
        raise RuntimeError(f"duplicate execution handler: {job_type}")
    _EXECUTION_HANDLERS[job_type] = handler


_SAFETY_GATES = SafetyGateService()


async def _execute_delivery(execution: ExecutionContext) -> dict[str, Any]:
    handler = EXECUTION_HANDLERS.get(execution.definition.job_type)
    if handler is None:
        raise DeliveryRejectedError("enabled definition has no execution handler")
    try:
        async with asyncio.timeout(execution.definition.timeout.seconds):
            return await handler(execution)
    except TimeoutError as exc:
        raise HandlerExecutionTimeoutError(
            f"{execution.definition.job_type} exceeded its execution timeout"
        ) from exc


async def _seal_attempt_log(
    log_sink: AttemptLogSink | None,
    *,
    outcome: str,
    attempt_outcome: str | None = None,
    summary: str | None = None,
) -> bool:
    if log_sink is None:
        return False
    try:
        await log_sink.write(
            source="system",
            message="Attempt execution ended; sealing captured evidence.",
            fields={
                "job_outcome": outcome,
                "attempt_outcome": attempt_outcome or outcome,
                "summary": summary,
            },
        )
        await log_sink.seal()
        return True
    except Exception:
        logger.error("Attempt log evidence could not be sealed", exc_info=True)
        return False


async def _record_evidence_degradation(
    ownership: AttemptOwnership,
    *,
    event_key: Literal["artifact.failed", "log.truncated"],
    source: str,
) -> None:
    with contextlib.suppress(Exception):
        factory = _get_session_factory()
        async with factory() as session, session.begin():
            job = await session.get(Job, ownership.job_id)
            if job is not None:
                await job_event_writer.append(
                    session,
                    job_id=ownership.job_id,
                    attempt_id=ownership.attempt_id,
                    event_key=event_key,
                    state=job.outcome or job.phase,
                    message="Canonical terminal evidence requires bounded repair",
                    detail={"source": source, "retryable": True},
                    canonical_version=ownership.fence_token,
                )


async def _register_terminal_artifact(
    ownership: AttemptOwnership, *, source: Literal["result", "error"]
) -> None:
    try:
        await register_virtual_artifact(
            job_id=ownership.job_id,
            attempt_id=ownership.attempt_id,
            fence_token=ownership.fence_token,
            source=source,
            name=f"Canonical {source}",
            retention_class="standard",
        )
    except Exception:
        logger.error("Canonical %s artifact registration failed", source, exc_info=True)
        await _record_evidence_degradation(
            ownership, event_key="artifact.failed", source=source
        )


def _result_physical_evidence(value: Any):
    if isinstance(value, dict):
        checksum = value.get("checksum")
        size_bytes = value.get("size_bytes")
        if (
            isinstance(value.get("artifact_key"), str)
            and isinstance(checksum, str)
            and isinstance(size_bytes, int)
        ):
            yield (
                "product_backup",
                value["artifact_key"],
                checksum,
                size_bytes,
                "Recoverable product backup",
            )
        elif (
            isinstance(value.get("managed_asset_id"), str)
            and isinstance(value.get("storage_key"), str)
            and isinstance(checksum, str)
            and isinstance(size_bytes, int)
        ):
            yield (
                "managed_sidecar",
                value["storage_key"],
                checksum,
                size_bytes,
                "Managed subtitle sidecar",
            )
        for child in value.values():
            yield from _result_physical_evidence(child)
    elif isinstance(value, list | tuple):
        for child in value:
            yield from _result_physical_evidence(child)


async def _register_result_evidence(
    ownership: AttemptOwnership, result: Mapping[str, Any]
) -> None:
    """Register post-terminal physical and validation evidence without rerunning product work."""
    boundary = artifact_boundary(settings.DATA_DIR)
    seen: set[str] = set()
    for kind, key, checksum, size_bytes, name in _result_physical_evidence(result):
        if key in seen:
            continue
        seen.add(key)
        try:
            source = boundary.from_key("data", key)
            if kind == "managed_sidecar":
                await register_physical_artifact(
                    job_id=ownership.job_id,
                    attempt_id=ownership.attempt_id,
                    fence_token=ownership.fence_token,
                    source=source,
                    kind=kind,
                    name=name,
                    content_type="application/x-subrip",
                    retention_class="extended",
                    metadata={"product_storage_key": key, "product_checksum": checksum},
                )
            else:
                await register_existing_physical_artifact(
                    job_id=ownership.job_id,
                    attempt_id=ownership.attempt_id,
                    fence_token=ownership.fence_token,
                    source=source,
                    kind=kind,
                    name=name,
                    checksum=checksum,
                    size_bytes=size_bytes,
                )
        except Exception:
            logger.error("Product evidence registration failed", exc_info=True)
            await _record_evidence_degradation(
                ownership, event_key="artifact.failed", source=kind
            )
    try:
        await register_validation_result_artifact(
            job_id=ownership.job_id,
            attempt_id=ownership.attempt_id,
            fence_token=ownership.fence_token,
        )
    except Exception:
        logger.error("Validation evidence registration failed", exc_info=True)
        await _record_evidence_degradation(
            ownership, event_key="artifact.failed", source="validation"
        )


async def deliver_job(
    transport_job: PgQueuerJob,
    context: Context,
    *,
    expected_entrypoint: str,
    runtime_instance_id: str | None = None,
) -> None:
    """Gate, admit, execute, seal canonically, then allow PgQueuer acknowledgement."""
    if str(transport_job.entrypoint) != expected_entrypoint:
        raise DeliveryRejectedError("transport entrypoint does not match worker registration")
    payload = parse_transport_payload(transport_job.payload)
    preflight = await _preflight(transport_job, payload)
    if preflight is None:
        return
    try:
        gates = await _SAFETY_GATES.acquire(
            preflight.requirements,
            cancelled=lambda: _admission_cancelled(payload, context),
            deadline_seconds=min(
                settings.JOB_ADMISSION_TIMEOUT_SECONDS,
                preflight.definition.timeout.seconds,
            ),
            publish_wait=lambda reason: _publish_wait(
                payload.job_id, payload.dispatch_generation, reason
            ),
        )
    except SafetyGateCancelledError:
        await _apply_pre_admission_intent(payload)
        if context.cancellation.cancel_called:
            raise asyncio.CancelledError from None
        return
    except (SafetyGateTimeoutError, SafetyGateConnectionLostError) as exc:
        delay = await _defer_pre_admission(
            payload,
            preflight,
            transport_job,
            cause=(
                "safety_gate_timeout"
                if isinstance(exc, SafetyGateTimeoutError)
                else "safety_gate_connection_lost"
            ),
        )
        if delay is None:
            await _apply_pre_admission_intent(payload)
            return
        # The deferral record is durable before PgQueuer receives RetryRequested.
        # If cancellation wins inside that narrow callback window, terminalize the
        # queued intent instead of reviving it through the retry primitive.
        if await _admission_cancelled(payload, context):
            await _apply_pre_admission_intent(payload)
            if context.cancellation.cancel_called:
                raise asyncio.CancelledError from None
            return
        raise RetryRequested(delay, "safety-gate admission deferred") from exc

    admitted: AdmittedDelivery | None = None
    try:
        admitted = await _admit_delivery(
            transport_job, payload, preflight, runtime_instance_id
        )
        if admitted is None:
            await _apply_pre_admission_intent(payload)
            return
        ownership = AttemptOwnership(
            job_id=admitted.delivery.canonical_job_id,
            attempt_id=admitted.attempt.attempt_id,
            fence_token=admitted.attempt.fence_token,
            dispatch_generation=admitted.delivery.dispatch_generation,
        )
        writer = FencedWriter(ownership, admitted.definition)
        try:
            workspace = AttemptWorkspaceManager.for_data_dir(Path(settings.DATA_DIR)).create(
                job_id=ownership.job_id,
                attempt_id=ownership.attempt_id,
                fence_token=ownership.fence_token,
            )
        except Exception as exc:
            await writer.fail(exc)
            raise
        try:
            log_sink = await AttemptLogSink.create(
                job_id=ownership.job_id,
                attempt_id=ownership.attempt_id,
                fence_token=ownership.fence_token,
                data_dir=Path(settings.DATA_DIR),
            )
            await log_sink.write(
                source="system",
                message="Attempt admitted and log capture started.",
                fields={"attempt_id": ownership.attempt_id},
            )
        except Exception:
            log_sink = None
            logger.error("Attempt log capture could not be initialized", exc_info=True)
        process_launcher = ProcessLauncher(
            worker_node=admitted.attempt.worker_node,
            boundary=workspace.boundary,
            working_directory=workspace.directory,
            record_identity=writer.record_process_identity,
            record_exit=writer.record_process_exit,
            pipe_sink=log_sink.feed_pipe if log_sink is not None else None,
            capture_limit=0 if log_sink is not None else 64 * 1024,
        )
        execution_progress = ExecutionProgress.create(
            job_id=admitted.delivery.canonical_job_id,
            attempt_id=admitted.attempt.attempt_id,
            fence_token=admitted.attempt.fence_token,
            definition=admitted.definition,
            subject=dict(admitted.subject),
        )
        session_factory = _get_session_factory()

        async def owns_execution_fence() -> bool:
            async with session_factory() as session:
                return await writer.owns_current_attempt(session)

        execution = ExecutionContext(
            delivery=admitted.delivery,
            attempt=admitted.attempt,
            request=admitted.request,
            configuration=admitted.configuration,
            subject=admitted.subject,
            definition=admitted.definition,
            cancellation=context.cancellation,
            safety_gates=gates,
            workspace=workspace,
            process_launcher=process_launcher,
            progress=execution_progress,
            io=ExecutionIO(
                cancelled=lambda: bool(context.cancellation.cancel_called),
                owns_fence=owns_execution_fence,
                progress=execution_progress.io_bytes,
            ),
            log_sink=log_sink,
            writer=writer,
            session_factory=session_factory,
        )
        try:
            await execution.progress.stage(execution.definition.progress_policy.stages[0][0])
            if log_sink is None:
                result = await _execute_delivery(execution)
            else:
                async with log_sink.capture_python_logs():
                    result = await _execute_delivery(execution)
        except Exception as exc:
            reason = (
                exc.reason or "retry requested"
                if isinstance(exc, RetryRequested)
                else str(exc)
            )
            try:
                await asyncio.shield(
                    process_launcher.shutdown(
                        cooperative_seconds=settings.JOB_PROCESS_COOPERATIVE_SECONDS,
                        term_seconds=settings.JOB_PROCESS_TERM_SECONDS,
                    )
                )
            except Exception as shutdown_exc:
                await writer.unsafe(
                    "Owned process death could not be proven after execution failure",
                    code="process_death_uncertain",
                    stage="retry_classification",
                )
                await _seal_attempt_log(
                    log_sink,
                    outcome="unsafe",
                    attempt_outcome="interrupted",
                    summary="owned process death could not be proven",
                )
                workspace.quarantine(
                    code="process_death_uncertain",
                    summary="owned process death could not be proven",
                )
                raise DeliveryRejectedError("owned process death is uncertain") from shutdown_exc
            failure_classification = execution.definition.failure_classifier(exc)
            retry = execution.definition.retry_policy.classify(
                failure_classification,
                execution.attempt.number,
            )
            if retry.delay_seconds is not None:
                disposition = await writer.retry(
                    reason=reason,
                    delay_seconds=retry.delay_seconds,
                )
                if disposition != WriteDisposition.APPLIED:
                    workspace.quarantine(
                        code="retry_fence_conflict", summary="retry ownership changed"
                    )
                    raise DeliveryRejectedError(
                        "canonical retry conflicted with current state"
                    ) from exc
                await _seal_attempt_log(
                    log_sink,
                    outcome="retrying",
                    attempt_outcome="retrying",
                    summary=reason,
                )
                workspace.cleanup()
                raise RetryRequested(timedelta(seconds=retry.delay_seconds), reason) from exc
            if retry.classification == RetryClassification.UNSAFE:
                disposition = await writer.unsafe(
                    reason or "execution safety is uncertain",
                    code="execution_uncertain",
                    stage="retry_classification",
                )
                if disposition == WriteDisposition.APPLIED:
                    await _seal_attempt_log(
                        log_sink,
                        outcome="unsafe",
                        attempt_outcome="interrupted",
                        summary=reason or "execution safety is uncertain",
                    )
                    await _register_terminal_artifact(ownership, source="error")
                workspace.quarantine(
                    code="execution_uncertain",
                    summary=reason or "execution safety is uncertain",
                )
                raise DeliveryRejectedError("execution safety is uncertain") from exc
            if retry.classification == RetryClassification.CANCELLED:
                disposition = await writer.fail(exc, cancelled=True)
                if disposition == WriteDisposition.APPLIED:
                    await _seal_attempt_log(
                        log_sink,
                        outcome="cancelled",
                        attempt_outcome="cancelled",
                        summary=reason or "execution cancelled",
                    )
                    await _register_terminal_artifact(ownership, source="error")
                workspace.quarantine(
                    code="cancelled", summary=reason or "execution cancelled"
                )
                raise DeliveryRejectedError("execution classified as cancelled") from exc
            disposition = await writer.fail(exc)
            exhausted = failure_classification == RetryClassification.TRANSIENT
            terminal_summary = (
                f"automatic retry exhausted: {reason}"
                if exhausted
                else f"execution failed: {reason}"
            )
            if disposition == WriteDisposition.APPLIED:
                await _seal_attempt_log(
                    log_sink,
                    outcome="failed",
                    attempt_outcome="failed",
                    summary=terminal_summary,
                )
                await _register_terminal_artifact(ownership, source="error")
            workspace.quarantine(
                code="retry_exhausted" if exhausted else "failed",
                summary=terminal_summary,
            )
            if exhausted:
                raise DeliveryRejectedError("automatic retry policy exhausted") from exc
            raise
        except asyncio.CancelledError as exc:
            await asyncio.shield(writer.stopping())
            await asyncio.shield(
                process_launcher.shutdown(
                    cooperative_seconds=settings.JOB_PROCESS_COOPERATIVE_SECONDS,
                    term_seconds=settings.JOB_PROCESS_TERM_SECONDS,
                )
            )
            disposition = await asyncio.shield(writer.fail(exc, cancelled=True))
            if disposition == WriteDisposition.APPLIED:
                await asyncio.shield(
                    _seal_attempt_log(
                        log_sink,
                        outcome="cancelled",
                        attempt_outcome="cancelled",
                        summary="attempt cancelled before publication",
                    )
                )
            await asyncio.shield(_register_terminal_artifact(ownership, source="error"))
            workspace.quarantine(code="cancelled", summary="attempt cancelled before publication")
            raise
        await process_launcher.shutdown(
            cooperative_seconds=settings.JOB_PROCESS_COOPERATIVE_SECONDS,
            term_seconds=settings.JOB_PROCESS_TERM_SECONDS,
        )
        first_stage = execution.definition.progress_policy.stages[0][0]
        final_stage = execution.definition.progress_policy.stages[-1][0]
        if final_stage != first_stage:
            await execution.progress.stage(final_stage)
        try:
            decision: TerminalDecision = writer.terminal_decision(result)
            disposition = await writer.succeed(result, decision=decision)
        except Exception as exc:
            await _seal_attempt_log(
                log_sink,
                outcome="unsafe",
                attempt_outcome="interrupted",
                summary="terminal result mapping or persistence could not be proven durable",
            )
            workspace.quarantine(
                code="terminal_uncertain",
                summary="terminal result mapping or persistence could not be proven durable",
            )
            raise DeliveryRejectedError("canonical terminal durability is uncertain") from exc
        if disposition != WriteDisposition.APPLIED:
            # A public cancellation may have won after the handler completed but
            # before this fenced success write. This delivery still owns process
            # death proof, so it seals the cancellation with its current fence
            # rather than leaving a stopping attempt for recovery.
            if await _cancellation_intent_won(ownership):
                cancelled = await writer.fail(
                    asyncio.CancelledError("cancellation won before terminal publication"),
                    cancelled=True,
                )
                if cancelled == WriteDisposition.APPLIED:
                    await _seal_attempt_log(
                        log_sink,
                        outcome="cancelled",
                        attempt_outcome="cancelled",
                        summary="cancellation won before terminal publication",
                    )
                    await _register_terminal_artifact(ownership, source="error")
                    workspace.quarantine(
                        code="cancelled",
                        summary="cancellation won before terminal publication",
                    )
                    return
            await _seal_attempt_log(
                log_sink,
                outcome="unsafe",
                attempt_outcome="interrupted",
                summary="terminal ownership changed before canonical durability",
            )
            workspace.quarantine(code="stale_fence", summary="completion ownership changed")
            raise DeliveryRejectedError("canonical completion conflicted with current state")
        log_sealed = await _seal_attempt_log(
            log_sink,
            outcome=decision.job_outcome.value,
            attempt_outcome=decision.attempt_outcome.value,
            summary=decision.summary,
        )
        if not log_sealed:
            await _record_evidence_degradation(
                ownership, event_key="log.truncated", source="attempt_log"
            )
        await _register_result_evidence(ownership, result)
        await _register_terminal_artifact(ownership, source="result")
        if decision.workspace == WorkspaceDisposition.CLEAN:
            workspace.cleanup()
        else:
            workspace.quarantine(
                code=f"domain_{decision.job_outcome.value}", summary=decision.summary
            )
        if not decision.acknowledge_transport:
            raise DeliveryRejectedError("terminal decision forbids transport acknowledgement")
    finally:
        await asyncio.shield(gates.release())


async def deliver_control_job(
    transport_job: PgQueuerJob,
    context: Context,
) -> None:
    """Retained typed control caller for the sole production-enabled definition."""
    await deliver_job(
        transport_job,
        context,
        expected_entrypoint="control",
    )


# Register migrated read-only family handlers. Placed at module end so ExecutionContext and
# register_execution_handler are defined before the import side effect runs.
from marquee.core.jobs import kernel_handlers as _kernel_handlers  # noqa: E402,F401
