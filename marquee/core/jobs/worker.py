"""Dedicated durable worker process; never run this loop inside FastAPI."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import socket
from datetime import UTC, datetime
from uuid import uuid4

from marquee.config import settings
from marquee.core.jobs import (
    builtin_handlers,  # noqa: F401 - registers handlers
    dovi_handlers,  # noqa: F401 - registers dovi analysis handler
    legacy_media,  # noqa: F401 - registers bridge handlers
)
from marquee.core.jobs.child_tracking import current_attempt_id
from marquee.core.jobs.handlers import resolve
from marquee.core.jobs.manager import job_manager
from marquee.database import _get_session_factory, close_db, init_db
from marquee.models import Job, JobAttempt, JobWorker

logger = logging.getLogger(__name__)


class DurableWorker:
    """The claim/execute loop. Distinct from the ``JobWorker`` ORM row it writes."""

    def __init__(self) -> None:
        self.id = f"{socket.gethostname()}-{uuid4().hex[:12]}"
        self._stopping = asyncio.Event()
        self._tasks: set[asyncio.Task] = set()
        self._next_recover_at = 0.0

    async def _recover_if_due(self) -> None:
        now = asyncio.get_running_loop().time()
        if now < self._next_recover_at:
            return
        self._next_recover_at = now + settings.JOB_HEARTBEAT_SECONDS
        factory = _get_session_factory()
        async with factory() as db:
            recovered = await job_manager.recover(db)
        if recovered:
            logger.info("Recovered %s stale job attempt(s)", recovered)

    async def _heartbeat(self) -> None:
        factory = _get_session_factory()
        while not self._stopping.is_set():
            async with factory() as db:
                worker = await db.get(JobWorker, self.id)
                if worker:
                    worker.status = "draining" if self._stopping.is_set() else "running"
                    worker.heartbeat_at = datetime.now(UTC)
                    await db.commit()
            try:
                await self._recover_if_due()
            except Exception:  # noqa: BLE001
                logger.exception("periodic job recovery failed")
            await asyncio.sleep(settings.JOB_HEARTBEAT_SECONDS)

    async def _run_claim(self, job: Job, attempt: JobAttempt) -> None:
        factory = _get_session_factory()
        async with factory() as db:
            current = await db.get(Job, job.id)
            current_attempt = await db.get(JobAttempt, attempt.id)
            if current is None or current_attempt is None:
                return
            await job_manager.start(db, current, current_attempt)
            handler = resolve(current.type)
            if handler is None:
                await job_manager.fail(
                    db, current, current_attempt, RuntimeError(f"no handler for {current.type!r}")
                )
                return

            async def renew_lease() -> None:
                while True:
                    await asyncio.sleep(settings.JOB_HEARTBEAT_SECONDS)
                    async with factory() as heartbeat_db:
                        heartbeat_attempt = await heartbeat_db.get(JobAttempt, attempt.id)
                        if heartbeat_attempt is None or heartbeat_attempt.status not in {
                            "claimed",
                            "running",
                        }:
                            return
                        await job_manager.heartbeat(heartbeat_db, heartbeat_attempt)

            heartbeat = asyncio.create_task(renew_lease())
            attempt_token = current_attempt_id.set(current_attempt.id)
            try:
                # Enforce maximum runtime to prevent hung jobs (PaddleOCR GPU hang,
                # ffmpeg stall, infinite loop in handler). Default: 1 hour.
                timeout = settings.JOB_MAX_RUNTIME_SECONDS
                result = await asyncio.wait_for(handler(current), timeout=timeout)
            except TimeoutError:
                logger.error(
                    "job %s exceeded max runtime (%ds) — terminating",
                    current.id,
                    settings.JOB_MAX_RUNTIME_SECONDS,
                )
                await job_manager.fail(
                    db,
                    current,
                    current_attempt,
                    TimeoutError(f"Job exceeded max runtime ({settings.JOB_MAX_RUNTIME_SECONDS}s)"),
                )
            except asyncio.CancelledError:
                await job_manager.interrupt(db, current, current_attempt, reason="worker shutdown")
                raise
            except Exception as exc:  # noqa: BLE001
                logger.exception("job %s failed", current.id)
                await job_manager.fail(db, current, current_attempt, exc)
            else:
                await job_manager.finish(db, current, current_attempt, result=result or {})
            finally:
                current_attempt_id.reset(attempt_token)
                heartbeat.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await heartbeat

    async def run(self) -> None:
        await init_db()
        factory = _get_session_factory()
        async with factory() as db:
            await job_manager.bootstrap_resources(db)
            await job_manager.recover(db)
            db.add(
                JobWorker(
                    id=self.id,
                    capabilities={"worker_concurrency": settings.JOB_WORKER_CONCURRENCY},
                    status="running",
                )
            )
            await db.commit()
        self._next_recover_at = asyncio.get_running_loop().time() + settings.JOB_HEARTBEAT_SECONDS
        heartbeat = asyncio.create_task(self._heartbeat())
        try:
            while not self._stopping.is_set():
                if len(self._tasks) >= settings.JOB_WORKER_CONCURRENCY:
                    done, _ = await asyncio.wait(self._tasks, return_when=asyncio.FIRST_COMPLETED)
                    self._tasks.difference_update(done)
                    continue
                async with factory() as db:
                    claim = await job_manager.claim_next(db, self.id)
                if claim is None:
                    await asyncio.sleep(settings.JOB_POLL_SECONDS)
                    continue
                task = asyncio.create_task(self._run_claim(*claim))
                self._tasks.add(task)
        finally:
            self._stopping.set()
            heartbeat.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heartbeat
            if self._tasks:
                done, pending = await asyncio.wait(
                    self._tasks, timeout=settings.JOB_SHUTDOWN_GRACE_SECONDS
                )
                for task in pending:
                    task.cancel()
                if pending:
                    await asyncio.gather(*pending, return_exceptions=True)
            async with factory() as db:
                worker = await db.get(JobWorker, self.id)
                if worker:
                    worker.status = "stopped"
                    worker.heartbeat_at = datetime.now(UTC)
                    await db.commit()
            await close_db()

    def stop(self) -> None:
        self._stopping.set()


async def main() -> None:
    from marquee.logging import setup_logging

    setup_logging(level=settings.LOG_LEVEL, fmt=settings.LOG_FORMAT)

    worker = DurableWorker()
    loop = asyncio.get_running_loop()
    import signal  # noqa: PLC0415

    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, worker.stop)
    try:
        await worker.run()
    except KeyboardInterrupt:
        worker.stop()


if __name__ == "__main__":
    asyncio.run(main())
