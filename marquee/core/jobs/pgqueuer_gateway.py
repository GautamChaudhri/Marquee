"""Narrow public-API bridge between canonical jobs and PgQueuer 1.1.1."""

from __future__ import annotations

import json
import threading
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import asyncpg
from pgqueuer import Queries
from pgqueuer.errors import DuplicateJobError
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from marquee.core.jobs.definitions import JobDefinitionError
from marquee.core.jobs.event_service import job_event_writer
from marquee.core.jobs.manifest import JOB_DEFINITION_REGISTRY
from marquee.models.job import Job, JobDispatch, JobEvent

ENTRYPOINT_CONTROL = "control"
# Registered worker execution entrypoints that may receive a ticket. Parent-only jobs never
# receive a ticket at all.
ENQUEUEABLE_ENTRYPOINTS = frozenset(
    {"control", "network", "cpu", "media_read", "media_write", "gpu", "maintenance"}
)
PAYLOAD_VERSION = 1
MIN_PRIORITY = 0
MAX_PRIORITY = 100
MAX_DEFER = timedelta(days=365)
MAX_STATUS_IDS = 100
MAX_BULK_ENQUEUE = 500


@dataclass(frozen=True, slots=True)
class EnqueueIntent:
    """One already-persisted canonical dispatch awaiting a transport ticket."""

    job_id: str
    entrypoint: str
    payload_version: int
    dispatch_generation: int
    priority: int
    execute_after: timedelta | None
    dedupe_key: str


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
        if entrypoint not in ENQUEUEABLE_ENTRYPOINTS:
            raise PgQueuerGatewayError(
                f"entrypoint {entrypoint!r} is not an enqueueable execution class"
            )
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
        try:
            JOB_DEFINITION_REGISTRY.for_dispatch(job.type, entrypoint=entrypoint)
        except JobDefinitionError as exc:
            raise PgQueuerGatewayError(str(exc)) from exc
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

    async def enqueue_many(
        self,
        session: AsyncSession,
        *,
        intents: Sequence[EnqueueIntent],
    ) -> tuple[int, ...]:
        """Insert and link one bounded ordered ticket set in the caller transaction."""
        ordered = tuple(intents)
        if not 1 <= len(ordered) <= MAX_BULK_ENQUEUE:
            raise PgQueuerGatewayError(
                f"bulk enqueue requires 1..{MAX_BULK_ENQUEUE} canonical dispatches"
            )
        job_ids = [intent.job_id for intent in ordered]
        dedupe_keys = [intent.dedupe_key for intent in ordered]
        if len(set(job_ids)) != len(job_ids):
            raise PgQueuerGatewayError("bulk enqueue contains duplicate canonical job IDs")
        if len(set(dedupe_keys)) != len(dedupe_keys):
            raise PgQueuerGatewayError("bulk enqueue contains duplicate transport dedupe keys")
        for intent in ordered:
            self._validate_common(
                job_id=intent.job_id,
                entrypoint=intent.entrypoint,
                payload_version=intent.payload_version,
                dispatch_generation=intent.dispatch_generation,
                priority=intent.priority,
                execute_after=intent.execute_after,
                dedupe_key=intent.dedupe_key,
            )
        if not session.in_transaction():
            raise PgQueuerGatewayError("caller must own an active SQLAlchemy transaction")

        await session.flush()
        jobs = {
            job.id: job
            for job in (
                await session.scalars(select(Job).where(Job.id.in_(job_ids)))
            ).all()
        }
        dispatches = {
            (dispatch.job_id, dispatch.generation): dispatch
            for dispatch in (
                await session.scalars(
                    select(JobDispatch).where(JobDispatch.job_id.in_(job_ids))
                )
            ).all()
        }
        event_counts = dict(
            (
                await session.execute(
                    select(JobEvent.job_id, func.count(JobEvent.id))
                    .where(JobEvent.job_id.in_(job_ids), JobEvent.state == "queued")
                    .group_by(JobEvent.job_id)
                )
            ).all()
        )
        for intent in ordered:
            job = jobs.get(intent.job_id)
            dispatch = dispatches.get((intent.job_id, intent.dispatch_generation))
            if job is None or dispatch is None:
                raise PgQueuerInvariantError(
                    "every canonical job and dispatch must exist before bulk enqueue"
                )
            try:
                JOB_DEFINITION_REGISTRY.for_dispatch(job.type, entrypoint=intent.entrypoint)
            except JobDefinitionError as exc:
                raise PgQueuerGatewayError(str(exc)) from exc
            if job.phase != "queued" or job.dispatch_generation != intent.dispatch_generation:
                raise PgQueuerInvariantError(
                    "canonical bulk job phase/generation does not match dispatch"
                )
            if job.pgq_job_id is not None or dispatch.pgq_job_id is not None:
                raise PgQueuerInvariantError("bulk dispatch is already linked to a ticket")
            if (
                dispatch.entrypoint != intent.entrypoint
                or dispatch.dedupe_key != intent.dedupe_key
                or dispatch.priority != intent.priority
            ):
                raise PgQueuerInvariantError("bulk dispatch audit does not match enqueue intent")
            if not event_counts.get(intent.job_id):
                raise PgQueuerInvariantError(
                    "every bulk job requires an initial queued event before enqueue"
                )

        payloads = [
            self._transport_payload(
                job_id=intent.job_id,
                payload_version=intent.payload_version,
                dispatch_generation=intent.dispatch_generation,
            )
            for intent in ordered
        ]
        try:
            async with self._queries(session) as queries:
                ids = await queries.enqueue(
                    [intent.entrypoint for intent in ordered],
                    payloads,
                    priority=[intent.priority for intent in ordered],
                    execute_after=[intent.execute_after or timedelta(0) for intent in ordered],
                    dedupe_key=dedupe_keys,
                )
        except DuplicateJobError as exc:
            raise PgQueuerInvariantError(
                "PgQueuer dedupe conflict for a canonical bulk dispatch"
            ) from exc
        if (
            len(ids) != len(ordered)
            or any(not isinstance(ticket_id, int) for ticket_id in ids)
            or len(set(ids)) != len(ids)
        ):
            raise PgQueuerInvariantError(
                "PgQueuer bulk enqueue must return ordered one-for-one unique numeric IDs"
            )

        ticket_ids = tuple(int(ticket_id) for ticket_id in ids)
        for intent, ticket_id in zip(ordered, ticket_ids, strict=True):
            job = jobs[intent.job_id]
            dispatch = dispatches[(intent.job_id, intent.dispatch_generation)]
            job.pgq_job_id = ticket_id
            dispatch.pgq_job_id = ticket_id
        await session.flush()
        return ticket_ids

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
            dispatch.disposition = "cancelled"
            dispatch.ended_at = now
            await job_event_writer.append(
                session,
                job_id=job.id,
                event_key="job.cancelled",
                state="cancelled",
                message="system_noop cancelled while queued",
            )
            from marquee.core.jobs.batches import project_terminal_child

            await project_terminal_child(session, job)
        elif transport_status == "picked":
            job.phase = "stopping"
            job.stopping_at = now
            await job_event_writer.append(
                session,
                job_id=job.id,
                event_key="job.stopping",
                state="stopping",
                message="system_noop cancellation requested",
            )
        else:
            raise PgQueuerInvariantError(
                f"transport status {transport_status!r} cannot accept cancellation"
            )

    async def reprioritize_known_ticket(
        self,
        session: AsyncSession,
        *,
        job_id: str,
        priority: int,
    ) -> None:
        """Replace one queued ticket so canonical and transport priority stay aligned."""
        if not MIN_PRIORITY <= priority <= MAX_PRIORITY:
            raise PgQueuerGatewayError(
                f"priority must be between {MIN_PRIORITY} and {MAX_PRIORITY}"
            )
        job = await session.scalar(select(Job).where(Job.id == job_id).with_for_update())
        if job is None:
            raise PgQueuerGatewayError("canonical job does not exist")
        if job.phase not in {"planned", "queued"}:
            raise PgQueuerGatewayError("only planned or queued jobs can change priority")
        if job.pgq_job_id is None:
            job.priority = priority
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
            if rows[0][1] != "queued":
                raise PgQueuerGatewayError("only queued transport work can change priority")
            await queries.mark_job_as_cancelled([job.pgq_job_id])

        now = datetime.now(UTC)
        dispatch.disposition = "superseded"
        dispatch.ended_at = now
        generation = job.dispatch_generation + 1
        dedupe_key = f"marquee:{job.id}:{generation}"
        replacement = JobDispatch(
            job_id=job.id,
            generation=generation,
            pgq_job_id=None,
            entrypoint=dispatch.entrypoint,
            dedupe_key=dedupe_key,
            priority=priority,
            eligible_at=job.eligible_at,
            disposition="active",
        )
        job.priority = priority
        job.dispatch_generation = generation
        job.pgq_job_id = None
        session.add(replacement)
        await job_event_writer.append(
            session,
            job_id=job.id,
            event_key="job.priority_changed",
            state=job.phase,
            message="Job priority changed within its execution class",
            detail={
                "dispatch_generation": generation,
                "priority": priority,
            },
        )
        await session.flush()
        await self.enqueue(
            session,
            job_id=job.id,
            entrypoint=dispatch.entrypoint,
            payload_version=job.payload_version,
            dispatch_generation=generation,
            priority=priority,
            execute_after=max(job.eligible_at - now, timedelta(0)),
            dedupe_key=dedupe_key,
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
