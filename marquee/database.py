"""Async SQLAlchemy engine, session factory, and FastAPI dependency.

PostgreSQL is the sole source of truth for every Marquee runtime role.

Connection lifecycle:
  - The engine is created lazily on first use (no import-time side effects).
  - Sessions are created per-request via FastAPI's dependency injection.
  - ``expire_on_commit=False`` prevents lazy-load issues after commit.
  - Pool sized for concurrent API requests + workers + SSE streams.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
from collections.abc import AsyncGenerator
from pathlib import Path

from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from marquee.config import settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Declarative Base
# ---------------------------------------------------------------------------


class Base(DeclarativeBase):
    """Base class for all SQLAlchemy ORM models."""


# ---------------------------------------------------------------------------
# Engine & Session Factory (lazy init)
# ---------------------------------------------------------------------------

_engine = None
_session_factory: async_sessionmaker | None = None


def _engine_connect_args(db_url: str) -> dict:
    """Return role-tagged asyncpg safety settings for PostgreSQL."""
    if not db_url.startswith("postgresql+asyncpg://"):
        return {}
    server_settings = {"application_name": f"marquee:{settings.MARQUEE_PROCESS_ROLE}"}
    if settings.DB_LOCK_TIMEOUT_MS:
        server_settings["lock_timeout"] = str(settings.DB_LOCK_TIMEOUT_MS)
    if settings.DB_IDLE_TXN_TIMEOUT_MS:
        server_settings["idle_in_transaction_session_timeout"] = str(
            settings.DB_IDLE_TXN_TIMEOUT_MS
        )
    return {"server_settings": server_settings}


def _get_engine():
    """Create or return the role-budgeted async SQLAlchemy engine."""
    global _engine
    if _engine is None:
        pool_size, max_overflow = settings.db_pool_budget
        _engine = create_async_engine(
            settings.db_url_resolved,
            echo=False,
            pool_pre_ping=True,
            pool_size=pool_size,
            max_overflow=max_overflow,
            pool_recycle=3600,
            connect_args=_engine_connect_args(settings.db_url_resolved),
        )
    return _engine


def _get_session_factory() -> async_sessionmaker:
    """Create or return the async session factory."""
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            bind=_get_engine(),
            class_=AsyncSession,
            expire_on_commit=False,
        )
    return _session_factory


def pool_stats() -> dict[str, int | None]:
    """Point-in-time telemetry for the current role-budgeted SQLAlchemy pool."""
    pool_size, max_overflow = settings.db_pool_budget
    if _engine is None:
        return {
            "size": None,
            "checked_in": None,
            "checked_out": None,
            "overflow": None,
            "configured_size": pool_size,
            "configured_max_overflow": max_overflow,
        }
    pool = _engine.pool
    stats: dict[str, int | None] = {}
    for key, attr in (
        ("size", "size"),
        ("checked_in", "checkedin"),
        ("checked_out", "checkedout"),
        ("overflow", "overflow"),
    ):
        method = getattr(pool, attr, None)
        try:
            stats[key] = method() if callable(method) else None
        except (NotImplementedError, AttributeError):
            stats[key] = None
    stats["configured_size"] = pool_size
    stats["configured_max_overflow"] = max_overflow
    return stats


# ---------------------------------------------------------------------------
# FastAPI Dependency
# ---------------------------------------------------------------------------


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency: yields a database session per request.

    Usage:
        @router.get("/items")
        async def list_items(db: AsyncSession = Depends(get_db)):
            ...
    """
    factory = _get_session_factory()
    async with factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


# ---------------------------------------------------------------------------
# Lifecycle Helpers
# ---------------------------------------------------------------------------


async def init_db(retries: int = 5) -> None:
    """Verify the migrated database is reachable.

    Schema changes are applied by the dedicated migration service before API,
    worker, and scheduler containers start.  Creating tables from application
    startup races with horizontally scaled services and is intentionally gone.

    Retries with exponential backoff handle transient failures during:
      - PostgreSQL container startup (Docker Compose race)
      - Database server restarts (maintenance windows)
      - Network interruptions (Kubernetes pod scheduling)
    """
    engine = _get_engine()
    for attempt in range(retries):
        try:
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            logger.info("Database connection verified")
            return
        except Exception as exc:
            if attempt == retries - 1:
                logger.error("Database connection failed after %d attempts", retries, exc_info=True)
                raise
            wait = 2**attempt  # 1, 2, 4, 8, 16 seconds
            logger.warning(
                "Database connection failed (attempt %d/%d), retrying in %ds: %s",
                attempt + 1,
                retries,
                wait,
                exc,
            )
            await asyncio.sleep(wait)


