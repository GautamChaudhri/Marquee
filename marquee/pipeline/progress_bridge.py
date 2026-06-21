"""Bridge worker-thread pipeline progress onto the durable job event stream.

The pipeline stages run off the event loop via ``asyncio.to_thread`` and emit
sync ``ProgressEvent``s from that worker thread. The frontend, however, reads a
single source of truth — the durable job stream (``GET /api/jobs/{id}/events``)
plus the ``job.progress`` snapshot (``GET /api/jobs/{id}``). This bridge marshals
those thread-side events onto the loop and turns them into persisted
``JobEvent`` rows + a live ``job.progress`` snapshot, so both the single-movie
``poster_pipeline`` handler and the cross-movie ``poster_pipeline_batch`` handler
share one progress contract.

Writes are throttled: stage boundaries (``start``/``end``) and movie boundaries
always persist; the high-frequency in-stage ticks (e.g. per-poster OCR) persist
at most once per ``min_interval`` seconds so a long batch does not flood
``job_events``.

Usage (inside a handler, on the worker event loop)::

    async with JobProgressBridge(job_id) as bridge:
        await asyncio.to_thread(run_work, progress=bridge.callback)
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Protocol

from marquee.core.jobs import job_manager
from marquee.database import _get_session_factory
from marquee.models import Job

logger = logging.getLogger(__name__)

_SENTINEL = object()


class _SupportsToDict(Protocol):
    def to_dict(self) -> dict[str, Any]: ...


class JobProgressBridge:
    """Async context manager that drains ProgressEvents into JobEvents."""

    def __init__(self, job_id: str, *, min_interval: float = 0.4) -> None:
        self._job_id = job_id
        self._min_interval = min_interval
        self._loop = asyncio.get_event_loop()
        self._queue: asyncio.Queue = asyncio.Queue()
        self._drain_task: asyncio.Task | None = None
        self._last_write = 0.0

    # -- thread-side -------------------------------------------------------

    def callback(self, event: _SupportsToDict) -> None:
        """Thread-safe sink handed to the runner as its ``progress`` callback."""
        payload = event.to_dict()
        self._loop.call_soon_threadsafe(self._queue.put_nowait, payload)

    # -- lifecycle ---------------------------------------------------------

    async def __aenter__(self) -> JobProgressBridge:
        self._drain_task = asyncio.create_task(self._drain())
        return self

    async def __aexit__(self, *exc: object) -> None:
        self._queue.put_nowait(_SENTINEL)
        if self._drain_task is not None:
            try:
                await self._drain_task
            except Exception:  # noqa: BLE001 — progress must never fail the job
                logger.exception("progress bridge drain failed for job %s", self._job_id)

    # -- loop-side ---------------------------------------------------------

    async def _drain(self) -> None:
        while True:
            event = await self._queue.get()
            if event is _SENTINEL:
                return
            boundary = event.get("state") in ("start", "end")
            now = time.monotonic()
            if not boundary and (now - self._last_write) < self._min_interval:
                continue  # coalesce high-frequency in-stage ticks
            self._last_write = now
            await self._persist(event)

    async def _persist(self, event: dict[str, Any]) -> None:
        factory = _get_session_factory()
        try:
            async with factory() as db:
                job = await db.get(Job, self._job_id)
                if job is None:
                    return
                stage = event.get("stage")
                job.current_stage = stage
                job.progress = {k: v for k, v in event.items() if v is not None}
                await job_manager.emit(
                    db,
                    job,
                    state="progress",
                    stage=stage,
                    message=_message(event),
                    detail=event,
                    persist=True,
                )
                await db.commit()
        except Exception:  # noqa: BLE001 — never let progress break the run
            logger.debug("progress persist skipped for job %s", self._job_id, exc_info=True)


def _message(event: dict[str, Any]) -> str:
    stage = event.get("stage", "?")
    state = event.get("state", "?")
    title = event.get("title")
    if title:
        idx, total = event.get("movie_index"), event.get("movie_total")
        where = f" [{idx}/{total}]" if idx and total else ""
        return f"{stage} {state}{where} — {title}"
    done, total = event.get("done"), event.get("total")
    if done is not None and total is not None:
        return f"{stage} {state} ({done}/{total})"
    return f"{stage} {state}"
