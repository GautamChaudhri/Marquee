"""MediaJobManager — durable operation record, SSE stream, and batch tracking.

Execution lives in the generic job platform (``marquee.core.jobs``); this
module keeps the detailed ``MediaJob``/``MediaJobEvent`` audit record and the
live SSE fan-out that ``marquee.core.jobs.legacy_media`` mirrors progress into
as the generic ``Job`` runs. See ``marquee/core/jobs/legacy_media.py`` for the
bridge that actually executes a ``MediaJob``'s operation.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

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

    # ------------------------------------------------------------------
    # Events / SSE
    # ------------------------------------------------------------------

    def stream(self, job_id: str) -> JobStream:
        return self._streams.setdefault(job_id, JobStream())

    async def emit(
        self,
        db: AsyncSession | None,
        job_id: str,
        stage: str,
        state: str,
        *,
        message: str | None = None,
        progress: dict | None = None,
        persist: bool = True,
        commit: bool = True,
    ) -> dict:
        """Publish a job event to live SSE subscribers, optionally persisting it.

        ``persist=False`` publishes to the in-memory stream only (no DB write,
        ``db`` may be None) — used for high-frequency encode progress ticks so
        the live bar stays smooth without one ``media_job_events`` row (and
        write-lock acquisition) per ffmpeg frame. Stage/state transitions and
        terminal events persist so a reconnecting client can replay the
        meaningful history cheaply.

        ``commit=False`` flushes the event into the caller's open transaction
        instead of committing, and skips the stream publish — the caller
        commits once and publishes the returned payload afterwards. Used by the
        legacy_media bridge to fold this write and the generic-job mirror into
        a single transaction (one commit per progress tick instead of two).
        """
        event_id: int | None = None
        if persist:
            event = MediaJobEvent(
                job_id=job_id,
                stage=stage,
                state=state,
                message=message,
                progress_json=json.dumps(progress) if progress else None,
            )
            db.add(event)

            # Update parent MediaJob's current stage and progress statistics
            job = await db.get(MediaJob, job_id)
            if job is not None:
                job.stage = stage
                if progress and "percent" in progress:
                    job.progress_done = int(progress["percent"])
                    job.progress_total = 100
                elif progress and "progress" in progress:
                    job.progress_done = int(progress["progress"])
                    job.progress_total = 100

            await db.flush()
            event_id = event.id
            if commit:
                await db.commit()
        payload = {
            "id": event_id,
            "job_id": job_id,
            "stage": stage,
            "state": state,
            "message": message,
            "progress": progress,
        }
        if commit or not persist:
            self.stream(job_id).publish(payload)
        return payload

    async def persisted_events(self, db: AsyncSession, job_id: str) -> list[dict]:
        rows = (
            (
                await db.execute(
                    select(MediaJobEvent)
                    .where(MediaJobEvent.job_id == job_id)
                    .order_by(MediaJobEvent.id)
                )
            )
            .scalars()
            .all()
        )
        return [
            {
                "id": r.id,
                "job_id": job_id,
                "stage": r.stage,
                "state": r.state,
                "message": r.message,
                "progress": json.loads(r.progress_json) if r.progress_json else None,
            }
            for r in rows
        ]

    # ------------------------------------------------------------------
    # Job creation / control
    # ------------------------------------------------------------------

    async def create_job(
        self,
        db: AsyncSession,
        *,
        operation: str,
        media_file_id: int | None,
        trigger: str = "manual",
        request: dict | None = None,
        plan: dict | None = None,
        status: str = "planned",
        input_signature: str | None = None,
        plan_expires_at: datetime | None = None,
        idempotency_key: str | None = None,
        batch_id: str | None = None,
        commit: bool = True,
    ) -> MediaJob:
        if idempotency_key:
            existing = (
                await db.execute(
                    select(MediaJob).where(MediaJob.idempotency_key == idempotency_key)
                )
            ).scalar_one_or_none()
            if existing is not None:
                if existing.operation != operation:
                    raise ValueError(
                        f"idempotency_key {idempotency_key!r} already used by media "
                        f"job {existing.job_id!r} of operation {existing.operation!r}; "
                        f"refusing to return it for a {operation!r} request"
                    )
                return existing
        job = MediaJob(
            job_id=uuid4().hex,
            operation=operation,
            media_file_id=media_file_id,
            trigger=trigger,
            status=status,
            request_json=json.dumps(request) if request else None,
            plan_json=json.dumps(plan) if plan else None,
            input_signature=input_signature,
            plan_expires_at=plan_expires_at,
            idempotency_key=idempotency_key,
            batch_id=batch_id,
        )
        db.add(job)
        if commit:
            await db.commit()
            await db.refresh(job)
        else:
            await db.flush()
        # The legacy row remains the detailed operation record for now; the
        # generic Job owns scheduling, resource admission, and worker leases.
        from marquee.core.jobs import job_manager  # noqa: PLC0415

        resources = {f"media-file:{media_file_id}": 1} if media_file_id is not None else {}
        if operation == "subtitle_generate":
            resources["gpu"] = 1
        elif operation in {
            "audio_remove",
            "subtitle_remove",
            "subtitle_embed",
            "subtitle_metadata",
            "audio_reorder",
            "subtitle_restore",
            "track_remove",
            "letterbox_reencode",
        }:
            resources["media_write"] = 1
        else:
            resources["media_read"] = 1
        if operation == "letterbox_reencode" and plan:
            family = plan.get("encoder", {}).get("family")
            if family and family != "cpu":
                resources["gpu"] = 1
                resources["transcode"] = 1
        await job_manager.create(
            db,
            job_type=operation,
            payload={"media_job_id": job.job_id},
            priority=80 if trigger in {"manual", "webhook"} else 30,
            resources=resources,
            subject_type="media_file" if media_file_id is not None else None,
            subject_id=media_file_id,
            idempotency_key=f"generic:{idempotency_key}" if idempotency_key else None,
            status="planned" if status == "planned" else "queued",
            max_attempts=1
            if operation
            in {
                "audio_remove",
                "subtitle_remove",
                "subtitle_embed",
                "subtitle_metadata",
                "audio_reorder",
                "subtitle_restore",
                "track_remove",
                "letterbox_reencode",
            }
            else 3,
            commit=commit,
        )
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

    @staticmethod
    def _selection_key(request: dict | None) -> tuple | None:
        """Normalize what a plan targets, for supersede comparisons.

        Returns None when the request carries no selection at all (scan-style
        operations), in which case any older plan of the same operation is a
        duplicate.
        """
        if not isinstance(request, dict):
            return None
        track_ids = request.get("track_ids") or []
        audio = request.get("audio_stream_indices") or []
        edits = request.get("edits") or []
        order = request.get("audio_stream_order") or []
        if not (track_ids or audio or edits or order):
            return None
        return (
            tuple(sorted(str(t) for t in track_ids)),
            tuple(sorted(int(i) for i in audio)),
            json.dumps(edits, sort_keys=True, default=str),
            tuple(int(i) for i in order),
        )

    async def supersede_planned_media_jobs(
        self,
        db: AsyncSession,
        *,
        media_file_id: int,
        operation: str,
        request: dict | None = None,
        message: str = "superseded by a newer plan",
    ) -> list[str]:
        """Cancel older *planned* jobs this new plan replaces.

        When ``request`` is given, only plans targeting the same selection
        (same track ids / audio indices / edits) are superseded — two plans
        for the same file removing *different* languages are siblings, not
        duplicates, and cancelling one silently dropped the user's other
        language selection.
        """
        from marquee.core.jobs import job_manager  # noqa: PLC0415
        from marquee.models import Job  # noqa: PLC0415

        media_jobs = (
            (
                await db.execute(
                    select(MediaJob)
                    .where(
                        MediaJob.operation == operation,
                        MediaJob.media_file_id == media_file_id,
                        MediaJob.status == "planned",
                    )
                    .order_by(MediaJob.created_at.asc(), MediaJob.job_id.asc())
                )
            )
            .scalars()
            .all()
        )
        new_key = self._selection_key(request)
        if new_key is not None:

            def _same_selection(mj: MediaJob) -> bool:
                try:
                    old_request = json.loads(mj.request_json) if mj.request_json else None
                except (TypeError, json.JSONDecodeError):
                    return True  # unreadable request — treat as duplicate
                return self._selection_key(old_request) == new_key

            media_jobs = [mj for mj in media_jobs if _same_selection(mj)]
        if not media_jobs:
            return []

        generic_jobs = (
            (
                await db.execute(
                    select(Job).where(
                        Job.type == operation,
                        Job.subject_type == "media_file",
                        Job.subject_id == str(media_file_id),
                        Job.status == "planned",
                    )
                )
            )
            .scalars()
            .all()
        )
        generic_by_media_id = {
            row.payload.get("media_job_id"): row
            for row in generic_jobs
            if isinstance(row.payload, dict) and row.payload.get("media_job_id")
        }

        now = datetime.now(UTC)
        cancelled_ids: list[str] = []
        for media_job in media_jobs:
            media_job.status = "cancelled"
            media_job.cancel_requested = True
            cancelled_ids.append(media_job.job_id)
            generic = generic_by_media_id.get(media_job.job_id)
            if generic is not None:
                await job_manager._cancel_before_execution(
                    db,
                    generic,
                    now=now,
                    message=message,
                )

        await db.commit()
        return cancelled_ids

    # ------------------------------------------------------------------
    # Batch progress
    # ------------------------------------------------------------------

    async def update_batch_progress(self, db: AsyncSession, batch_id: str) -> None:
        """Tally child MediaJob statuses onto their parent MediaBatch row.

        Called by the legacy_media bridge handler whenever a bridged MediaJob
        with a batch_id reaches a terminal state.
        """
        from marquee.models import MediaBatch  # noqa: PLC0415

        batch = await db.get(MediaBatch, batch_id)
        if batch is None:
            return
        rows = (
            (await db.execute(select(MediaJob.status).where(MediaJob.batch_id == batch_id)))
            .scalars()
            .all()
        )
        batch.completed_count = sum(1 for s in rows if s == "succeeded")
        batch.failed_count = sum(1 for s in rows if s == "failed")
        terminal = {"succeeded", "failed", "cancelled", "interrupted"}
        if all(s in terminal for s in rows):
            batch.status = "completed"
        batch.updated_at = datetime.now(UTC)
        await db.commit()


media_job_manager = MediaJobManager()
