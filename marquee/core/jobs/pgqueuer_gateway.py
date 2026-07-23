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


@dataclass(frozen=True, slots=True)
class AdmissionDeferral:
    """Durable pre-admission recovery result for one canonical dispatch."""

    job_id: str
    dispatch_generation: int
    pgq_job_id: int
    cause: str
    defer_count: int
    next_eligible_at: datetime


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


    async def _live_ticket_statuses(
        self,
        session: AsyncSession,
        *,
        ticket_ids: Sequence[int],
        for_update: bool = False,
    ) -> dict[int, str]:
        """Read current PgQueuer rows, never historical queue-log state.

        PgQueuer 1.1.1 Queries.job_status reads the append-only log table. The
        installed settings expose the validated queue relation, and this
        enlisted read establishes whether a transport ticket still physically
        exists before a documented Queries mutation is attempted.
        """
        ids = tuple(int(ticket_id) for ticket_id in ticket_ids)
        if not 1 <= len(ids) <= MAX_STATUS_IDS or len(set(ids)) != len(ids):
            raise PgQueuerGatewayError("ticket_ids must be 1..100 unique numeric IDs")
        async with self._queries(session) as queries:
            queue_table = queries.qbq.settings.queue_table
            if (
                not queue_table
                or not (queue_table[0].isalpha() or queue_table[0] == "_")
                or not all(character.isalnum() or character == "_" for character in queue_table)
            ):
                raise PgQueuerInvariantError("configured PgQueuer queue relation is unsafe")
            statement = text(
                f"SELECT id, status::text AS status FROM {queue_table} "
                "WHERE id = ANY(:ticket_ids)"
                + (" FOR UPDATE" if for_update else "")
            )
            rows = (await session.execute(statement, {"ticket_ids": list(ids)})).all()
        return {int(row.id): str(row.status) for row in rows}

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

        statuses = await self._live_ticket_statuses(
            session,
            ticket_ids=(job.pgq_job_id,),
            for_update=True,
        )
        transport_status = statuses.get(job.pgq_job_id)
        if transport_status is None:
            raise PgQueuerInvariantError("known PgQueuer ticket is absent")
        async with self._queries(session) as queries:
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


    async def recover_admission_deferral(
        self,
        session: AsyncSession,
        *,
        job_id: str,
        delay: timedelta,
        cause: str,
        defer_count: int,
    ) -> AdmissionDeferral:
        """Repair one held or absent pre-admission ticket without creating an attempt."""
        if delay <= timedelta(0) or delay > MAX_DEFER:
            raise PgQueuerGatewayError("admission deferral delay is out of bounds")
        if defer_count < 1 or defer_count > 10:
            raise PgQueuerGatewayError("admission deferral count is out of bounds")

        job = await session.scalar(select(Job).where(Job.id == job_id).with_for_update())
        if job is None or job.pgq_job_id is None:
            raise PgQueuerGatewayError("canonical job has no current PgQueuer ticket")
        dispatch = await session.scalar(
            select(JobDispatch)
            .where(
                JobDispatch.job_id == job.id,
                JobDispatch.generation == job.dispatch_generation,
            )
            .with_for_update()
        )
        if (
            dispatch is None
            or dispatch.disposition != "active"
            or dispatch.pgq_job_id != job.pgq_job_id
            or job.phase != "queued"
            or job.desired_state != "run"
        ):
            raise PgQueuerInvariantError("canonical dispatch is not recoverable for admission")

        prior_ticket_id = job.pgq_job_id
        recreated = False
        status = (
            await self._live_ticket_statuses(
                session,
                ticket_ids=(prior_ticket_id,),
                for_update=True,
            )
        ).get(prior_ticket_id)
        if status is None:
            async with self._queries(session) as queries:
                try:
                    ids = await queries.enqueue(
                        dispatch.entrypoint,
                        self._transport_payload(
                            job_id=job.id,
                            payload_version=job.payload_version,
                            dispatch_generation=job.dispatch_generation,
                        ),
                        priority=dispatch.priority,
                        execute_after=delay,
                        dedupe_key=dispatch.dedupe_key,
                    )
                except DuplicateJobError as exc:
                    raise PgQueuerInvariantError(
                        "absent admission ticket conflicts with its canonical dedupe key"
                    ) from exc
                if len(ids) != 1 or not isinstance(ids[0], int):
                    raise PgQueuerInvariantError(
                        "admission recovery enqueue must return exactly one numeric ID"
                    )
                job.pgq_job_id = int(ids[0])
                dispatch.pgq_job_id = int(ids[0])
                recreated = True
        elif status in {"picked", "failed"}:
            # PgQueuer 1.1.1 exposes requeue through Queries.retry_job(); its
            # enlisted transaction atomically records the delayed queued state.
            from pgqueuer.models import Job as PgQueuerJob

            async with self._queries(session) as queries:
                await queries.retry_job(
                    PgQueuerJob.model_construct(id=prior_ticket_id), delay, None
                )
        elif status != "queued":
            raise PgQueuerInvariantError(
                f"transport status {status!r} cannot recover admission deferral"
            )

        now = datetime.now(UTC)
        next_eligible_at = now + delay
        job.eligible_at = next_eligible_at
        dispatch.eligible_at = next_eligible_at
        job.attention = {
            "code": "admission_deferred",
            "summary": "Safety-gate admission was durably deferred for recovery",
            "cause": cause,
            "defer_count": defer_count,
            "next_eligible_at": next_eligible_at.isoformat(),
        }
        await job_event_writer.append(
            session,
            job_id=job.id,
            event_key="job.admission_recovered",
            state="queued",
            message="Recovered pre-admission PgQueuer deferral",
            detail={
                "cause": cause,
                "defer_count": defer_count,
                "prior_pgq_job_id": prior_ticket_id,
                "transport_recreated": recreated,
            },
        )
        return AdmissionDeferral(
            job_id=job.id,
            dispatch_generation=job.dispatch_generation,
            pgq_job_id=job.pgq_job_id,
            cause=cause,
            defer_count=defer_count,
            next_eligible_at=next_eligible_at,
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

        statuses = await self._live_ticket_statuses(
            session,
            ticket_ids=(job.pgq_job_id,),
            for_update=True,
        )
        if statuses.get(job.pgq_job_id) != "queued":
            raise PgQueuerGatewayError("only queued transport work can change priority")
        async with self._queries(session) as queries:
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
        by_ticket = await self._live_ticket_statuses(session, ticket_ids=tickets)
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
