"""Narrow public-API bridge between canonical jobs and PgQueuer 1.1.1."""

from __future__ import annotations

import json
import threading
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

import asyncpg
from pgqueuer import Queries
from pgqueuer.errors import DuplicateJobError
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from marquee.models.job import Job, JobDispatch, JobEvent

ENTRYPOINT_CONTROL = "control"
PAYLOAD_VERSION = 1
MIN_PRIORITY = 0
MAX_PRIORITY = 100
MAX_DEFER = timedelta(days=365)
MAX_STATUS_IDS = 100


class PgQueuerGatewayError(RuntimeError):
    """Raised when the supported gateway preconditions are not met."""


class PgQueuerInvariantError(PgQueuerGatewayError):
    """Raised when canonical and transport identity disagree."""


class PgQueuerGateway:
    """Use PgQueuer only through public Queries on the caller transaction."""

    def __init__(self) -> None:
        self._active_raw_connections: set[int] = set()
        self._active_lock = threading.Lock()

    @staticmethod
    def _validate_common(
        *,
        job_id: str,
        entrypoint: str,
        payload_version: int,
        dispatch_generation: int,
        priority: int,
        execute_after: timedelta | None,
        dedupe_key: str,
    ) -> None:
        if not job_id or len(job_id) > 32:
            raise PgQueuerGatewayError("job_id must be a non-empty canonical string")
        if entrypoint != ENTRYPOINT_CONTROL:
            raise PgQueuerGatewayError("only the control entrypoint is enabled in JMC1")
        if payload_version != PAYLOAD_VERSION:
            raise PgQueuerGatewayError("unsupported JMC1 payload version")
        if dispatch_generation < 1:
            raise PgQueuerGatewayError("dispatch_generation must be at least one")
        if not MIN_PRIORITY <= priority <= MAX_PRIORITY:
            raise PgQueuerGatewayError(
                f"priority must be between {MIN_PRIORITY} and {MAX_PRIORITY}"
            )
        if execute_after is not None and not timedelta(0) <= execute_after <= MAX_DEFER:
            raise PgQueuerGatewayError("execute_after must be between zero and 365 days")
        expected_dedupe = f"marquee:{job_id}:{dispatch_generation}"
        if dedupe_key != expected_dedupe:
            raise PgQueuerGatewayError(f"dedupe_key must equal {expected_dedupe!r}")

    @staticmethod
    def _transport_payload(
        *,
        job_id: str,
        payload_version: int,
        dispatch_generation: int,
    ) -> bytes:
        return json.dumps(
            {
                "dispatch_generation": dispatch_generation,
                "job_id": job_id,
                "payload_version": payload_version,
            },
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")

    @asynccontextmanager
    async def _queries(self, session: AsyncSession) -> AsyncIterator[Queries]:
        if not session.in_transaction():
            raise PgQueuerGatewayError("caller must own an active SQLAlchemy transaction")
        connection = await session.connection()
        if connection.dialect.name != "postgresql" or connection.dialect.driver != "asyncpg":
            raise PgQueuerGatewayError("PgQueuer gateway requires PostgreSQL with asyncpg")

        raw_connection = await connection.get_raw_connection()
        driver_connection = raw_connection.driver_connection
        if not isinstance(driver_connection, asyncpg.Connection):
            raise PgQueuerGatewayError("SQLAlchemy driver connection is not asyncpg")
        if driver_connection.is_closed():
            raise PgQueuerGatewayError("SQLAlchemy asyncpg driver connection is closed")

        # SQLAlchemy transactions begin lazily. Use its connection after
        # obtaining the documented raw handle but before invoking PgQueuer so
        # the underlying asyncpg connection is demonstrably enlisted.
        await connection.execute(text("SELECT 1"))
        if not driver_connection.is_in_transaction():
            raise PgQueuerGatewayError("asyncpg driver is not in the caller transaction")

        key = id(driver_connection)
        with self._active_lock:
            if key in self._active_raw_connections:
                raise PgQueuerGatewayError(
                    "concurrent PgQueuer use of one SQLAlchemy connection is prohibited"
                )
            self._active_raw_connections.add(key)
        try:
            yield Queries.from_asyncpg_connection(driver_connection)
        finally:
            with self._active_lock:
                self._active_raw_connections.discard(key)

    async def enqueue(
        self,
        session: AsyncSession,
        *,
        job_id: str,
        entrypoint: str,
        payload_version: int,
        dispatch_generation: int,
        priority: int,
        execute_after: timedelta | None,
        dedupe_key: str,
    ) -> int:
        """Insert and link one ticket without committing or closing anything."""
        self._validate_common(
            job_id=job_id,
            entrypoint=entrypoint,
            payload_version=payload_version,
            dispatch_generation=dispatch_generation,
            priority=priority,
            execute_after=execute_after,
            dedupe_key=dedupe_key,
        )
        if not session.in_transaction():
            raise PgQueuerGatewayError("caller must own an active SQLAlchemy transaction")

        await session.flush()
        job = await session.get(Job, job_id)
        dispatch = await session.scalar(
            select(JobDispatch).where(
                JobDispatch.job_id == job_id,
                JobDispatch.generation == dispatch_generation,
            )
        )
        if job is None or dispatch is None:
            raise PgQueuerInvariantError("canonical job and dispatch must exist before enqueue")
        if job.type != "system_noop":
            raise PgQueuerGatewayError("only system_noop is enabled in JMC1")
        if job.phase != "queued" or job.dispatch_generation != dispatch_generation:
            raise PgQueuerInvariantError("canonical job phase/generation does not match dispatch")
        if job.pgq_job_id is not None or dispatch.pgq_job_id is not None:
            raise PgQueuerInvariantError("dispatch is already linked to a PgQueuer ticket")
        if (
            dispatch.entrypoint != entrypoint
            or dispatch.dedupe_key != dedupe_key
            or dispatch.priority != priority
        ):
            raise PgQueuerInvariantError("dispatch audit does not match enqueue request")
        event_count = await session.scalar(
            select(func.count(JobEvent.id)).where(
                JobEvent.job_id == job_id,
                JobEvent.state == "queued",
            )
        )
        if not event_count:
            raise PgQueuerInvariantError("initial queued event must exist before enqueue")

        payload = self._transport_payload(
            job_id=job_id,
            payload_version=payload_version,
            dispatch_generation=dispatch_generation,
        )
        try:
            async with self._queries(session) as queries:
                ids = await queries.enqueue(
                    entrypoint,
                    payload,
                    priority=priority,
                    execute_after=execute_after,
                    dedupe_key=dedupe_key,
                )
        except DuplicateJobError as exc:
            raise PgQueuerInvariantError(
                "PgQueuer dedupe conflict for a canonical dispatch generation"
            ) from exc
        if len(ids) != 1 or not isinstance(ids[0], int):
            raise PgQueuerInvariantError("PgQueuer enqueue must return exactly one numeric ID")

        ticket_id = int(ids[0])
        job.pgq_job_id = ticket_id
        dispatch.pgq_job_id = ticket_id
        await session.flush()
        return ticket_id

    async def cancel_known_ticket(self, session: AsyncSession, *, job_id: str) -> None:
        """Atomically request canonical cancellation through the known current ticket."""
        job = await session.scalar(select(Job).where(Job.id == job_id).with_for_update())
        if job is None or job.pgq_job_id is None:
            raise PgQueuerGatewayError("canonical job has no current PgQueuer ticket")
        if job.phase == "terminal":
            return
        dispatch = await session.scalar(
            select(JobDispatch)
            .where(
                JobDispatch.job_id == job.id,
                JobDispatch.generation == job.dispatch_generation,
            )
            .with_for_update()
        )
        if dispatch is None or dispatch.pgq_job_id != job.pgq_job_id:
            raise PgQueuerInvariantError("canonical current dispatch is missing or mismatched")

        async with self._queries(session) as queries:
            rows = await queries.job_status([job.pgq_job_id])
            if len(rows) != 1 or int(rows[0][0]) != job.pgq_job_id:
                raise PgQueuerInvariantError("known PgQueuer ticket status is unavailable")
            transport_status = rows[0][1]
            await queries.mark_job_as_cancelled([job.pgq_job_id])

        now = datetime.now(UTC)
        job.desired_state = "cancel"
        if transport_status == "queued":
            job.phase = "terminal"
            job.outcome = "cancelled"
            job.terminal_at = now
            job.status = "cancelled"
            job.finished_at = now
            dispatch.disposition = "cancelled"
            dispatch.ended_at = now
            session.add(
                JobEvent(
                    job_id=job.id,
                    state="cancelled",
                    message="system_noop cancelled while queued",
                )
            )
        elif transport_status == "picked":
            job.phase = "stopping"
            job.stopping_at = now
            job.status = "stopping"
            session.add(
                JobEvent(
                    job_id=job.id,
                    state="stopping",
                    message="system_noop cancellation requested",
                )
            )
        else:
            raise PgQueuerInvariantError(
                f"transport status {transport_status!r} cannot accept cancellation"
            )

    async def known_ticket_statuses(
        self,
        session: AsyncSession,
        *,
        job_ids: Sequence[str],
    ) -> dict[str, str]:
        """Return statuses only for bounded canonical IDs supplied by the caller."""
        if not 1 <= len(job_ids) <= MAX_STATUS_IDS:
            raise PgQueuerGatewayError(f"job_ids must contain 1..{MAX_STATUS_IDS} items")
        jobs = (
            (
                await session.execute(
                    select(Job).where(Job.id.in_(set(job_ids)), Job.pgq_job_id.is_not(None))
                )
            )
            .scalars()
            .all()
        )
        tickets = [job.pgq_job_id for job in jobs if job.pgq_job_id is not None]
        if not tickets:
            return {}
        async with self._queries(session) as queries:
            rows = await queries.job_status(tickets)
        by_ticket = {int(ticket): status for ticket, status in rows}
        return {
            job.id: by_ticket[job.pgq_job_id]
            for job in jobs
            if job.pgq_job_id in by_ticket
        }

    async def queue_statistics(self, session: AsyncSession) -> list[dict]:
        """Return PgQueuer's bounded aggregate statistics, never raw rows."""
        async with self._queries(session) as queries:
            rows = await queries.queue_size()
        return [row.model_dump(mode="json") for row in rows]


pgqueuer_gateway = PgQueuerGateway()
