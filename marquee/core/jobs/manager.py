"""Transactional job creation, resource admission, recovery, and history."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from marquee.config import settings
from marquee.models import Job, JobAttempt, JobEvent, JobResource, JobResourceReservation, JobWorker

logger = logging.getLogger(__name__)

TERMINAL = {"succeeded", "failed", "cancelled", "interrupted", "dead_letter"}
ACTIVE = {"claimed", "running", "waiting_external", "cancelling"}
RETRYABLE = {"letterbox_detect", "poster_pipeline", "library_sync", "subtitle_scan", "taste_map"}


def utcnow() -> datetime:
    return datetime.now(UTC)


class JobManager:
    """The sole writer for generic job lifecycle and resource reservations."""

    def __init__(self) -> None:
        self._listeners: dict[str, set[asyncio.Queue]] = {}

    @staticmethod
    def resource_capacity(key: str) -> int:
        if key == "gpu":
            return settings.JOB_GPU_SLOTS
        if key == "media_read":
            return settings.JOB_MEDIA_READ_SLOTS
        if key in {"media_write", "transcode", "maintenance_exclusive"}:
            return settings.JOB_MEDIA_WRITE_SLOTS if key != "maintenance_exclusive" else 1
        if key == "network_external":
            return settings.JOB_NETWORK_SLOTS
        if key.startswith("media-file:"):
            return 1
        return 1

    async def bootstrap_resources(self, db: AsyncSession) -> None:
        for key in ("gpu", "media_read", "media_write", "transcode", "network_external", "maintenance_exclusive"):
            resource = await db.get(JobResource, key)
            if resource is None:
                db.add(JobResource(key=key, capacity=self.resource_capacity(key)))
            else:
                resource.capacity = self.resource_capacity(key)
                resource.enabled = resource.capacity > 0
        await db.commit()

    async def create(
        self,
        db: AsyncSession,
        *,
        job_type: str,
        payload: dict[str, Any] | None = None,
        priority: int = 50,
        resources: dict[str, int] | None = None,
        parent_id: str | None = None,
        correlation_id: str | None = None,
        subject_type: str | None = None,
        subject_id: str | int | None = None,
        idempotency_key: str | None = None,
        status: str = "queued",
        max_attempts: int = 3,
    ) -> Job:
        if idempotency_key:
            existing = (
                await db.execute(select(Job).where(Job.idempotency_key == idempotency_key))
            ).scalar_one_or_none()
            if existing is not None:
                return existing
        job_id = uuid4().hex
        request = resources or {}
        for key in request:
            if await db.get(JobResource, key) is None:
                db.add(JobResource(key=key, capacity=self.resource_capacity(key)))
        job = Job(
            id=job_id,
            type=job_type,
            payload=payload or {},
            priority=priority,
            resource_request=request,
            parent_id=parent_id,
            root_id=parent_id or job_id,
            correlation_id=correlation_id or job_id,
            subject_type=subject_type,
            subject_id=str(subject_id) if subject_id is not None else None,
            idempotency_key=idempotency_key,
            status=status,
            max_attempts=max_attempts,
        )
        db.add(job)
        await db.flush()
        await self.emit(db, job, state=status, message="job created", persist=True)
        await db.commit()
        await db.refresh(job)
        return job

    async def emit(
        self,
        db: AsyncSession,
        job: Job,
        *,
        state: str,
        stage: str | None = None,
        message: str | None = None,
        detail: dict | None = None,
        attempt_id: int | None = None,
        persist: bool = True,
    ) -> None:
        event_data = {"job_id": job.id, "state": state, "stage": stage, "message": message, "detail": detail}
        if persist:
            event = JobEvent(job_id=job.id, attempt_id=attempt_id, state=state, stage=stage, message=message, detail=detail)
            db.add(event)
            await db.flush()
            event_data["id"] = event.id
        for queue in tuple(self._listeners.get(job.id, ())):
            with contextlib.suppress(asyncio.QueueFull):
                queue.put_nowait(event_data)

    async def _reserve(self, db: AsyncSession, job: Job, attempt: JobAttempt) -> bool:
        now = utcnow()
        # Serialize admission around the global maintenance fence.  PostgreSQL
        # holds this row lock only for the claim transaction, not job runtime.
        await db.execute(
            select(JobResource).where(JobResource.key == "maintenance_exclusive").with_for_update()
        )
        maintenance_active = (
            await db.execute(
                select(func.count(JobResourceReservation.id)).where(
                    JobResourceReservation.resource_key == "maintenance_exclusive",
                    JobResourceReservation.released_at.is_(None),
                )
            )
        ).scalar_one()
        if "maintenance_exclusive" not in job.resource_request and maintenance_active:
            return False
        if "maintenance_exclusive" in job.resource_request:
            any_active = (
                await db.execute(
                    select(func.count(JobResourceReservation.id)).where(
                        JobResourceReservation.released_at.is_(None)
                    )
                )
            ).scalar_one()
            if any_active:
                return False
        # Always lock resources in deterministic order to avoid deadlocks.
        for key, units in sorted(job.resource_request.items()):
            resource = (
                await db.execute(select(JobResource).where(JobResource.key == key).with_for_update())
            ).scalar_one_or_none()
            if resource is None or not resource.enabled or resource.capacity < int(units):
                return False
            in_use = (
                await db.execute(
                    select(func.coalesce(func.sum(JobResourceReservation.units), 0)).where(
                        JobResourceReservation.resource_key == key,
                        JobResourceReservation.released_at.is_(None),
                    )
                )
            ).scalar_one()
            if int(in_use) + int(units) > resource.capacity:
                return False
        expiry = now + timedelta(seconds=settings.JOB_LEASE_SECONDS)
        for key, units in job.resource_request.items():
            db.add(JobResourceReservation(job_id=job.id, attempt_id=attempt.id, resource_key=key, units=int(units), lease_expires_at=expiry))
        return True

    async def claim_next(self, db: AsyncSession, worker_id: str) -> tuple[Job, JobAttempt] | None:
        now = utcnow()
        candidates = (
            await db.execute(
                select(Job)
                .where(Job.status.in_(("queued", "retry_scheduled", "waiting_resource")), Job.scheduled_at <= now)
                .order_by(Job.priority.desc(), Job.created_at)
                .with_for_update(skip_locked=True)
                .limit(32)
            )
        ).scalars().all()
        for job in candidates:
            if job.cancel_requested:
                job.status = "cancelled"
                job.finished_at = now
                await self.emit(db, job, state="cancelled", message="cancelled before execution")
                continue
            if job.pause_requested:
                job.status = "paused"
                await self.emit(db, job, state="paused", message="paused before execution")
                continue
            attempt = JobAttempt(job_id=job.id, number=job.attempt_count + 1, worker_id=worker_id, status="claimed")
            db.add(attempt)
            await db.flush()
            if not await self._reserve(db, job, attempt):
                await db.delete(attempt)
                job.status = "waiting_resource"
                continue
            job.attempt_count += 1
            job.status = "claimed"
            job.claimed_at = now
            await self.emit(db, job, state="claimed", message=f"claimed by {worker_id}", attempt_id=attempt.id)
            await db.commit()
            return job, attempt
        await db.commit()
        return None

    async def start(self, db: AsyncSession, job: Job, attempt: JobAttempt) -> None:
        now = utcnow()
        job.status = "running"
        job.started_at = job.started_at or now
        attempt.status = "running"
        attempt.started_at = now
        attempt.heartbeat_at = now
        await self.emit(db, job, state="running", stage=job.current_stage, attempt_id=attempt.id)
        await db.commit()

    async def heartbeat(self, db: AsyncSession, attempt: JobAttempt) -> None:
        attempt.heartbeat_at = utcnow()
        await db.execute(
            update(JobResourceReservation)
            .where(JobResourceReservation.attempt_id == attempt.id, JobResourceReservation.released_at.is_(None))
            .values(lease_expires_at=utcnow() + timedelta(seconds=settings.JOB_LEASE_SECONDS))
        )
        await db.commit()

    async def _release(self, db: AsyncSession, attempt_id: int) -> None:
        await db.execute(
            update(JobResourceReservation)
            .where(JobResourceReservation.attempt_id == attempt_id, JobResourceReservation.released_at.is_(None))
            .values(released_at=utcnow())
        )

    async def finish(self, db: AsyncSession, job: Job, attempt: JobAttempt, *, result: dict | None = None) -> None:
        now = utcnow()
        job.status = "succeeded"
        job.result = result or {}
        job.finished_at = now
        attempt.status = "succeeded"
        attempt.finished_at = now
        attempt.metrics = {"runtime_seconds": (now - (attempt.started_at or now)).total_seconds()}
        await self._release(db, attempt.id)
        await self.emit(db, job, state="succeeded", stage=job.current_stage, attempt_id=attempt.id, detail=job.result)
        await self._update_parent(db, job.parent_id)
        await db.commit()

    async def fail(self, db: AsyncSession, job: Job, attempt: JobAttempt, exc: Exception) -> None:
        now = utcnow()
        error = {"type": type(exc).__name__, "message": str(exc)}
        await self._release(db, attempt.id)
        attempt.status = "failed"
        attempt.finished_at = now
        attempt.error = error
        if job.cancel_requested:
            job.status = "cancelled"
            job.finished_at = now
        elif job.type in RETRYABLE and job.attempt_count < job.max_attempts:
            job.status = "retry_scheduled"
            job.scheduled_at = now + timedelta(seconds=min(600, 30 * (2 ** (job.attempt_count - 1))))
        else:
            job.status = "dead_letter" if job.attempt_count >= job.max_attempts else "failed"
            job.finished_at = now
        job.error = error
        await self.emit(db, job, state=job.status, stage=job.current_stage, message=str(exc), detail=error, attempt_id=attempt.id)
        await self._update_parent(db, job.parent_id)
        await db.commit()

    async def _update_parent(self, db: AsyncSession, parent_id: str | None) -> None:
        if parent_id is None:
            return
        parent = await db.get(Job, parent_id)
        if parent is None:
            return
        statuses = (
            await db.execute(select(Job.status).where(Job.parent_id == parent_id))
        ).scalars().all()
        total = len(statuses)
        completed = sum(status in TERMINAL for status in statuses)
        failed = sum(status in {"failed", "dead_letter", "interrupted", "cancelled"} for status in statuses)
        parent.progress = {"children_total": total, "children_completed": completed, "children_failed": failed}
        if total and completed == total:
            parent.status = "failed" if failed else "succeeded"
            parent.finished_at = utcnow()
            await self.emit(db, parent, state=parent.status, message="all child jobs terminal", detail=parent.progress)

    async def interrupt(self, db: AsyncSession, job: Job, attempt: JobAttempt, *, reason: str) -> None:
        """Record controlled worker shutdown without pretending work failed."""
        now = utcnow()
        await self._release(db, attempt.id)
        attempt.status = "interrupted"
        attempt.finished_at = now
        attempt.error = {"type": "Interrupted", "message": reason}
        job.status = "interrupted"
        job.finished_at = now
        job.error = attempt.error
        await self.emit(db, job, state="interrupted", message=reason, attempt_id=attempt.id)
        await db.commit()

    async def request_cancel(self, db: AsyncSession, job: Job) -> Job:
        job.cancel_requested = True
        if job.status in {"planned", "queued", "waiting_resource", "retry_scheduled", "paused"}:
            job.status = "cancelled"
            job.finished_at = utcnow()
        elif job.status == "waiting_external":
            job.status = "cancelling"
        media_job_id = job.payload.get("media_job_id") if isinstance(job.payload, dict) else None
        if media_job_id:
            # Bridge cancellation into the established media handlers, which
            # poll this flag while encoding/remuxing.
            from marquee.models import MediaJob  # noqa: PLC0415

            media_job = await db.get(MediaJob, media_job_id)
            if media_job is not None:
                media_job.cancel_requested = True
        await self.emit(db, job, state=job.status, message="cancellation requested")
        await db.commit()
        return job

    async def set_paused(self, db: AsyncSession, job: Job, paused: bool) -> Job:
        job.pause_requested = paused
        if paused and job.status in {"queued", "waiting_resource", "retry_scheduled"}:
            job.status = "paused"
        elif not paused and job.status == "paused":
            job.status = "queued"
            job.scheduled_at = utcnow()
        await self.emit(db, job, state=job.status, message="pause requested" if paused else "resumed")
        await db.commit()
        return job

    async def recover(self, db: AsyncSession) -> int:
        cutoff = utcnow() - timedelta(seconds=settings.JOB_LEASE_SECONDS)
        attempts = (
            await db.execute(
                select(JobAttempt, Job)
                .join(Job, Job.id == JobAttempt.job_id)
                .where(Job.status.in_(("claimed", "running")), JobAttempt.heartbeat_at < cutoff)
                .with_for_update(skip_locked=True)
            )
        ).all()
        for attempt, job in attempts:
            await self._release(db, attempt.id)
            attempt.status = "interrupted"
            attempt.finished_at = utcnow()
            if job.type in RETRYABLE and job.attempt_count < job.max_attempts:
                job.status, job.scheduled_at = "retry_scheduled", utcnow()
            else:
                job.status, job.finished_at = "interrupted", utcnow()
            await self.emit(db, job, state=job.status, message="worker lease expired", attempt_id=attempt.id)
        # Reap worker rows whose heartbeat went stale (crashed/killed without a
        # clean shutdown) so /metrics doesn't keep reporting ghosts as running.
        await db.execute(
            update(JobWorker)
            .where(JobWorker.status.notin_(("stopped", "dead")), JobWorker.heartbeat_at < cutoff)
            .values(status="dead")
        )
        await db.commit()
        return len(attempts)

    def subscribe(self, job_id: str) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=100)
        self._listeners.setdefault(job_id, set()).add(queue)
        return queue

    def unsubscribe(self, job_id: str, queue: asyncio.Queue) -> None:
        listeners = self._listeners.get(job_id)
        if listeners:
            listeners.discard(queue)
            if not listeners:
                self._listeners.pop(job_id, None)


job_manager = JobManager()
