"""Bounded canonical intent reconciliation through the public PgQueuer gateway."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import UTC, datetime

from sqlalchemy import or_, select

from marquee.config import settings
from marquee.core.jobs.event_service import job_event_writer
from marquee.core.jobs.pgqueuer_gateway import PgQueuerGateway, pgqueuer_gateway
from marquee.database import _get_session_factory
from marquee.models.job import Job, JobDispatch

logger = logging.getLogger(__name__)


class TransportIntentMonitor:
    def __init__(self, gateway: PgQueuerGateway = pgqueuer_gateway, *, batch_size: int = 25):
        if batch_size < 1 or batch_size > 100:
            raise ValueError("transport intent monitor batch is outside the bound")
        self.gateway = gateway
        self.batch_size = batch_size

    async def run_once(self) -> dict[str, int]:
        counts = {"cancelled": 0, "retried": 0, "attention": 0}
        factory = _get_session_factory()
        async with factory() as session, session.begin():
            jobs = list(
                await session.scalars(
                    select(Job)
                    .where(
                        Job.phase != "terminal",
                        Job.phase.in_(("queued", "running", "stopping")),
                        or_(Job.desired_state != "run", Job.pgq_job_id.is_(None)),
                    )
                    .order_by(Job.updated_at.nullsfirst(), Job.id)
                    .limit(self.batch_size)
                    .with_for_update(skip_locked=True)
                )
            )
            if not jobs:
                return counts
            statuses = await self.gateway.known_ticket_statuses(
                session, job_ids=[job.id for job in jobs]
            )
            for job in jobs:
                dispatch = await session.scalar(
                    select(JobDispatch)
                    .where(
                        JobDispatch.job_id == job.id,
                        JobDispatch.generation == job.dispatch_generation,
                    )
                    .with_for_update()
                )
                status = statuses.get(job.id)
                linked = bool(
                    dispatch is not None
                    and dispatch.disposition == "active"
                    and dispatch.pgq_job_id == job.pgq_job_id
                )
                if not linked or status is None:
                    job.attention = {
                        "code": "transport_link_attention",
                        "summary": "Current canonical transport linkage is missing or mismatched",
                    }
                    counts["attention"] += 1
                    continue
                if job.desired_state == "cancel" and status in {"queued", "picked"}:
                    await self.gateway.cancel_known_ticket(session, job_id=job.id)
                    counts["retried"] += 1
                    continue
                if job.desired_state == "cancel" and status == "cancelled":
                    if job.phase == "queued":
                        now = datetime.now(UTC)
                        job.phase = "terminal"
                        job.outcome = "cancelled"
                        job.terminal_at = now
                        assert dispatch is not None
                        dispatch.disposition = "cancelled"
                        dispatch.ended_at = now
                        await job_event_writer.append(
                            session,
                            job_id=job.id,
                            event_key="job.cancelled",
                            state="cancelled",
                            message="Committed cancellation reconciled with transport",
                        )
                        counts["cancelled"] += 1
                    else:
                        job.attention = {
                            "code": "physical_cancellation_pending",
                            "summary": "Transport is cancelled; physical attempt reconciliation remains",
                        }
                        counts["attention"] += 1
                    continue
                if job.desired_state == "pause":
                    job.attention = {
                        "code": "paused_transport_attention",
                        "summary": "Paused canonical intent requires command-side ticket reconciliation",
                    }
                    counts["attention"] += 1
        return counts


transport_intent_monitor = TransportIntentMonitor(
    batch_size=settings.JOB_INTENT_MONITOR_BATCH_SIZE
)


async def monitor_until_shutdown(shutdown: asyncio.Event, *, interval_seconds: float) -> None:
    while not shutdown.is_set():
        try:
            await transport_intent_monitor.run_once()
        except Exception:
            logger.exception("bounded transport-intent reconciliation failed")
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(shutdown.wait(), timeout=interval_seconds)
