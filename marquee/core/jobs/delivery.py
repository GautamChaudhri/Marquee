"""Registry-driven, pre-admission-gated, fenced PgQueuer delivery kernel."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal

from pgqueuer import RetryRequested
from pgqueuer.models import Context
from pgqueuer.models import Job as PgQueuerJob
from sqlalchemy import func, select, update

from marquee.config import settings
from marquee.core.jobs.artifact_service import register_virtual_artifact
from marquee.core.jobs.definitions import JobDefinition
from marquee.core.jobs.event_service import job_event_writer
from marquee.core.jobs.fenced_writer import (
    AttemptOwnership,
    FencedWriter,
    WriteDisposition,
)
from marquee.core.jobs.log_capture import AttemptLogSink
from marquee.core.jobs.manifest import JOB_DEFINITION_REGISTRY
from marquee.core.jobs.process_identity import read_boot_id
from marquee.core.jobs.process_launcher import ProcessLauncher
from marquee.core.jobs.progress import MeasurementMode, ProgressMeasurementUpdate
from marquee.core.jobs.progress_service import ProgressObservation, progress_writer
from marquee.core.jobs.safety_gates import (
    SafetyGateCancelledError,
    SafetyGateHandle,
    SafetyGateService,
    SafetyRequirements,
    requirements_for_policy,
)
from marquee.core.jobs.workspaces import AttemptWorkspace, AttemptWorkspaceManager
from marquee.database import _get_session_factory
from marquee.models.job import Job, JobAttempt, JobDispatch

TRANSPORT_KEYS = frozenset({"dispatch_generation", "job_id", "payload_version"})
logger = logging.getLogger(__name__)


class DeliveryRejectedError(RuntimeError):
    """Raise to make PgQueuer hold malformed or unsafe work."""


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
    log_sink: AttemptLogSink | None
    writer: FencedWriter


KernelHandler = Callable[[ExecutionContext], Awaitable[dict[str, Any]]]
LegacyNoopExecutor = Callable[[dict[str, Any], Context], Awaitable[dict[str, Any]]]


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
    )


async def _admit_delivery(
    transport_job: PgQueuerJob,
    payload: TransportPayload,
    preflight: PreflightDelivery,
) -> AdmittedDelivery | None:
    factory = _get_session_factory()
    rejection: DeliveryRejectedError | None = None
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
        elif job.phase == "queued":
            admitted = await _start_attempt(session, job, transport_job, preflight)
        else:
            rejection = DeliveryRejectedError(
                f"canonical job phase {job.phase!r} cannot be delivered"
            )
    if rejection is not None:
        raise rejection
    return admitted


async def execute_system_noop(context: ExecutionContext) -> dict[str, Any]:
    """The only production handler; it has no ORM, transport row, or path authority."""
    if context.cancellation.cancel_called:
        raise asyncio.CancelledError
    await progress_writer.safe_write(
        job_id=context.delivery.canonical_job_id,
        attempt_id=context.attempt.attempt_id,
        fence_token=context.attempt.fence_token,
        observation=ProgressObservation(
            stage_key="execute",
            overall=ProgressMeasurementUpdate(
                scope_id="system-noop:overall", mode=MeasurementMode.NONE
            ),
            current=ProgressMeasurementUpdate(
                scope_id="system-noop:current", mode=MeasurementMode.NONE
            ),
            producer_ordinal=1,
        ),
    )
    return {
        "outcome": "succeeded",
        "summary": {"echo": context.request.get("echo")},
    }


EXECUTION_HANDLERS: Mapping[str, KernelHandler] = MappingProxyType(
    {"system_noop": execute_system_noop}
)
_SAFETY_GATES = SafetyGateService()


async def _execute_delivery(
    execution: ExecutionContext,
    transport_context: Context,
    executor: LegacyNoopExecutor | None,
) -> dict[str, Any]:
    if executor is None:
        handler = EXECUTION_HANDLERS.get(execution.definition.job_type)
        if handler is None:
            raise DeliveryRejectedError("enabled definition has no execution handler")
        return await handler(execution)
    legacy = await executor(dict(execution.request), transport_context)
    return (
        legacy
        if set(legacy) >= {"outcome", "summary"}
        else {"outcome": "succeeded", "summary": {"echo": legacy}}
    )


async def _seal_attempt_log(log_sink: AttemptLogSink | None, *, outcome: str) -> None:
    if log_sink is None:
        return
    with contextlib.suppress(Exception):
        await log_sink.write(
            source="system",
            message="Attempt execution ended; sealing captured evidence.",
            fields={"outcome": outcome},
        )
        await log_sink.seal()


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


async def deliver_control_job(
    transport_job: PgQueuerJob,
    context: Context,
    *,
    executor: LegacyNoopExecutor | None = None,
) -> None:
    """Gate, admit, execute, seal canonically, then allow PgQueuer acknowledgement."""
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

    admitted: AdmittedDelivery | None = None
    try:
        admitted = await _admit_delivery(transport_job, payload, preflight)
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
        execution = ExecutionContext(
            delivery=admitted.delivery,
            attempt=admitted.attempt,
            request=admitted.request,
            configuration=MappingProxyType({}),
            subject=MappingProxyType({}),
            definition=admitted.definition,
            cancellation=context.cancellation,
            safety_gates=gates,
            workspace=workspace,
            process_launcher=process_launcher,
            log_sink=log_sink,
            writer=writer,
        )
        try:
            if log_sink is None:
                result = await _execute_delivery(execution, context, executor)
            else:
                async with log_sink.capture_python_logs():
                    result = await _execute_delivery(execution, context, executor)
        except RetryRequested as exc:
            await asyncio.shield(
                process_launcher.shutdown(
                    cooperative_seconds=settings.JOB_PROCESS_COOPERATIVE_SECONDS,
                    term_seconds=settings.JOB_PROCESS_TERM_SECONDS,
                )
            )
            await _seal_attempt_log(log_sink, outcome="retrying")
            workspace.cleanup()
            await writer.retry(
                reason=exc.reason or "retry requested",
                delay_seconds=exc.delay.total_seconds(),
            )
            raise
        except asyncio.CancelledError as exc:
            await asyncio.shield(writer.stopping())
            await asyncio.shield(
                process_launcher.shutdown(
                    cooperative_seconds=settings.JOB_PROCESS_COOPERATIVE_SECONDS,
                    term_seconds=settings.JOB_PROCESS_TERM_SECONDS,
                )
            )
            await asyncio.shield(_seal_attempt_log(log_sink, outcome="cancelled"))
            workspace.quarantine(code="cancelled", summary="attempt cancelled before publication")
            await asyncio.shield(writer.fail(exc, cancelled=True))
            await asyncio.shield(_register_terminal_artifact(ownership, source="error"))
            raise
        except Exception as exc:
            await asyncio.shield(
                process_launcher.shutdown(
                    cooperative_seconds=settings.JOB_PROCESS_COOPERATIVE_SECONDS,
                    term_seconds=settings.JOB_PROCESS_TERM_SECONDS,
                )
            )
            await asyncio.shield(_seal_attempt_log(log_sink, outcome="failed"))
            workspace.quarantine(code="failed", summary="attempt failed before safe cleanup")
            await writer.fail(exc)
            await _register_terminal_artifact(ownership, source="error")
            raise
        await process_launcher.shutdown(
            cooperative_seconds=settings.JOB_PROCESS_COOPERATIVE_SECONDS,
            term_seconds=settings.JOB_PROCESS_TERM_SECONDS,
        )
        await _seal_attempt_log(log_sink, outcome="succeeded")
        disposition = await writer.succeed(result)
        if disposition != WriteDisposition.APPLIED:
            workspace.quarantine(code="stale_fence", summary="completion ownership changed")
            if disposition == WriteDisposition.CONFLICT:
                raise DeliveryRejectedError("canonical completion conflicted with current state")
            return
        await _register_terminal_artifact(ownership, source="result")
        workspace.cleanup()
    finally:
        await asyncio.shield(gates.release())
