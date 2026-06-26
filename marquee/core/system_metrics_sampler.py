"""Lightweight host-telemetry sampler — NOT part of the Job/JobEvent system.

Writes directly to ``system_metrics_samples`` on its own asyncio timer inside
the API process. Must never create a Job, JobAttempt, or JobEvent row — those
exist for durable, resumable, user-visible work; a 10-15s heartbeat would
flood the job history Projection Room lets the user inspect (thousands of
rows/day). A single ``asyncio.create_task`` loop is enough here: the sampler
does no GPU/ffmpeg work, just a handful of psutil/NVML calls plus one INSERT
per tick, so it doesn't need the WorkerSupervisor's subprocess machinery.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging

from sqlalchemy import select

from marquee.config import settings
from marquee.core import system_metrics
from marquee.core.jobs.manager import ACTIVE
from marquee.database import _get_session_factory
from marquee.models import Job, SystemMetricsSample

logger = logging.getLogger(__name__)


class SystemMetricsSampler:
    """Supervises the single background task that samples host telemetry."""

    def __init__(self) -> None:
        self._task: asyncio.Task | None = None

    async def _tick(self) -> None:
        data = system_metrics.collect(settings.metrics_disk_path)
        factory = _get_session_factory()
        async with factory() as db:
            active = (
                await db.execute(select(Job.id, Job.type).where(Job.status.in_(ACTIVE)))
            ).all()
            db.add(
                SystemMetricsSample(
                    cpu=data["cpu"],
                    gpu=data["gpu"],
                    ram=data["ram"],
                    disk=data["disk"],
                    net=data["net"],
                    active_jobs=[{"id": row.id, "type": row.type} for row in active],
                )
            )
            await db.commit()

    async def _run(self) -> None:
        while True:
            try:
                await self._tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("system metrics sample failed — will retry next tick")
            await asyncio.sleep(settings.METRICS_SAMPLE_INTERVAL_SECONDS)

    async def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name="system-metrics-sampler")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task
        self._task = None
