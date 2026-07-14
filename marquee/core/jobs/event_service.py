"""Transactional semantic job-event insertion.

The durable ``job_events.id`` sequence is the only public event cursor. PostgreSQL
notifications are transaction-scoped wakeup hints and never carry event content.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from marquee.models.job import JOB_OUTCOMES, JOB_PHASES, Job, JobEvent

JOB_EVENT_CHANNEL = "marquee_job_events_v1"
MAX_EVENT_DETAIL_BYTES = 16 * 1024
MAX_EVENT_MESSAGE_CHARS = 500
MAX_EVENT_DETAIL_DEPTH = 5
MAX_EVENT_DETAIL_ITEMS = 64

SEMANTIC_EVENT_KEYS = frozenset(
    {
        "attempt.interrupted",
        "attempt.started",
        "attention.updated",
        "artifact.available",
        "artifact.expired",
        "artifact.failed",
        "job.cancelled",
        "job.dead_letter",
        "job.failed",
        "job.no_change",
        "job.partially_succeeded",
        "job.paused",
        "job.priority_changed",
        "job.queued",
        "job.resumed",
        "job.retried",
        "job.retry_requested",
        "job.stopping",
        "job.succeeded",
        "job.superseded",
        "job.unsafe",
        "log.available",
        "log.truncated",
        "progress.updated",
    }
)
EVENT_STATES = frozenset((*JOB_PHASES, *JOB_OUTCOMES, "retrying"))
_SEMANTIC_KEY = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$")
_SENSITIVE_KEY = re.compile(
    r"(?:authorization|cookie|credential|database_url|environment|password|private_key|"
    r"secret|stack_trace|stderr|stdout|token)",
    re.IGNORECASE,
)
_UNSAFE_TEXT = re.compile(
    r"(?:authorization\s*:|bearer\s+\S+|postgres(?:ql)?://|"
    r"(?:api[_-]?key|password|secret|token)\s*[=:]\s*\S+|-----BEGIN)",
    re.IGNORECASE,
)


class JobEventContractError(ValueError):
    """Raised before an invalid or unsafe semantic event can be persisted."""


def _validate_detail(value: Any, *, depth: int = 0, key: str | None = None) -> None:
    if depth > MAX_EVENT_DETAIL_DEPTH:
        raise JobEventContractError("event detail exceeds the maximum nesting depth")
    if key is not None and _SENSITIVE_KEY.search(key):
        raise JobEventContractError(f"event detail key is not allowlisted: {key}")
    if value is None or isinstance(value, bool | int | float):
        return
    if isinstance(value, str):
        if len(value) > 2000:
            raise JobEventContractError("event detail string is too long")
        if _UNSAFE_TEXT.search(value):
            raise JobEventContractError("event detail contains credential-shaped text")
        return
    if isinstance(value, Mapping):
        if len(value) > MAX_EVENT_DETAIL_ITEMS:
            raise JobEventContractError("event detail object has too many entries")
        for nested_key, nested_value in value.items():
            if not isinstance(nested_key, str) or len(nested_key) > 80:
                raise JobEventContractError("event detail keys must be bounded strings")
            _validate_detail(nested_value, depth=depth + 1, key=nested_key)
        return
    if isinstance(value, Sequence) and not isinstance(value, bytes | bytearray):
        if len(value) > MAX_EVENT_DETAIL_ITEMS:
            raise JobEventContractError("event detail list has too many entries")
        for nested_value in value:
            _validate_detail(nested_value, depth=depth + 1)
        return
    raise JobEventContractError("event detail contains an unsupported value")


class JobEventWriter:
    """Insert and notify without taking commit authority from the caller."""

    async def append(
        self,
        session: AsyncSession,
        *,
        job_id: str,
        event_key: str,
        state: str,
        attempt_id: int | None = None,
        stage: str | None = None,
        message: str | None = None,
        detail: dict[str, Any] | None = None,
        canonical_version: int | None = None,
    ) -> JobEvent:
        if event_key not in SEMANTIC_EVENT_KEYS or not _SEMANTIC_KEY.fullmatch(event_key):
            raise JobEventContractError(f"unknown semantic event key: {event_key}")
        if state not in EVENT_STATES:
            raise JobEventContractError(f"unknown canonical event state: {state}")
        if stage is not None and (len(stage) > 80 or not _SEMANTIC_KEY.fullmatch(stage)):
            raise JobEventContractError("event stage must be a bounded semantic key")
        if message is not None and len(message) > MAX_EVENT_MESSAGE_CHARS:
            raise JobEventContractError("event message is too long")
        if message is not None and _UNSAFE_TEXT.search(message):
            raise JobEventContractError("event message contains credential-shaped text")
        public_detail = dict(detail) if detail is not None else {}
        _validate_detail(public_detail)
        if len(json.dumps(public_detail, sort_keys=True, separators=(",", ":")).encode()) > (
            MAX_EVENT_DETAIL_BYTES
        ):
            raise JobEventContractError("event detail exceeds the persisted byte limit")

        await session.flush()
        if canonical_version is None:
            canonical_version = await session.scalar(
                select(Job.fence_token).where(Job.id == job_id)
            )
        if canonical_version is None or canonical_version < 0:
            raise JobEventContractError("event job has no canonical version")
        public_detail["_canonical_version"] = canonical_version

        event = JobEvent(
            job_id=job_id,
            attempt_id=attempt_id,
            event_key=event_key,
            state=state,
            stage=stage,
            message=message,
            detail=public_detail,
        )
        session.add(event)
        await session.flush((event,))
        await session.execute(
            text("SELECT pg_notify(:channel, :cursor)"),
            {"channel": JOB_EVENT_CHANNEL, "cursor": str(event.id)},
        )
        return event


job_event_writer = JobEventWriter()
