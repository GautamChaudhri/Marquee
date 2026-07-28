"""Atomic JMC1 command service for the sole enabled ``system_noop`` definition."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from marquee.core.jobs.contracts import TriggerKind
from marquee.core.jobs.pgqueuer_gateway import MAX_DEFER
from marquee.core.jobs.submission import (
    IdempotencyConflictError,
    SubjectLocator,
    SubmissionError,
    SubmissionInvariantError,
    submit_job,
)
from marquee.models.job import Job

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
        raise JobCommandError(f"system_noop payload exceeds {MAX_NOOP_PAYLOAD_BYTES} encoded bytes")
    # Normalize ordering/types through the same strict JSON representation.
    return json.loads(encoded)


def validate_system_noop_idempotency_key(idempotency_key: str) -> str:
    if not IDEMPOTENCY_PATTERN.fullmatch(idempotency_key):
        raise JobCommandError("system_noop idempotency key must match system_noop:<stable-key>")
    return idempotency_key


async def create_system_noop(
    session: AsyncSession,
    *,
    payload: dict[str, Any],
    idempotency_key: str,
    priority: int = 50,
    execute_after: timedelta | None = None,
) -> Job:
    """Backward-compatible committing wrapper over the caller-owned submitter."""
    normalized_payload = validate_system_noop_payload(payload)
    canonical_key = validate_system_noop_idempotency_key(idempotency_key)
    if session.in_transaction():
        raise JobCommandError("system_noop command service requires a fresh session transaction")
    delay = execute_after or timedelta(0)
    if not timedelta(0) <= delay <= MAX_DEFER:
        raise JobCommandError("execute_after must be between zero and 365 days")

    try:
        async with session.begin():
            result = await submit_job(
                session,
                job_type="system_noop",
                request=normalized_payload,
                subject=SubjectLocator(kind="system_work", reference="system_noop"),
                trigger=TriggerKind.SYSTEM,
                initiator=None,
                idempotency_key=canonical_key,
                priority=priority,
                eligible_at=datetime.now(UTC) + delay,
            )
            job = await session.get(Job, result.job_id)
            if job is None:
                raise SubmissionInvariantError("canonical submission result is missing")
        return job
    except IdempotencyConflictError as exc:
        raise JobCommandError("idempotency key already belongs to a different command") from exc
    except SubmissionError as exc:
        raise JobCommandError(str(exc)) from exc
