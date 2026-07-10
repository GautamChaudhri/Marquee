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

import asyncio
import contextvars
import logging
import os
import signal

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

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
    try:
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
    except SQLAlchemyError:
        logger.warning("could not clear tracked child pid=%d", pid, exc_info=True)


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


async def terminate_child_pids(
    db: AsyncSession,
    attempt: JobAttempt,
    *,
    grace_seconds: float = 2.0,
) -> list[int]:
    """Terminate child PIDs tracked on ``attempt`` and clear stale records."""
    pids = [int(pid) for pid in (attempt.child_pids or [])]
    if not pids:
        return []

    signalled: list[int] = []
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
            signalled.append(pid)
        except ProcessLookupError:
            pass
        except PermissionError:
            logger.warning("permission denied sending SIGTERM to child pid=%d", pid)

    if signalled and grace_seconds > 0:
        await asyncio.sleep(grace_seconds)

    killed: list[int] = []
    for pid in pids:
        if not _pid_alive(pid):
            continue
        try:
            os.kill(pid, signal.SIGKILL)
            killed.append(pid)
        except ProcessLookupError:
            pass
        except PermissionError:
            logger.warning("permission denied sending SIGKILL to child pid=%d", pid)

    attempt.child_pids = []
    await db.flush()
    return signalled + [pid for pid in killed if pid not in signalled]