# Tables that describe the running system rather than its contents. A reset
# clears application data; it must leave registration rows that live processes
# and the migrator have already written:
#
#   runtime_instances — a running worker or scheduler holds its own row id for
#       the life of the process and ``job_attempts`` references it. Deleting the
#       row strands every in-process handle: heartbeats fail forever and each
#       subsequent attempt dies on the foreign key.
#   schema_contracts — written by ``marquee.db_migration``. Worker and scheduler
#       startup verifies against it and fails closed, so clearing it blocks the
#       next boot until the migration command is run again.
#   worker_nodes — re-created on demand by ``record_worker_node``, but the same
#       category, and keeping it spares a reset-shaped gap in node telemetry.
_RUNTIME_REGISTRATION_TABLES = frozenset(
    {
        "runtime_instances",
        "schema_contracts",
        "worker_nodes",
    }
)

# PgQueuer owns its transport tables, so they are absent from ``Base.metadata``
# and a metadata-driven truncate would leave queue rows naming jobs that no
# longer exist. ``pgqueuer_schedules`` is deliberately not here: the running
# scheduler registers it once at startup, exactly like the tables above.
_TRANSPORT_QUEUE_TABLES = ("pgqueuer", "pgqueuer_log", "pgqueuer_statistics")

# Everything under the data root that exists only to back a job row. Truncating the
# job tables strands all of it, so a reset that leaves it behind is not a reset.
_JOB_EVIDENCE_DIRECTORIES = ("jobs/evidence", "jobs/workspaces")

# How long to let the worker tear down live jobs before wiping anyway. Cancellation
# is cooperative: the worker has to notice the intent, stop the runner child, and
# seal its evidence. Past this the reset proceeds and reports what was still running.
_QUIESCE_TIMEOUT_SECONDS = 30.0
_QUIESCE_POLL_SECONDS = 0.5


async def _request_stop_for_live_jobs(db: AsyncSession) -> int:
    """Signal cancellation for every job that has not reached terminal.

    Sets the durable intent the delivery loop already polls, so a running handler
    stops its runner child and releases its process group rather than being
    orphaned by the truncate and left writing into deleted rows.
    """
    from marquee.models import Job  # noqa: PLC0415

    result = await db.execute(
        update(Job)
        .where(Job.phase != "terminal", Job.desired_state != "cancel")
        .values(desired_state="cancel")
    )
    await db.commit()
    return int(result.rowcount or 0)


async def _await_quiescence(db: AsyncSession, *, timeout_seconds: float) -> list[str]:
    """Wait for executing jobs to release; return the ids that never did.

    Only ``running`` and ``stopping`` jobs own a process. Planned and queued work has
    no handler to unwind and its transport ticket is about to be truncated, so
    waiting on it would just add a timeout to the common case.
    """
    from marquee.models import Job  # noqa: PLC0415

    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_seconds
    while True:
        await db.rollback()  # a fresh read each poll — the worker commits elsewhere
        executing = list(
            (await db.scalars(select(Job.id).where(Job.phase.in_(("running", "stopping"))))).all()
        )
        if not executing or loop.time() >= deadline:
            return executing
        await asyncio.sleep(_QUIESCE_POLL_SECONDS)


def _clear_job_evidence() -> int:
    """Remove the on-disk evidence the truncated job rows used to own."""
    root = Path(settings.DATA_DIR)
    removed = 0
    for relative in _JOB_EVIDENCE_DIRECTORIES:
        directory = root / relative
        if not directory.is_dir() or directory.is_symlink():
            continue
        for child in directory.iterdir():
            try:
                if child.is_symlink() or child.is_file():
                    child.unlink()
                else:
                    shutil.rmtree(child)
            except OSError:
                logger.warning("Could not remove stranded job evidence: %s", child)
                continue
            removed += 1
    return removed


