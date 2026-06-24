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
        for key in (
            "gpu",
            "media_read",
            "media_write",
            "transcode",
            "network_external",
            "maintenance_exclusive",
        ):
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
        commit: bool = True,
    ) -> Job:
        if idempotency_key:
            existing = (
                await db.execute(select(Job).where(Job.idempotency_key == idempotency_key))
            ).scalar_one_or_none()
            if existing is not None:
                if existing.type != job_type:
                    raise ValueError(
                        f"idempotency_key {idempotency_key!r} already used by job "
                        f"{existing.id!r} of type {existing.type!r}; refusing to "
                        f"return it for a {job_type!r} request"
                    )
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
        if commit:
            await db.commit()
            await db.refresh(job)
        return job

    async def create_batch(
        self,
        db: AsyncSession,
        *,
        parent_type: str,
        parent_payload: dict[str, Any],
        parent_priority: int,
        parent_subject_type: str,
        parent_subject_id: str,
        children: list[dict[str, Any]],
    ) -> tuple[Job, list[Job]]:
        """Create a batch parent and every child in one visible transaction.

        A worker must never observe a partially-created batch: otherwise a fast
        first child can terminalize the parent before later children exist.
        """
        parent_id = uuid4().hex
        correlation_id = parent_id
        parent = Job(
            id=parent_id,
            type=parent_type,
            payload=parent_payload,
            priority=parent_priority,
            root_id=parent_id,
            correlation_id=correlation_id,
            subject_type=parent_subject_type,
            subject_id=parent_subject_id,
            status="waiting_external",
        )
        jobs: list[Job] = []
        resource_keys = {key for child in children for key in child.get("resources", {})}
        for key in resource_keys:
            if await db.get(JobResource, key) is None:
                db.add(JobResource(key=key, capacity=self.resource_capacity(key)))
        db.add(parent)
        await db.flush()
        await self.emit(db, parent, state=parent.status, message="batch created", persist=True)

        for child in children:
            job = Job(
                id=uuid4().hex,
                type=child["job_type"],
                payload=child.get("payload", {}),
                priority=child.get("priority", parent_priority),
                resource_request=child.get("resources", {}),
                parent_id=parent_id,
                root_id=parent_id,
                correlation_id=correlation_id,
                subject_type=child.get("subject_type"),
                subject_id=str(child["subject_id"])
                if child.get("subject_id") is not None
                else None,
                max_attempts=child.get("max_attempts", 3),
            )
            db.add(job)
            jobs.append(job)
        await db.flush()
        for job in jobs:
            await self.emit(db, job, state="queued", message="job created", persist=True)
        await db.commit()
        await db.refresh(parent)
        return parent, jobs

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
        event_data = {
            "job_id": job.id,
            "state": state,
            "stage": stage,
            "message": message,
            "detail": detail,
        }
        if persist:
            event = JobEvent(
                job_id=job.id,
                attempt_id=attempt_id,
                state=state,
                stage=stage,
                message=message,
                detail=detail,
            )
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
                await db.execute(
                    select(JobResource).where(JobResource.key == key).with_for_update()
                )
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
            db.add(
                JobResourceReservation(
                    job_id=job.id,
                    attempt_id=attempt.id,
                    resource_key=key,
                    units=int(units),
                    lease_expires_at=expiry,
                )
            )
        return True

    async def claim_next(self, db: AsyncSession, worker_id: str) -> tuple[Job, JobAttempt] | None:
        now = utcnow()
        candidates = (
            (
                await db.execute(
                    select(Job)
                    .where(
                        Job.status.in_(("queued", "retry_scheduled", "waiting_resource")),
                        Job.scheduled_at <= now,
                    )
                    .order_by(Job.priority.desc(), Job.created_at)
                    .with_for_update(skip_locked=True)
                    .limit(32)
                )
            )
            .scalars()
            .all()
        )
        for job in candidates:
            if job.cancel_requested:
                job.status = "cancelled"
                job.finished_at = now
                await self.emit(db, job, state="cancelled", message="cancelled before execution")
                await self._update_parent(db, job.parent_id, child=job)
                continue
            if job.pause_requested:
                job.status = "paused"
                await self.emit(db, job, state="paused", message="paused before execution")
                continue
            attempt = JobAttempt(
                job_id=job.id, number=job.attempt_count + 1, worker_id=worker_id, status="claimed"
            )
            db.add(attempt)
            await db.flush()
            if not await self._reserve(db, job, attempt):
                await db.delete(attempt)
                job.status = "waiting_resource"
                continue
            job.attempt_count += 1
            job.status = "claimed"
            job.claimed_at = now
            await self.emit(
                db, job, state="claimed", message=f"claimed by {worker_id}", attempt_id=attempt.id
            )
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
            .where(
                JobResourceReservation.attempt_id == attempt.id,
                JobResourceReservation.released_at.is_(None),
            )
            .values(lease_expires_at=utcnow() + timedelta(seconds=settings.JOB_LEASE_SECONDS))
        )
        await db.commit()

    async def _release(self, db: AsyncSession, attempt_id: int) -> None:
        await db.execute(
            update(JobResourceReservation)
            .where(
                JobResourceReservation.attempt_id == attempt_id,
                JobResourceReservation.released_at.is_(None),
            )
            .values(released_at=utcnow())
        )

    async def _release_job_reservations(
        self, db: AsyncSession, job_id: str, *, released_at: datetime
    ) -> None:
        await db.execute(
            update(JobResourceReservation)
            .where(
                JobResourceReservation.job_id == job_id,
                JobResourceReservation.released_at.is_(None),
            )
            .values(released_at=released_at)
        )

    async def _bridge_media_cancel(self, db: AsyncSession, job: Job) -> None:
        media_job_id = job.payload.get("media_job_id") if isinstance(job.payload, dict) else None
        if not media_job_id:
            return
        # Bridge cancellation into the established media handlers, which poll
        # this flag while encoding/remuxing.
        from marquee.models import MediaJob  # noqa: PLC0415

        media_job = await db.get(MediaJob, media_job_id)
        if media_job is not None:
            media_job.cancel_requested = True

    async def _sync_media_job_status(self, db: AsyncSession, job: Job) -> None:
        """Mirror a just-recovered generic Job's status onto its bridged MediaJob.

        Scoped to exactly the one job/attempt recover() found stale — never a
        sweep over all MediaJob rows — so a legitimately-running job in another
        worker process is never touched. No-op if the job isn't one of the
        legacy_media-bridged types (no media_job_id in its payload).
        """
        media_job_id = job.payload.get("media_job_id") if isinstance(job.payload, dict) else None
        if not media_job_id:
            return
        from marquee.models import MediaJob  # noqa: PLC0415

        media_job = await db.get(MediaJob, media_job_id)
        if media_job is None:
            return
        if job.status == "retry_scheduled":
            media_job.status = "queued"
            media_job.cancel_requested = False
        elif job.status == "cancelled":
            media_job.status = "cancelled"
        else:
            media_job.status = "interrupted"

    async def _cancel_before_execution(
        self,
        db: AsyncSession,
        job: Job,
        *,
        now: datetime,
        message: str,
    ) -> None:
        job.cancel_requested = True
        job.status = "cancelled"
        job.finished_at = now
        await self._bridge_media_cancel(db, job)
        attempts = (
            (
                await db.execute(
                    select(JobAttempt).where(
                        JobAttempt.job_id == job.id, JobAttempt.finished_at.is_(None)
                    )
                )
            )
            .scalars()
            .all()
        )
        for attempt in attempts:
            attempt.status = "interrupted"
            attempt.finished_at = now
            attempt.error = {"type": "Cancelled", "message": message}
        await self._release_job_reservations(db, job.id, released_at=now)
        await self.emit(db, job, state="cancelled", message=message)

    async def finish(
        self, db: AsyncSession, job: Job, attempt: JobAttempt, *, result: dict | None = None
    ) -> None:
        now = utcnow()
        job.status = "succeeded"
        job.result = result or {}
        job.finished_at = now
        attempt.status = "succeeded"
        attempt.finished_at = now
        attempt.metrics = {"runtime_seconds": (now - (attempt.started_at or now)).total_seconds()}
        await self._release(db, attempt.id)
        await self.emit(
            db,
            job,
            state="succeeded",
            stage=job.current_stage,
            attempt_id=attempt.id,
            detail=job.result,
        )
        await self._update_parent(db, job.parent_id, child=job)
        await db.commit()

    async def fail(self, db: AsyncSession, job: Job, attempt: JobAttempt, exc: Exception) -> None:
        now = utcnow()
        error = {"type": type(exc).__name__, "message": str(exc)}
        await self._release(db, attempt.id)
        attempt.status = "failed"
        attempt.finished_at = now
        attempt.error = error
        # Cancellation can be requested from another session while this handler
        # is running, so refresh the durable flag before deciding whether to
        # retry or terminalize the job.
        await db.refresh(job, ["cancel_requested"])
        if job.cancel_requested:
            job.status = "cancelled"
            job.finished_at = now
        elif job.type in RETRYABLE and job.attempt_count < job.max_attempts:
            job.status = "retry_scheduled"
            job.scheduled_at = now + timedelta(
                seconds=min(600, 30 * (2 ** (job.attempt_count - 1)))
            )
        else:
            job.status = "dead_letter" if job.attempt_count >= job.max_attempts else "failed"
            job.finished_at = now
        job.error = error
        await self.emit(
            db,
            job,
            state=job.status,
            stage=job.current_stage,
            message=str(exc),
            detail=error,
            attempt_id=attempt.id,
        )
        await self._update_parent(db, job.parent_id, child=job)
        await db.commit()

    async def _update_parent(
        self, db: AsyncSession, parent_id: str | None, child: Job | None = None
    ) -> None:
        if parent_id is None:
            return
        parent = await db.get(Job, parent_id)
        if parent is None:
            return
        statuses = (
            (await db.execute(select(Job.status).where(Job.parent_id == parent_id))).scalars().all()
        )
        total = len(statuses)
        completed = sum(status in TERMINAL for status in statuses)
        failed = sum(
            status in {"failed", "dead_letter", "interrupted", "cancelled"} for status in statuses
        )
        parent.progress = {
            "children_total": total,
            "children_completed": completed,
            "children_failed": failed,
        }
        detail = dict(parent.progress)
        if child is not None:
            # Name the child that just finished so a UI can move exactly that item.
            detail["subject_id"] = child.subject_id
            detail["subject_status"] = (
                child.result.get("status") if isinstance(child.result, dict) else None
            )
        if total and completed == total:
            if parent.cancel_requested or parent.status == "cancelling":
                parent.status = "cancelled"
            elif any(status == "interrupted" for status in statuses) and not any(
                status in {"failed", "dead_letter", "cancelled"} for status in statuses
            ):
                parent.status = "interrupted"
            else:
                parent.status = "failed" if failed else "succeeded"
            parent.finished_at = utcnow()
            summary = await self._child_result_summary(db, parent_id)
            detail["summary"] = summary
            # Persist the outcome on the parent so a client that loads the job
            # after completion (e.g. a page refresh) can show the summary.
            parent.result = {**parent.progress, "summary": summary}
            await self.emit(
                db, parent, state=parent.status, message="all child jobs terminal", detail=detail
            )
        else:
            if parent.cancel_requested and parent.status in {
                "waiting_external",
                "claimed",
                "running",
            }:
                parent.status = "cancelling"
            # Incremental progress on every child completion so SSE listeners get a
            # live bar and can react to each item finishing.  The parent's real
            # status stays non-terminal, so the stream's `done` sentinel is not sent.
            state = "cancelling" if parent.status == "cancelling" else "progress"
            message = "cancellation in progress" if state == "cancelling" else "child progress"
            await self.emit(
                db, parent, state=state, stage=parent.current_stage, message=message, detail=detail
            )

    async def _child_result_summary(self, db: AsyncSession, parent_id: str) -> dict[str, int]:
        """Domain-neutral tally of child ``result['status']`` values for a batch."""
        rows = (
            await db.execute(select(Job.status, Job.result).where(Job.parent_id == parent_id))
        ).all()
        summary: dict[str, int] = {}
        for job_status, result in rows:
            status = result.get("status") if isinstance(result, dict) else None
            if status is None and job_status in {
                "failed",
                "dead_letter",
                "interrupted",
                "cancelled",
            }:
                status = job_status
            if status:
                summary[status] = summary.get(status, 0) + 1
        return summary

    async def interrupt(
        self, db: AsyncSession, job: Job, attempt: JobAttempt, *, reason: str
    ) -> None:
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
        await self._update_parent(db, job.parent_id, child=job)
        await db.commit()

    async def request_cancel(self, db: AsyncSession, job: Job) -> Job:
        now = utcnow()
        job.cancel_requested = True
        children = (
            (
                await db.execute(
                    select(Job).where(Job.parent_id == job.id).order_by(Job.created_at, Job.id)
                )
            )
            .scalars()
            .all()
        )
        if children:
            job.status = "cancelling"
            for child in children:
                if child.status in TERMINAL:
                    continue
                if child.status in {
                    "planned",
                    "queued",
                    "waiting_resource",
                    "retry_scheduled",
                    "paused",
                }:
                    await self._cancel_before_execution(
                        db, child, now=now, message="cancelled by parent"
                    )
                else:
                    child.cancel_requested = True
                    await self._bridge_media_cancel(db, child)
                    await self.emit(db, child, state=child.status, message="cancellation requested")
            await self._update_parent(db, job.id)
        else:
            if job.status in {"planned", "queued", "waiting_resource", "retry_scheduled", "paused"}:
                await self._cancel_before_execution(
                    db, job, now=now, message="cancellation requested"
                )
            else:
                if job.status in ACTIVE:
                    job.status = "cancelling"
                await self._bridge_media_cancel(db, job)
                await self.emit(db, job, state=job.status, message="cancellation requested")
            await self._update_parent(db, job.parent_id, child=job)
        await db.commit()
        return job

    async def set_paused(self, db: AsyncSession, job: Job, paused: bool) -> Job:
        job.pause_requested = paused
        if paused and job.status in {"queued", "waiting_resource", "retry_scheduled"}:
            job.status = "paused"
        elif not paused and job.status == "paused":
            job.status = "queued"
            job.scheduled_at = utcnow()
        await self.emit(
            db, job, state=job.status, message="pause requested" if paused else "resumed"
        )
        await db.commit()
        return job

    async def _reconcile_active_parents(self, db: AsyncSession) -> None:
        parent_ids = (
            (
                await db.execute(
                    select(Job.id).where(
                        Job.status.in_(tuple(ACTIVE)),
                        Job.id.in_(select(Job.parent_id).where(Job.parent_id.is_not(None))),
                    )
                )
            )
            .scalars()
            .all()
        )
        for parent_id in parent_ids:
            statuses = (
                (await db.execute(select(Job.status).where(Job.parent_id == parent_id)))
                .scalars()
                .all()
            )
            if statuses and all(status in TERMINAL for status in statuses):
                await self._update_parent(db, parent_id)

    async def recover(self, db: AsyncSession) -> int:
        cutoff = utcnow() - timedelta(seconds=settings.JOB_LEASE_SECONDS)
        attempts = (
            await db.execute(
                select(JobAttempt, Job)
                .join(Job, Job.id == JobAttempt.job_id)
                .where(
                    JobAttempt.status.in_(("claimed", "running")), JobAttempt.heartbeat_at < cutoff
                )
                .with_for_update(skip_locked=True)
            )
        ).all()
        for attempt, job in attempts:
            now = utcnow()
            error = {"type": "Interrupted", "message": "worker lease expired"}
            await self._release(db, attempt.id)
            attempt.status = "interrupted"
            attempt.finished_at = now
            attempt.error = error
            job.error = error
            if job.cancel_requested:
                job.status = "cancelled"
                job.finished_at = now
            elif job.type in RETRYABLE and job.attempt_count < job.max_attempts:
                job.status = "retry_scheduled"
                job.scheduled_at = now
                job.finished_at = None
            else:
                job.status = "interrupted"
                job.finished_at = now
            await self._sync_media_job_status(db, job)
            await self.emit(
                db,
                job,
                state=job.status,
                message="worker lease expired",
                detail=error,
                attempt_id=attempt.id,
            )
            # Keep batch parents progressing even if a child died outside finish/fail.
            await self._update_parent(db, job.parent_id, child=job)
        await self._reconcile_active_parents(db)
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
