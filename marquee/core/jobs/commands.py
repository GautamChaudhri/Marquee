"""Atomic JMC1 command service for the sole enabled ``system_noop`` definition."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from marquee.core.configuration_cache import configuration_provider
from marquee.core.jobs.event_service import job_event_writer
from marquee.core.jobs.manifest import JOB_DEFINITION_REGISTRY
from marquee.core.jobs.pgqueuer_gateway import (
    ENTRYPOINT_CONTROL,
    PAYLOAD_VERSION,
    PgQueuerInvariantError,
    pgqueuer_gateway,
)
from marquee.core.jobs.subjects import SystemWorkSnapshot
from marquee.models.job import Job, JobDispatch

MAX_NOOP_PAYLOAD_BYTES = 4096
IDEMPOTENCY_PATTERN = re.compile(r"^system_noop:[A-Za-z0-9._-]{1,160}$")
FORBIDDEN_PAYLOAD_KEY_PARTS = ("password", "path", "secret", "token")


class JobCommandError(ValueError):
    """Raised when a canonical command is invalid or conflicts."""


def _validate_payload_keys(value: Any) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if not isinstance(key, str):
                raise JobCommandError("system_noop payload keys must be strings")
            lowered = key.lower()
            if any(part in lowered for part in FORBIDDEN_PAYLOAD_KEY_PARTS):
                raise JobCommandError("system_noop payload cannot contain secrets or paths")
            _validate_payload_keys(child)
    elif isinstance(value, list):
        for child in value:
            _validate_payload_keys(child)


def validate_system_noop_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Require one small deterministic JSON document with no sensitive fields."""
    if not isinstance(payload, dict):
        raise JobCommandError("system_noop payload must be a JSON object")
    _validate_payload_keys(payload)
    try:
        encoded = json.dumps(
            payload,
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise JobCommandError("system_noop payload must be finite JSON") from exc
    if len(encoded) > MAX_NOOP_PAYLOAD_BYTES:
        raise JobCommandError(
            f"system_noop payload exceeds {MAX_NOOP_PAYLOAD_BYTES} encoded bytes"
        )
    # Normalize ordering/types through the same strict JSON representation.
    return json.loads(encoded)


def validate_system_noop_idempotency_key(idempotency_key: str) -> str:
    if not IDEMPOTENCY_PATTERN.fullmatch(idempotency_key):
        raise JobCommandError(
            "system_noop idempotency key must match system_noop:<stable-key>"
        )
    return idempotency_key


def _validate_existing(existing: Job, payload: dict[str, Any]) -> None:
    if (
        existing.type != "system_noop"
        or existing.payload_version != PAYLOAD_VERSION
        or existing.request != payload
    ):
        raise JobCommandError("idempotency key already belongs to a different command")
    if existing.dispatch_generation < 1 or existing.pgq_job_id is None:
        raise PgQueuerInvariantError(
            "existing canonical system_noop has no completed transport dispatch"
        )


async def _load_existing(session: AsyncSession, idempotency_key: str) -> Job | None:
    return await session.scalar(select(Job).where(Job.idempotency_key == idempotency_key))


async def create_system_noop(
    session: AsyncSession,
    *,
    payload: dict[str, Any],
    idempotency_key: str,
    priority: int = 50,
    execute_after: timedelta | None = None,
) -> Job:
    """Create the canonical rows and transport ticket, then commit exactly once."""
    normalized_payload = validate_system_noop_payload(payload)
    definition = JOB_DEFINITION_REGISTRY.for_dispatch(
        "system_noop", entrypoint=ENTRYPOINT_CONTROL
    )
    normalized_payload = definition.request.validate(
        normalized_payload, version=PAYLOAD_VERSION
    ).model_dump(mode="json", exclude_none=True)
    canonical_key = validate_system_noop_idempotency_key(idempotency_key)
    if session.in_transaction():
        raise JobCommandError("system_noop command service requires a fresh session transaction")

    try:
        async with session.begin():
            existing = await _load_existing(session, canonical_key)
            if existing is not None:
                _validate_existing(existing, normalized_payload)
                return existing

            now = datetime.now(UTC)
            configuration = configuration_provider.snapshot_for(definition.configuration_keys)
            delay = execute_after or timedelta(0)
            eligible_at = now + delay
            job_id = uuid4().hex
            generation = 1
            dedupe_key = f"marquee:{job_id}:{generation}"
            job = Job(
                id=job_id,
                type="system_noop",
                payload_version=PAYLOAD_VERSION,
                request=normalized_payload,
                phase="queued",
                desired_state="run",
                dispatch_generation=generation,
                priority=priority,
                eligible_at=eligible_at,
                idempotency_key=canonical_key,
                configuration_version=configuration.version,
                configuration_snapshot=configuration.values,
                root_id=job_id,
                trigger_kind=next(iter(definition.trigger_kinds)).value,
                feature_area=definition.feature_area.value,
                subject_kind="system_work",
                subject_reference="system_noop",
                subject_snapshot=SystemWorkSnapshot(
                    display_id="system:noop",
                    display_name="System no-op",
                    work="system_noop",
                ).model_dump(mode="json"),
                queued_at=now,
            )
            dispatch = JobDispatch(
                job_id=job_id,
                generation=generation,
                pgq_job_id=None,
                entrypoint=ENTRYPOINT_CONTROL,
                dedupe_key=dedupe_key,
                priority=priority,
                eligible_at=eligible_at,
                disposition="active",
            )
            session.add_all([job, dispatch])
            await job_event_writer.append(
                session,
                job_id=job_id,
                event_key="job.queued",
                state="queued",
                message="system_noop queued",
                detail={"dispatch_generation": generation},
            )
            await pgqueuer_gateway.enqueue(
                session,
                job_id=job_id,
                entrypoint=ENTRYPOINT_CONTROL,
                payload_version=PAYLOAD_VERSION,
                dispatch_generation=generation,
                priority=priority,
                execute_after=execute_after,
                dedupe_key=dedupe_key,
            )
        return job
    except IntegrityError:
        # The unique canonical idempotency key is flushed before transport
        # enqueue. A concurrent loser therefore has no ticket to clean up.
        await session.rollback()
        async with session.begin():
            winner = await _load_existing(session, canonical_key)
            if winner is None:
                raise
            _validate_existing(winner, normalized_payload)
            return winner
    except PgQueuerInvariantError:
        # A transport dedupe collision is diagnostic corruption, not a second
        # idempotency mechanism and never a request for another ticket.
        raise
