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
from collections.abc import AsyncGenerator

from sqlalchemy import text
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


async def reset_database(db: AsyncSession) -> dict[str, str | int]:
    """Delete all row data from every application table.

    PostgreSQL uses ``TRUNCATE ... RESTART IDENTITY CASCADE`` so foreign-key
    graphs clear in one statement and primary-key sequences restart.
    """
    __import__("marquee.models")

    tables = list(Base.metadata.sorted_tables)
    connection = await db.connection()
    dialect = connection.dialect.name

    if not tables:
        return {"status": "reset", "dialect": dialect, "tables_cleared": 0}

    if dialect != "postgresql":
        raise RuntimeError(f"reset_database requires PostgreSQL, got {dialect!r}")

    preparer = connection.dialect.identifier_preparer
    formatted_tables = ", ".join(preparer.format_table(table) for table in tables)
    await db.execute(text(f"TRUNCATE TABLE {formatted_tables} RESTART IDENTITY CASCADE"))

    await db.commit()
    return {"status": "reset", "dialect": dialect, "tables_cleared": len(tables)}


async def close_db() -> None:
    """Dispose of the engine and connection pool.

    Call once at application shutdown.
    """
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _session_factory = None
