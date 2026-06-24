"""Track subprocess PIDs spawned by a running job attempt.

``JobAttempt.child_pids`` lets the supervisor's orphan-killer
(``WorkerSupervisor._kill_orphaned_children``) find and SIGKILL ffmpeg/PaddleOCR
processes left behind if the worker that spawned them is hard-killed (OOM,
SIGKILL) mid-job. That only works if something actually populates the column —
this module is the plumbing for that: ``worker.py`` sets the current attempt id
in a contextvar around each handler invocation, and subprocess-spawning code
calls ``record_child_pid``/``clear_child_pid`` right after spawning / on exit.
"""

from __future__ import annotations

import contextvars
import logging

from sqlalchemy import select

from marquee.database import _get_session_factory
from marquee.models import JobAttempt

logger = logging.getLogger(__name__)

current_attempt_id: contextvars.ContextVar[int | None] = contextvars.ContextVar(
    "current_attempt_id", default=None
)


async def record_child_pid(pid: int) -> None:
    """Add ``pid`` to the current job attempt's tracked child PIDs, if any."""
    attempt_id = current_attempt_id.get()
    if attempt_id is None:
        return
    factory = _get_session_factory()
    async with factory() as db:
        attempt = (
            await db.execute(
                select(JobAttempt).where(JobAttempt.id == attempt_id).with_for_update()
            )
        ).scalar_one_or_none()
        if attempt is None:
            return
        pids = list(attempt.child_pids or [])
        if pid not in pids:
            pids.append(pid)
            attempt.child_pids = pids
            await db.commit()


async def clear_child_pid(pid: int) -> None:
    """Remove ``pid`` from the current job attempt's tracked child PIDs."""
    attempt_id = current_attempt_id.get()
    if attempt_id is None:
        return
    factory = _get_session_factory()
    async with factory() as db:
        attempt = (
            await db.execute(
                select(JobAttempt).where(JobAttempt.id == attempt_id).with_for_update()
            )
        ).scalar_one_or_none()
        if attempt is None or not attempt.child_pids:
            return
        pids = [p for p in attempt.child_pids if p != pid]
        attempt.child_pids = pids
        await db.commit()