async def reset_database(
    db: AsyncSession,
    *,
    include_runtime_registration: bool = False,
    quiesce: bool = True,
    quiesce_timeout_seconds: float | None = None,
) -> dict[str, str | int | list[str]]:
    """Return the installation to a clean, fresh state.

    Three things have to go, in order, or the "empty" database is not actually
    empty of consequences:

    1. **Live work.** Cancellation is requested for every non-terminal job and the
       platform is given until ``quiesce_timeout_seconds`` to release them. Skipping
       this leaves handlers running against rows that no longer exist — they cannot
       do damage (every write is fenced) but they keep runner children alive and
       emit rejected progress until they finish on their own.
    2. **Rows.** ``TRUNCATE ... RESTART IDENTITY CASCADE`` clears foreign-key graphs
       in one statement and restarts primary-key sequences.
    3. **Bytes.** Attempt workspaces and registered evidence exist only to back job
       rows, so they are removed too; otherwise a wiped database keeps accumulating
       artifacts nothing references.

    Runtime registration is preserved by default so a live worker and scheduler keep
    serving without a restart. Pass ``include_runtime_registration`` to empty those
    too — for a test fixture isolating one schema, where no process is registered
    against the rows, and where ``quiesce=False`` avoids waiting on a platform that
    is not running.

    Caches, poster backups, and model files are deliberately left alone: they are not
    owned by any row here, and are either self-healing or a safety net.
    """
    __import__("marquee.models")

    timeout = _QUIESCE_TIMEOUT_SECONDS if quiesce_timeout_seconds is None else quiesce_timeout_seconds
    stop_requested = 0
    still_running: list[str] = []
    if quiesce:
        stop_requested = await _request_stop_for_live_jobs(db)
        still_running = await _await_quiescence(db, timeout_seconds=timeout)
        if still_running:
            logger.warning(
                "Reset proceeding with %d job(s) still live after %.0fs: %s",
                len(still_running),
                timeout,
                ", ".join(still_running[:10]),
            )

    tables = [
        table
        for table in Base.metadata.sorted_tables
        if include_runtime_registration or table.name not in _RUNTIME_REGISTRATION_TABLES
    ]
    connection = await db.connection()
    dialect = connection.dialect.name

    if not tables:
        return {"status": "reset", "dialect": dialect, "tables_cleared": 0}

    if dialect != "postgresql":
        raise RuntimeError(f"reset_database requires PostgreSQL, got {dialect!r}")

    preparer = connection.dialect.identifier_preparer
    formatted_tables = ", ".join(preparer.format_table(table) for table in tables)
    await db.execute(text(f"TRUNCATE TABLE {formatted_tables} RESTART IDENTITY CASCADE"))

    # Installed by the migration command, so absent wherever the schema came
    # from ``create_all`` alone; resolve each name against the search path.
    transport_tables = [
        name
        for name in _TRANSPORT_QUEUE_TABLES
        if await db.scalar(text("SELECT to_regclass(:name)"), {"name": name}) is not None
    ]
    if transport_tables:
        formatted_transport = ", ".join(preparer.quote(name) for name in transport_tables)
        await db.execute(text(f"TRUNCATE TABLE {formatted_transport} RESTART IDENTITY CASCADE"))

    table_names = {table.name for table in tables}
    if {"configuration_revisions", "configuration_current"} <= table_names:
        await db.execute(
            text(
                "INSERT INTO configuration_revisions "
                "(version, values, checksum, schema_version, actor, trigger) VALUES "
                "(1, '{}'::json, "
                "'44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a', "
                '1, \'{"kind":"system","id":"reset"}\'::json, \'reset_seed\')'
            )
        )
        await db.execute(
            text("INSERT INTO configuration_current (singleton_id, current_version) VALUES (1, 1)")
        )

    await db.commit()
    evidence_removed = await asyncio.to_thread(_clear_job_evidence)
    return {
        "status": "reset",
        "dialect": dialect,
        "tables_cleared": len(tables) + len(transport_tables),
        "jobs_stop_requested": stop_requested,
        "jobs_still_running": still_running,
        "evidence_entries_removed": evidence_removed,
    }


async def close_db() -> None:
    """Dispose of the engine and connection pool.

    Call once at application shutdown.
    """
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _session_factory = None
