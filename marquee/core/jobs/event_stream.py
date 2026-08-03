"""One durable job-event tailer and bounded fan-out per API instance."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from types import SimpleNamespace
from typing import Any, Literal, Protocol

import asyncpg
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select

from marquee.core.jobs.event_service import JOB_EVENT_CHANNEL
from marquee.core.runtime_settings import effective_settings as settings
from marquee.database import _get_session_factory
from marquee.models.job import JobEvent

logger = logging.getLogger(__name__)


class EventReconciliation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    snapshot_url: str
    presentation_url: str


class JobEventDelta(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    state: str
    stage: str | None = None
    message: str | None = None
    detail: dict[str, Any] = Field(default_factory=dict)


class JobEventFrame(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal[1] = 1
    cursor: int = Field(ge=0)
    event_key: str
    job_id: str | None
    attempt_id: int | None = None
    canonical_version: int = Field(ge=0)
    occurred_at: datetime | None = None
    delta: JobEventDelta
    reconciliation: EventReconciliation | None


_PUBLIC_DETAIL_KEYS: dict[str, frozenset[str]] = {
    "attempt.started": frozenset({"dispatch_generation"}),
    "attempt.interrupted": frozenset({"code", "summary"}),
    "job.priority_changed": frozenset({"dispatch_generation", "execution_class", "priority"}),
    "job.queued": frozenset({"dispatch_generation"}),
    "job.retried": frozenset({"original_job_id"}),
    "job.retry_requested": frozenset({"code", "summary"}),
    "progress.updated": frozenset({"progress_sequence"}),
    "work_items.updated": frozenset({"work_item_sequence", "work_item_total"}),
    "log.available": frozenset({"attempt_id", "truncated"}),
    "log.truncated": frozenset({"attempt_id"}),
    "artifact.available": frozenset({"artifact_id", "kind", "name"}),
    "artifact.failed": frozenset({"artifact_id", "kind", "name"}),
    "artifact.expired": frozenset({"artifact_id", "kind", "name"}),
}


def _public_detail(event_key: str, detail: dict[str, Any] | None) -> dict[str, Any]:
    allowed = _PUBLIC_DETAIL_KEYS.get(event_key, frozenset())
    return {key: value for key, value in (detail or {}).items() if key in allowed}


class _EventRecord(Protocol):
    id: int
    job_id: str
    attempt_id: int | None
    event_key: str
    state: str
    stage: str | None
    message: str | None
    detail: dict[str, Any] | None
    created_at: datetime


def frame_from_event(event: _EventRecord) -> JobEventFrame:
    detail = event.detail or {}
    canonical_version = detail.get("_canonical_version", 0)
    if isinstance(canonical_version, bool) or not isinstance(canonical_version, int):
        canonical_version = 0
    return JobEventFrame(
        cursor=event.id,
        event_key=event.event_key,
        job_id=event.job_id,
        attempt_id=event.attempt_id,
        canonical_version=canonical_version,
        occurred_at=event.created_at,
        delta=JobEventDelta(
            state=event.state,
            stage=event.stage,
            message=event.message,
            detail=_public_detail(event.event_key, detail),
        ),
        reconciliation=EventReconciliation(
            snapshot_url=f"/api/jobs/{event.job_id}/snapshot",
            presentation_url=f"/api/jobs/{event.job_id}/presentation",
        ),
    )


def reset_frame(high_water: int) -> JobEventFrame:
    return JobEventFrame(
        cursor=high_water,
        event_key="stream.reset_required",
        job_id=None,
        canonical_version=0,
        delta=JobEventDelta(
            state="reconcile",
            message="The live event cursor must be reconciled from bounded snapshots.",
            detail={"high_water": high_water},
        ),
        reconciliation=None,
    )


@dataclass(eq=False, slots=True)
class EventClient:
    after: int
    authorization_scope: Literal["all_jobs"] = "all_jobs"
    job_ids: frozenset[str] = frozenset()
    queue: asyncio.Queue[JobEventFrame] = field(
        default_factory=lambda: asyncio.Queue(maxsize=settings.JOB_EVENT_CLIENT_QUEUE_SIZE)
    )
    closed: bool = False


class JobEventTailer:
    """Consume each committed cursor range once and isolate slow clients."""

    def __init__(self) -> None:
        self._connection: asyncpg.Connection | None = None
        self._task: asyncio.Task[None] | None = None
        self._wake = asyncio.Event()
        self._fanout_lock = asyncio.Lock()
        self._clients: set[EventClient] = set()
        self._cursor = 0
        self._announced_cursor = 0
        self._last_error: str | None = None
        self._metrics = {
            "listener_repairs": 0,
            "notifications": 0,
            "invalid_notifications": 0,
            "client_overflows": 0,
            "frames_fanned_out": 0,
        }

    @property
    def cursor(self) -> int:
        return self._cursor

    def health(self) -> dict[str, Any]:
        connected = self._connection is not None and not self._connection.is_closed()
        running = self._task is not None and not self._task.done()
        return {
            "status": "ok" if connected and running else "unavailable",
            "connected": connected,
            "running": running,
            "cursor": self._cursor,
            "event_lag": max(0, self._announced_cursor - self._cursor),
            "clients": len(self._clients),
            "last_error": self._last_error,
            **self._metrics,
        }

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        await self._connect(initialize_cursor=True)
        self._task = asyncio.create_task(self._run(), name="job-event-tailer")

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        connection, self._connection = self._connection, None
        if connection is not None and not connection.is_closed():
            with contextlib.suppress(Exception):
                await connection.remove_listener(JOB_EVENT_CHANNEL, self._notify)
            await connection.close()
        async with self._fanout_lock:
            for client in self._clients:
                client.closed = True
            self._clients.clear()

    async def subscribe(self, after: int | None, *, invalid_cursor: bool = False) -> EventClient:
        factory = _get_session_factory()
        async with self._fanout_lock, factory() as session:
            low, high = (
                await session.execute(select(func.min(JobEvent.id), func.max(JobEvent.id)))
            ).one()
            high = int(high or 0)
            if after is None:
                after = high
            client = EventClient(after=after)
            self._clients.add(client)
            gap = high - after
            reset = (
                invalid_cursor
                or after < 0
                or after > high
                or (low is not None and after < int(low) - 1)
                or gap > settings.JOB_EVENT_REPLAY_LIMIT
            )
            if reset:
                self._reset_client(client, high)
                return client
            if gap:
                events = (
                    await session.scalars(
                        select(JobEvent)
                        .where(JobEvent.id > after, JobEvent.id <= high)
                        .order_by(JobEvent.id)
                        .limit(settings.JOB_EVENT_REPLAY_LIMIT)
                    )
                ).all()
                for event in events:
                    client.queue.put_nowait(frame_from_event(event))
                    client.after = event.id
            return client

    async def unsubscribe(self, client: EventClient) -> None:
        async with self._fanout_lock:
            client.closed = True
            self._clients.discard(client)

    async def _connect(self, *, initialize_cursor: bool) -> None:
        dsn = settings.db_url_resolved.replace("postgresql+asyncpg://", "postgresql://", 1)
        connection = await asyncpg.connect(
            dsn,
            server_settings={"application_name": "marquee:api:event-listener"},
        )
        await connection.add_listener(JOB_EVENT_CHANNEL, self._notify)
        if initialize_cursor:
            self._cursor = int(
                await connection.fetchval("SELECT COALESCE(MAX(id), 0) FROM job_events")
            )
            self._announced_cursor = self._cursor
        self._connection = connection
        self._last_error = None

    def _notify(
        self,
        connection: asyncpg.Connection,
        pid: int,
        channel: str,
        payload: str,
    ) -> None:
        del connection, pid, channel
        self._metrics["notifications"] += 1
        try:
            cursor = int(payload, 10)
        except ValueError:
            self._metrics["invalid_notifications"] += 1
        else:
            if cursor >= 0:
                self._announced_cursor = max(self._announced_cursor, cursor)
            else:
                self._metrics["invalid_notifications"] += 1
        self._wake.set()

    async def _run(self) -> None:
        while True:
            try:
                try:
                    await asyncio.wait_for(
                        self._wake.wait(), timeout=settings.JOB_EVENT_REPAIR_SECONDS
                    )
                except TimeoutError:
                    self._metrics["listener_repairs"] += 1
                self._wake.clear()
                await self._repair()
            except asyncio.CancelledError:
                raise
            except (OSError, RuntimeError, asyncpg.PostgresError) as exc:
                self._last_error = type(exc).__name__
                logger.warning("Job event listener degraded; reconnecting", exc_info=True)
                connection, self._connection = self._connection, None
                if connection is not None and not connection.is_closed():
                    with contextlib.suppress(Exception):
                        await connection.close()
                await asyncio.sleep(settings.JOB_EVENT_RECONNECT_SECONDS)
                try:
                    await self._connect(initialize_cursor=False)
                except (OSError, RuntimeError, asyncpg.PostgresError) as reconnect_exc:
                    self._last_error = type(reconnect_exc).__name__

    async def _repair(self) -> None:
        connection = self._connection
        if connection is None or connection.is_closed():
            raise RuntimeError("job event listener is disconnected")
        while True:
            rows = await connection.fetch(
                """
                SELECT id, job_id, attempt_id, event_key, state, stage, message, detail, created_at
                FROM job_events WHERE id > $1 ORDER BY id LIMIT $2
                """,
                self._cursor,
                settings.JOB_EVENT_TAIL_BATCH_SIZE,
            )
            if not rows:
                return
            for row in rows:
                values = dict(row)
                if isinstance(values.get("detail"), str):
                    values["detail"] = json.loads(values["detail"])
                event = SimpleNamespace(**values)
                await self._fanout(frame_from_event(event))
                self._cursor = event.id
            if len(rows) < settings.JOB_EVENT_TAIL_BATCH_SIZE:
                return

    async def _fanout(self, frame: JobEventFrame) -> None:
        async with self._fanout_lock:
            for client in tuple(self._clients):
                if client.closed or frame.cursor <= client.after:
                    continue
                if client.queue.full():
                    self._metrics["client_overflows"] += 1
                    self._reset_client(client, frame.cursor)
                    continue
                client.queue.put_nowait(frame)
                client.after = frame.cursor
                self._metrics["frames_fanned_out"] += 1

    def _reset_client(self, client: EventClient, high_water: int) -> None:
        while not client.queue.empty():
            client.queue.get_nowait()
        client.queue.put_nowait(reset_frame(high_water))
        client.after = high_water
        client.closed = True


job_event_tailer = JobEventTailer()
