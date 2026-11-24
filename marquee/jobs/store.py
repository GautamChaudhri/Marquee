"""What every change to the job tables shares: the errors, the event log, the notification.

The worker listens on :data:`CHANNEL`. A notification is only a hint that something changed
(it is delivered when the sending transaction commits, and a listener can miss one while it
reconnects); the worker's poll is what makes the queue correct.
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import func, insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from marquee.db.models import JobEvent

CHANNEL = "marquee_jobs"


class JobNotFoundError(LookupError):
    """No job has that id."""


class JobStateError(Exception):
    """The job's state does not allow what was asked; the message says why."""


def check_json(value: Any, what: str) -> None:
    """Raise :class:`ValueError` unless ``value`` is plain JSON with finite numbers."""
    try:
        json.dumps(value, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"the {what} is not plain JSON: {exc}") from exc


async def add_event(
    session: AsyncSession,
    job_id: int,
    event_type: str,
    message: str | None = None,
    data: Any = None,
) -> None:
    await session.execute(
        insert(JobEvent).values(job_id=job_id, type=event_type, message=message, data=data)
    )


async def notify(session: AsyncSession, message: str) -> None:
    """Wake the worker once the current transaction commits."""
    await session.execute(select(func.pg_notify(CHANNEL, message)))
