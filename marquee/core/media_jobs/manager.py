"""MediaJobManager — durable queue, serial worker, SSE, and restart recovery.

The worker processes ``queued`` jobs oldest-first, one at a time (remuxes are
I/O-bound; overlapping them thrashes the disk). Each job takes a per-media-file
lock. Progress events are persisted to ``media_job_events`` and mirrored to live
SSE subscribers. On startup, jobs left ``running`` become ``interrupted`` (a
crash mid-remux leaves only a discardable ``.partial`` beside the source).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from marquee.database import _get_session_factory
from marquee.models import MediaJob, MediaJobEvent

logger = logging.getLogger(__name__)

_SENTINEL = object()


class JobStream:
    """Live in-memory fan-out for one job's events (DB is the durable record)."""

    def __init__(self) -> None:
        self.subscribers: list[asyncio.Queue] = []
        self.done = False

    def publish(self, event: dict) -> None:
        for queue in self.subscribers:
            queue.put_nowait(event)

    def finish(self) -> None:
        self.done = True
        for queue in self.subscribers:
            queue.put_nowait(_SENTINEL)

    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue()
        if self.done:
            queue.put_nowait(_SENTINEL)
        self.subscribers.append(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        with contextlib.suppress(ValueError):
            self.subscribers.remove(queue)


class MediaJobManager:
    def __init__(self) -> None:
        self._streams: dict[str, JobStream] = {}
        self._locks: dict[int, asyncio.Lock] = {}
        self._worker: asyncio.Task | None = None
        self._running = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        await self.recover()
        self._running = True
        self._worker = asyncio.create_task(self._worker_loop())
        logger.info("MediaJobManager worker started")

    async def stop(self) -> None:
        self._running = False
        if self._worker is not None:
            self._worker.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._worker

    async def recover(self) -> None:
        """Mark crashed ``running`` jobs ``interrupted`` (design §22.3)."""
        factory = _get_session_factory()
        async with factory() as db:
            await db.execute(
                update(MediaJob).where(MediaJob.status == "running").values(status="interrupted")
            )
            await db.commit()

    # ------------------------------------------------------------------
    # Events / SSE
    # ------------------------------------------------------------------

    def stream(self, job_id: str) -> JobStream:
        return self._streams.setdefault(job_id, JobStream())

    async def emit(
        self, db: AsyncSession, job_id: str, stage: str, state: str,
        *, message: str | None = None, progress: dict | None = None,
    ) -> None:
        event = MediaJobEvent(
            job_id=job_id, stage=stage, state=state, message=message,
            progress_json=json.dumps(progress) if progress else None,
        )
        db.add(event)
        await db.commit()
        await db.refresh(event)
        self.stream(job_id).publish(
            {
                "id": event.id, "job_id": job_id, "stage": stage, "state": state,
                "message": message, "progress": progress,
            }
        )

    async def persisted_events(self, db: AsyncSession, job_id: str) -> list[dict]:
        rows = (
            await db.execute(
                select(MediaJobEvent).where(MediaJobEvent.job_id == job_id).order_by(MediaJobEvent.id)
            )
        ).scalars().all()
        return [
            {
                "id": r.id, "job_id": job_id, "stage": r.stage, "state": r.state,
                "message": r.message,
                "progress": json.loads(r.progress_json) if r.progress_json else None,
            }
            for r in rows
        ]

    # ------------------------------------------------------------------
    # Job creation / control
    # ------------------------------------------------------------------

    async def create_job(
        self, db: AsyncSession, *, operation: str, media_file_id: int | None,
        trigger: str = "manual", request: dict | None = None, plan: dict | None = None,
        status: str = "planned", input_signature: str | None = None,
        plan_expires_at: datetime | None = None, idempotency_key: str | None = None,
        batch_id: str | None = None,
    ) -> MediaJob:
        job = MediaJob(
            job_id=uuid4().hex, operation=operation, media_file_id=media_file_id,
            trigger=trigger, status=status,
            request_json=json.dumps(request) if request else None,
            plan_json=json.dumps(plan) if plan else None,
            input_signature=input_signature, plan_expires_at=plan_expires_at,
            idempotency_key=idempotency_key, batch_id=batch_id,
        )
        db.add(job)
        await db.commit()
        await db.refresh(job)
        return job

    async def cancel(self, db: AsyncSession, job_id: str) -> bool:
        job = await db.get(MediaJob, job_id)
        if job is None:
            return False
        if job.status in ("planned", "queued"):
            job.status = "cancelled"
        else:
            job.cancel_requested = True
        await db.commit()
        return True

    # ------------------------------------------------------------------
    # Worker
    # ------------------------------------------------------------------

    async def _worker_loop(self) -> None:
        while self._running:
            try:
                job_id = await self._next_queued_job_id()
                if job_id is None:
                    await asyncio.sleep(2.0)
                    continue
                await self._run_job(job_id)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("media job worker iteration failed")
                await asyncio.sleep(2.0)

    async def _next_queued_job_id(self) -> str | None:
        factory = _get_session_factory()
        async with factory() as db:
            row = (
                await db.execute(
                    select(MediaJob.job_id)
                    .where(MediaJob.status == "queued")
                    .order_by(MediaJob.created_at)
                    .limit(1)
                )
            ).scalar_one_or_none()
            return row

    async def _run_job(self, job_id: str) -> None:
        from marquee.core.media_jobs.handlers import dispatch  # noqa: PLC0415

        factory = _get_session_factory()
        async with factory() as db:
            job = await db.get(MediaJob, job_id)
            if job is None or job.status != "queued":
                return
            if job.cancel_requested:
                job.status = "cancelled"
                await db.commit()
                return
            lock = self._locks.setdefault(job.media_file_id or -1, asyncio.Lock())
            async with lock:
                job.status = "running"
                job.attempts += 1
                await db.commit()
                await self.emit(db, job_id, "start", "running", message=job.operation)
                try:
                    result = await dispatch(db, job, self.emit)
                    job.status = "succeeded"
                    job.result_json = json.dumps(result, default=str)
                    await db.commit()
                except Exception as exc:  # noqa: BLE001 — recorded on the job
                    logger.exception("media job %s failed", job_id)
                    code = getattr(exc, "code", None)
                    job.status = "cancelled" if code == "cancelled" else "failed"
                    job.error_json = json.dumps({"error": str(exc), "code": getattr(exc, "code", None)})
                    await db.commit()
                    await self.emit(db, job_id, "error", job.status, message=str(exc))
                finally:
                    if job.batch_id:
                        await self._update_batch(db, job.batch_id)
            self.stream(job_id).finish()

    async def _update_batch(self, db: AsyncSession, batch_id: str) -> None:
        from marquee.models import MediaBatch  # noqa: PLC0415

        batch = await db.get(MediaBatch, batch_id)
        if batch is None:
            return
        rows = (
            await db.execute(select(MediaJob.status).where(MediaJob.batch_id == batch_id))
        ).scalars().all()
        batch.completed_count = sum(1 for s in rows if s == "succeeded")
        batch.failed_count = sum(1 for s in rows if s == "failed")
        terminal = {"succeeded", "failed", "cancelled", "interrupted"}
        if all(s in terminal for s in rows):
            batch.status = "completed"
        batch.updated_at = datetime.now(UTC)
        await db.commit()


media_job_manager = MediaJobManager()
