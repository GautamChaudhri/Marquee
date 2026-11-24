"""The worker: the one process that runs the queue (``marquee worker``).

Exactly one worker runs at a time. At start it takes a PostgreSQL advisory lock on a
connection of its own and holds it for its whole life; a second worker finds the lock taken
and exits. Since no other worker can exist, a job still ``running`` when it starts was left
by a worker that died, and is put back or failed right then: no heartbeats. Whether a
worker is alive is read from the lock (:func:`marquee.jobs.queries.worker_status`).

The same connection listens for "new job" and "cancel" notifications. The worker also polls
every few seconds, which is how it notices a ``run_after`` that has come due, and what keeps
the queue correct when a notification is missed. If that connection is lost, the lock is
gone with it, so the worker stops.

Each pass of the loop queues what the schedules say is due, claims jobs while their class
has a free slot and runs each as a task, passes cancel requests on to running jobs, and
stops the GPU child once it has waited ``GPU_IDLE_SECONDS`` for a job. Each job works in
database sessions of its own; no session is shared between tasks.

On SIGTERM or SIGINT the worker stops claiming, signals cancel to what is running, waits a
short while, puts what did not finish back in the queue without counting the attempt,
stops the GPU child, and exits 0.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal
import threading
import time
from collections import Counter
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field, replace
from typing import Any

import psycopg
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from marquee.cancellation import JobCancelledError
from marquee.config import settings
from marquee.db.engine import create_engine, create_session_factory
from marquee.db.models import Job
from marquee.jobs import queue, schedules
from marquee.jobs.gpu_child import GpuChild, describe_error
from marquee.jobs.registry import (
    JobContext,
    JobError,
    JobKind,
    PermanentJobError,
    Registry,
    default_registry,
    free_classes,
)
from marquee.jobs.store import CHANNEL, WORKER_LOCK_KEY

logger = logging.getLogger(__name__)

APPLICATION_NAME = "marquee-worker"
POLL_S = 5.0
STOP_GRACE_S = 10.0
PROGRESS_INTERVAL_S = 1.0
CONNECT_TIMEOUT_S = 10


class WorkerAlreadyRunningError(Exception):
    """Another worker holds the lock."""


class WorkerLostLockError(Exception):
    """The connection holding the lock was lost, so this worker must stop."""


def libpq_url(url: str) -> str:
    """A SQLAlchemy URL as psycopg takes it. It holds the password: never log it."""
    return make_url(url).set(drivername="postgresql").render_as_string(hide_password=False)


def scrub(text: str) -> str:
    for secret in settings.secret_values():
        text = text.replace(secret, "***")
    return text


class _Progress:
    """A job's progress function: writes at most about once a second, the latest value wins.

    It may be called from the event loop or from a thread a handler started.
    """

    def __init__(self, write: Callable[[tuple], Awaitable[None]]) -> None:
        self._write = write
        self._loop = asyncio.get_running_loop()
        self._latest: tuple | None = None
        self._last = 0.0
        self._timer: asyncio.TimerHandle | None = None
        self._writes: set[asyncio.Task[None]] = set()

    def __call__(
        self, current: int | None = None, total: int | None = None, message: str | None = None
    ) -> None:
        self._loop.call_soon_threadsafe(self._set, (current, total, message))

    def _set(self, value: tuple) -> None:
        self._latest = value
        if self._timer is None:
            delay = max(0.0, self._last + PROGRESS_INTERVAL_S - time.monotonic())
            self._timer = self._loop.call_later(delay, self._flush)

    def _flush(self) -> None:
        self._timer = None
        value, self._latest = self._latest, None
        if value is None:
            return
        self._last = time.monotonic()
        task = asyncio.ensure_future(self._write(value))
        self._writes.add(task)
        task.add_done_callback(self._writes.discard)

    async def close(self) -> None:
        """Write what is pending, so the last progress is kept, and wait for the writes."""
        await asyncio.sleep(0)  # let calls already handed to the loop arrive
        if self._timer is not None:
            self._timer.cancel()
            self._flush()
        if self._writes:
            await asyncio.gather(*self._writes, return_exceptions=True)


@dataclass
class _Running:
    job_id: int
    job_class: str
    cancel: threading.Event = field(default_factory=threading.Event)
    task: asyncio.Task[None] | None = None
    asked_to_cancel: bool = False
    """Someone asked for this job to stop (as opposed to the worker stopping)."""
    stopping: bool = False


class Worker:
    def __init__(
        self,
        *,
        registry: Registry | None = None,
        database_url: str | None = None,
        schedules: Sequence[schedules.Schedule] = schedules.SCHEDULES,
        drain: bool = False,
        poll_s: float = POLL_S,
        stop_grace_s: float = STOP_GRACE_S,
        gpu_idle_s: float | None = None,
    ) -> None:
        self._registry = default_registry() if registry is None else registry
        self._url = database_url or settings.database_url
        # A drain empties the queue as it is; it does not queue scheduled work.
        self._schedules = () if drain else schedules
        self._drain = drain
        self._poll_s = poll_s
        self._stop_grace_s = stop_grace_s
        self._gpu_idle_s = settings.GPU_IDLE_SECONDS if gpu_idle_s is None else gpu_idle_s
        self._running: dict[int, _Running] = {}
        self._stopping = False
        self._wake: asyncio.Event | None = None
        self._gpu_lock: asyncio.Lock | None = None
        self._sessions: async_sessionmaker[AsyncSession] | None = None
        self.gpu = GpuChild()

    def request_stop(self) -> None:
        """Stop claiming and shut down gracefully (what SIGTERM and SIGINT do)."""
        self._stopping = True
        if self._wake is not None:
            self._wake.set()

    async def run(self) -> None:
        """Run until stopped (or, with ``drain``, until nothing is runnable)."""
        self._wake = asyncio.Event()
        self._gpu_lock = asyncio.Lock()
        engine = create_engine(self._url)
        self._sessions = create_session_factory(engine)
        conn = await psycopg.AsyncConnection.connect(
            libpq_url(self._url),
            autocommit=True,
            application_name=APPLICATION_NAME,
            connect_timeout=CONNECT_TIMEOUT_S,
        )
        try:
            cursor = await conn.execute("SELECT pg_try_advisory_lock(%s)", [WORKER_LOCK_KEY])
            row = await cursor.fetchone()
            if not row or not row[0]:
                raise WorkerAlreadyRunningError("a worker is already running")
            await conn.execute(f"LISTEN {CHANNEL}")
            async with self._sessions() as session:
                orphans = await queue.recover_orphans(session)
            logger.info(
                "WORKER | started%s; %d orphaned job(s) recovered",
                " (drain)" if self._drain else "",
                len(orphans),
            )
            listener = asyncio.create_task(self._listen(conn))
            try:
                await self._loop(listener)
            finally:
                listener.cancel()
                with contextlib.suppress(asyncio.CancelledError, psycopg.Error):
                    await listener
                await self._shutdown()
        finally:
            await conn.close()
            await engine.dispose()
        logger.info("WORKER | stopped")

    async def _listen(self, conn: psycopg.AsyncConnection) -> None:
        assert self._wake is not None
        async for _ in conn.notifies():
            self._wake.set()

    async def _loop(self, listener: asyncio.Task[None]) -> None:
        assert self._wake is not None
        while not self._stopping:
            if listener.done():
                cause = None if listener.cancelled() else listener.exception()
                raise WorkerLostLockError("the worker's lock connection was lost") from cause
            claimed = 0
            try:
                await self._run_schedules()
                claimed = await self._claim()
                await self._pass_cancels()
                await self._stop_idle_gpu()
            except (SQLAlchemyError, OSError) as exc:
                logger.warning("WORKER | database unavailable: %s", scrub(describe_error(exc)))
            else:
                if self._drain and not self._running and not claimed:
                    return
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._wake.wait(), self._poll_s)
            self._wake.clear()

    async def _run_schedules(self) -> None:
        if not self._schedules:
            return
        assert self._sessions is not None
        async with self._sessions() as session:
            await schedules.run_due(session, self._schedules, self._registry)

    async def _claim(self) -> int:
        assert self._sessions is not None
        claimed = 0
        while not self._stopping:
            classes = free_classes(Counter(r.job_class for r in self._running.values()))
            if not classes:
                break
            async with self._sessions() as session:
                job = await queue.claim(session, classes)
            if job is None:
                break
            entry = _Running(job.id, job.job_class)
            self._running[job.id] = entry
            entry.task = asyncio.create_task(self._run_job(job, entry), name=f"job-{job.id}")
            claimed += 1
        return claimed

    async def _pass_cancels(self) -> None:
        assert self._sessions is not None
        waiting = [r.job_id for r in self._running.values() if not r.asked_to_cancel]
        if not waiting:
            return
        async with self._sessions() as session:
            asked = await queue.cancels_requested(session, waiting)
        for job_id in asked:
            entry = self._running.get(job_id)
            if entry is not None:
                logger.info("JOB | %d asked to cancel", job_id)
                entry.asked_to_cancel = True
                entry.cancel.set()

    async def _stop_idle_gpu(self) -> None:
        assert self._gpu_lock is not None
        idle = self.gpu.idle_seconds()
        if idle is None or idle < self._gpu_idle_s or self._gpu_lock.locked():
            return
        async with self._gpu_lock:
            logger.info("GPU | idle for %.0f s; stopping the child", idle)
            await asyncio.to_thread(self.gpu.stop)

    async def _run_job(self, job: Job, entry: _Running) -> None:
        kind = self._registry.get(job.kind)
        progress = _Progress(lambda value: self._record_progress(job.id, value))
        started = time.monotonic()
        timeout = asyncio.timeout(kind.timeout_s if kind else None)
        try:
            if kind is None:
                raise PermanentJobError(f"unknown job kind {job.kind!r}")
            context = JobContext(job.id, dict(job.payload), entry.cancel, progress)
            if kind.job_class == "gpu" and kind.handler is not None:
                context = replace(context, gpu_runner=self._gpu_runner(context))
            async with timeout:
                result = await self._handle(kind, context)
            outcome: tuple = ("succeed", result)
        except asyncio.CancelledError:
            # The worker is stopping and gave up waiting; it requeues this job itself.
            await progress.close()
            self._running.pop(job.id, None)
            raise
        except TimeoutError as exc:
            if not timeout.expired():
                outcome = ("fail", describe_error(exc), False)
            else:
                entry.cancel.set()
                outcome = ("fail", f"timed out after {kind.timeout_s:g} s", False)  # type: ignore[union-attr]
        except JobCancelledError as exc:
            requeue = entry.stopping and not entry.asked_to_cancel
            outcome = ("requeue",) if requeue else ("cancelled", str(exc) or "cancelled")
        except PermanentJobError as exc:
            outcome = ("fail", describe_error(exc), True)
        except Exception as exc:  # noqa: BLE001 - the boundary: any failure fails the attempt
            if not isinstance(exc, JobError):  # a JobError's message already says it all
                logger.exception("JOB | %d %s raised", job.id, job.kind)
            outcome = ("fail", describe_error(exc), False)
        await progress.close()
        try:
            await self._record(job, outcome, time.monotonic() - started)
        except (SQLAlchemyError, OSError) as exc:
            # The row stays running; the next worker start recovers it as an orphan.
            logger.error("JOB | %d outcome not recorded: %s", job.id, scrub(describe_error(exc)))
        finally:
            self._running.pop(job.id, None)
            if self._wake is not None:
                self._wake.set()

    async def _handle(self, kind: JobKind, context: JobContext) -> Any:
        if kind.gpu_function is None:
            assert kind.handler is not None
            return await kind.handler(context)
        return await self._in_child(kind.gpu_function, context.payload, context)

    async def _in_child(self, function: str, payload: dict[str, Any], context: JobContext) -> Any:
        assert self._gpu_lock is not None
        async with self._gpu_lock:
            return await self.gpu.run(function, payload, context.cancel, context.progress)

    def _gpu_runner(self, context: JobContext) -> Callable[[str, dict[str, Any]], Awaitable[Any]]:
        """What ``ctx.gpu`` calls for a gpu kind's handler: this job's own cancel and progress."""

        async def run(function: str, payload: dict[str, Any]) -> Any:
            return await self._in_child(function, payload, context)

        return run

    async def _record(self, job: Job, outcome: tuple, seconds: float) -> None:
        assert self._sessions is not None
        async with self._sessions() as session:
            if outcome[0] == "succeed":
                try:
                    await queue.succeed(session, job.id, outcome[1])
                    state = "succeeded"
                except ValueError as exc:
                    await session.rollback()
                    state = await queue.fail(session, job.id, str(exc), permanent=True)
            elif outcome[0] == "fail":
                state = await queue.fail(session, job.id, scrub(outcome[1]), permanent=outcome[2])
            elif outcome[0] == "cancelled":
                await queue.mark_cancelled(session, job.id, scrub(outcome[1]))
                state = "cancelled"
            else:
                await queue.requeue(session, [job.id])
                state = "requeued"
        detail = f": {scrub(outcome[1])}" if outcome[0] == "fail" else ""
        logger.info("JOB | %d %s %s after %.1f s%s", job.id, job.kind, state, seconds, detail)

    async def _record_progress(self, job_id: int, value: tuple) -> None:
        assert self._sessions is not None
        current, total, message = value
        try:
            async with self._sessions() as session:
                await queue.record_progress(session, job_id, current, total, message)
        except (SQLAlchemyError, OSError, ValueError) as exc:
            logger.warning("JOB | %d progress not recorded: %s", job_id, describe_error(exc))

    async def _shutdown(self) -> None:
        running = list(self._running.values())
        if running:
            logger.info("WORKER | stopping; signalling cancel to %d job(s)", len(running))
            for entry in running:
                entry.stopping = True
                entry.cancel.set()
            tasks = [entry.task for entry in running if entry.task is not None]
            _, pending = await asyncio.wait(tasks, timeout=self._stop_grace_s)
            unfinished = [entry.job_id for entry in running if entry.task in pending]
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            if unfinished and self._sessions is not None:
                async with self._sessions() as session:
                    await queue.requeue(session, unfinished)
                logger.info("WORKER | put back %d unfinished job(s)", len(unfinished))
        await asyncio.to_thread(self.gpu.stop)


async def serve(worker: Worker) -> None:
    """Run ``worker`` with SIGTERM and SIGINT wired to a graceful stop."""
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(signum, worker.request_stop)
    try:
        await worker.run()
    finally:
        for signum in (signal.SIGTERM, signal.SIGINT):
            loop.remove_signal_handler(signum)
