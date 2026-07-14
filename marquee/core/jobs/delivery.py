"""Strict JMC1 PgQueuer delivery wrapper for the sole ``control`` entrypoint."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from pgqueuer import RetryRequested
from pgqueuer.models import Context
from pgqueuer.models import Job as PgQueuerJob
from sqlalchemy import func, select

from marquee.database import _get_session_factory
from marquee.models.job import Job, JobAttempt, JobDispatch, JobEvent

TRANSPORT_KEYS = frozenset({"dispatch_generation", "job_id", "payload_version"})
MAX_ERROR_MESSAGE = 1000


class DeliveryRejectedError(RuntimeError):
    """Raise to make PgQueuer hold malformed or unsafe work."""


@dataclass(frozen=True)
class TransportPayload:
    job_id: str
    payload_version: int
    dispatch_generation: int


@dataclass(frozen=True)
class AdmittedDelivery:
    job_id: str
    dispatch_generation: int
    attempt_id: int
    payload: dict[str, Any]


NoopExecutor = Callable[[dict[str, Any], Context], Awaitable[dict[str, Any]]]


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
    return TransportPayload(
        job_id=job_id,
        payload_version=payload_version,
        dispatch_generation=generation,
    )


def _bounded_error(exc: BaseException) -> dict[str, str]:
    return {
        "type": type(exc).__name__,
        "message": str(exc)[:MAX_ERROR_MESSAGE],
    }


async def _start_attempt(
    session: Any,
    job: Job,
    transport_job: PgQueuerJob,
    payload: TransportPayload,
) -> AdmittedDelivery:
    now = datetime.now(UTC)
    last_attempt = await session.scalar(
        select(func.max(JobAttempt.number)).where(JobAttempt.job_id == job.id)
    )
    attempt = JobAttempt(
        job_id=job.id,
        number=(last_attempt or 0) + 1,
        worker_id=f"pgqueuer:{transport_job.queue_manager_id}",
        status="running",
        claimed_at=now,
        started_at=now,
        metrics={
            "pgq_job_id": int(transport_job.id),
            "pgq_attempt": transport_job.attempts,
        },
    )
    session.add(attempt)
    await session.flush()
    job.phase = "running"
    job.status = "running"
    job.started_at = job.started_at or now
    session.add(
        JobEvent(
            job_id=job.id,
            attempt_id=attempt.id,
            state="running",
            message="system_noop started",
            detail={"dispatch_generation": payload.dispatch_generation},
        )
    )
    return AdmittedDelivery(
        job_id=job.id,
        dispatch_generation=payload.dispatch_generation,
        attempt_id=attempt.id,
        payload=dict(job.payload),
    )


async def _admit_delivery(
    transport_job: PgQueuerJob,
    payload: TransportPayload,
) -> AdmittedDelivery | None:
    factory = _get_session_factory()
    rejection: DeliveryRejectedError | None = None
    admitted: AdmittedDelivery | None = None
    async with factory() as session, session.begin():
        job = await session.scalar(
            select(Job).where(Job.id == payload.job_id).with_for_update()
        )
        if job is None:
            rejection = DeliveryRejectedError("canonical job does not exist")
        else:
            dispatch = await session.scalar(
                select(JobDispatch)
                .where(
                    JobDispatch.job_id == payload.job_id,
                    JobDispatch.generation == payload.dispatch_generation,
                )
                .with_for_update()
            )
            if dispatch is None:
                rejection = DeliveryRejectedError("canonical dispatch does not exist")
            elif (
                payload.dispatch_generation != job.dispatch_generation
                or int(transport_job.id) != job.pgq_job_id
                or int(transport_job.id) != dispatch.pgq_job_id
            ):
                if dispatch.disposition == "active":
                    dispatch.disposition = "stale"
                    dispatch.ended_at = datetime.now(UTC)
            elif job.type != "system_noop" or payload.payload_version != job.payload_version:
                error = {
                    "type": "UnmigratedDefinition",
                    "message": "only system_noop payload version 1 is enabled",
                }
                now = datetime.now(UTC)
                job.phase = "terminal"
                job.outcome = "failed"
                job.error = error
                job.terminal_at = now
                job.status = "failed"
                job.finished_at = now
                dispatch.disposition = "failed"
                dispatch.ended_at = now
                session.add(
                    JobEvent(
                        job_id=job.id,
                        state="failed",
                        message="unmigrated job definition held",
                        detail=error,
                    )
                )
                rejection = DeliveryRejectedError(error["message"])
            elif job.phase == "terminal" or job.outcome is not None:
                pass
            elif job.desired_state == "cancel":
                now = datetime.now(UTC)
                job.phase = "terminal"
                job.outcome = "cancelled"
                job.terminal_at = now
                job.status = "cancelled"
                job.finished_at = now
                dispatch.disposition = "cancelled"
                dispatch.ended_at = now
                session.add(
                    JobEvent(
                        job_id=job.id,
                        state="cancelled",
                        message="cancelled before execution",
                    )
                )
            elif job.desired_state == "pause":
                job.pgq_job_id = None
                job.status = "paused"
                dispatch.disposition = "cancelled"
                dispatch.ended_at = datetime.now(UTC)
            elif job.phase == "running":
                active_attempt = await session.scalar(
                    select(JobAttempt)
                    .where(
                        JobAttempt.job_id == job.id,
                        JobAttempt.status == "running",
                    )
                    .order_by(JobAttempt.number.desc())
                    .limit(1)
                    .with_for_update()
                )
                manager_identity = f"pgqueuer:{transport_job.queue_manager_id}"
                if active_attempt is not None and active_attempt.worker_id == manager_identity:
                    # PgQueuer does not dispatch one picked ticket twice from the
                    # same manager. This is an in-flight duplicate, not recovery.
                    pass
                else:
                    now = datetime.now(UTC)
                    if active_attempt is not None:
                        error = {
                            "type": "WorkerLost",
                            "message": "delivery recovered after stale transport heartbeat",
                        }
                        active_attempt.status = "interrupted"
                        active_attempt.finished_at = now
                        active_attempt.error = error
                        session.add(
                            JobEvent(
                                job_id=job.id,
                                attempt_id=active_attempt.id,
                                state="interrupted",
                                message="stale system_noop attempt recovered",
                                detail=error,
                            )
                        )
                    admitted = await _start_attempt(session, job, transport_job, payload)
            elif job.phase != "queued":
                rejection = DeliveryRejectedError(
                    f"canonical job phase {job.phase!r} cannot be delivered"
                )
            else:
                admitted = await _start_attempt(session, job, transport_job, payload)
    if rejection is not None:
        raise rejection
    return admitted


async def _record_success(admitted: AdmittedDelivery, result: dict[str, Any]) -> None:
    factory = _get_session_factory()
    async with factory() as session, session.begin():
        job = await session.scalar(
            select(Job).where(Job.id == admitted.job_id).with_for_update()
        )
        if job is None or job.phase == "terminal":
            return
        if job.dispatch_generation != admitted.dispatch_generation or job.phase != "running":
            return
        dispatch = await session.scalar(
            select(JobDispatch)
            .where(
                JobDispatch.job_id == admitted.job_id,
                JobDispatch.generation == admitted.dispatch_generation,
            )
            .with_for_update()
        )
        attempt = await session.get(JobAttempt, admitted.attempt_id)
        now = datetime.now(UTC)
        job.phase = "terminal"
        job.outcome = "succeeded"
        job.result = result
        job.error = None
        job.terminal_at = now
        job.status = "succeeded"
        job.finished_at = now
        if dispatch is not None:
            dispatch.disposition = "succeeded"
            dispatch.ended_at = now
        if attempt is not None:
            attempt.status = "succeeded"
            attempt.finished_at = now
        session.add(
            JobEvent(
                job_id=job.id,
                attempt_id=admitted.attempt_id,
                state="succeeded",
                message="system_noop completed",
                detail={"result": result},
            )
        )


async def _record_retry(admitted: AdmittedDelivery, exc: RetryRequested) -> None:
    factory = _get_session_factory()
    async with factory() as session, session.begin():
        job = await session.scalar(
            select(Job).where(Job.id == admitted.job_id).with_for_update()
        )
        if job is None or job.phase == "terminal":
            return
        attempt = await session.get(JobAttempt, admitted.attempt_id)
        now = datetime.now(UTC)
        error = {
            "type": "RetryRequested",
            "message": (exc.reason or "retry requested")[:MAX_ERROR_MESSAGE],
            "delay_seconds": exc.delay.total_seconds(),
        }
        job.phase = "queued"
        job.status = "retry_scheduled"
        job.error = error
        if attempt is not None:
            attempt.status = "retrying"
            attempt.finished_at = now
            attempt.error = error
        session.add(
            JobEvent(
                job_id=job.id,
                attempt_id=admitted.attempt_id,
                state="retrying",
                message="system_noop retry requested",
                detail=error,
            )
        )


async def _record_terminal_error(
    admitted: AdmittedDelivery,
    exc: BaseException,
    *,
    cancelled: bool,
) -> None:
    factory = _get_session_factory()
    async with factory() as session, session.begin():
        job = await session.scalar(
            select(Job).where(Job.id == admitted.job_id).with_for_update()
        )
        if job is None or job.phase == "terminal":
            return
        dispatch = await session.scalar(
            select(JobDispatch)
            .where(
                JobDispatch.job_id == admitted.job_id,
                JobDispatch.generation == admitted.dispatch_generation,
            )
            .with_for_update()
        )
        attempt = await session.get(JobAttempt, admitted.attempt_id)
        now = datetime.now(UTC)
        outcome = "cancelled" if cancelled else "failed"
        error = _bounded_error(exc)
        job.phase = "terminal"
        job.outcome = outcome
        job.error = error
        job.terminal_at = now
        job.status = outcome
        job.finished_at = now
        if dispatch is not None:
            dispatch.disposition = "cancelled" if cancelled else "failed"
            dispatch.ended_at = now
        if attempt is not None:
            attempt.status = outcome
            attempt.finished_at = now
            attempt.error = error
        session.add(
            JobEvent(
                job_id=job.id,
                attempt_id=admitted.attempt_id,
                state=outcome,
                message=f"system_noop {outcome}",
                detail=error,
            )
        )


async def execute_system_noop(
    payload: dict[str, Any],
    context: Context,
) -> dict[str, Any]:
    """The only shipped JMC1 effect: echo canonical payload data."""
    if context.cancellation.cancel_called:
        raise asyncio.CancelledError
    return {"echo": payload}


async def deliver_control_job(
    transport_job: PgQueuerJob,
    context: Context,
    *,
    executor: NoopExecutor = execute_system_noop,
) -> None:
    """Admit, execute, terminalize, then return so PgQueuer may acknowledge."""
    payload = parse_transport_payload(transport_job.payload)
    admitted = await _admit_delivery(transport_job, payload)
    if admitted is None:
        return
    try:
        result = await executor(admitted.payload, context)
    except RetryRequested as exc:
        await _record_retry(admitted, exc)
        raise
    except asyncio.CancelledError as exc:
        await asyncio.shield(_record_terminal_error(admitted, exc, cancelled=True))
        raise
    except Exception as exc:
        await _record_terminal_error(admitted, exc, cancelled=False)
        raise
    await _record_success(admitted, result)
