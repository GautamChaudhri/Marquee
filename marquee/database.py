"""Async SQLAlchemy engine, session factory, and FastAPI dependency.

SQLite is used as the database backend — appropriate for the scale of a
personal media library (hundreds to low thousands of items).

Connection lifecycle:
  - The engine is created lazily on first use (no import-time side effects).
  - SQLite foreign keys are enabled via a ``connect`` event listener.
  - Sessions are created per-request via FastAPI's dependency injection.
  - ``expire_on_commit=False`` prevents lazy-load issues after commit.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from marquee.config import settings

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


def _get_engine():
    """Create or return the async SQLAlchemy engine."""
    global _engine
    if _engine is None:
        conn_args = {}
        if settings.DB_URL.startswith("sqlite"):
            # SQLite needs check_same_thread=False for async access
            conn_args["check_same_thread"] = False

        _engine = create_async_engine(
            settings.db_url_resolved,
            echo=False,
            connect_args=conn_args or None,
        )

        # Enable foreign key constraints and WAL mode for SQLite
        if settings.db_url_resolved.startswith("sqlite"):

            @event.listens_for(_engine.sync_engine, "connect")
            def _set_sqlite_pragma(dbapi_connection, _connection_record):
                cursor = dbapi_connection.cursor()
                cursor.execute("PRAGMA foreign_keys=ON")
                cursor.execute("PRAGMA journal_mode=WAL")
                # Wait up to 5s for a competing writer instead of failing
                # instantly with "database is locked" — the long-running encode
                # worker holds the write lock in bursts while request handlers
                # also read/write the same SQLite file.
                cursor.execute("PRAGMA busy_timeout=5000")
                # WAL + NORMAL is the standard durable-enough, fast combination.
                cursor.execute("PRAGMA synchronous=NORMAL")
                cursor.close()

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


async def init_db() -> None:
    """Create all tables that don't exist yet.

    Call once at application startup. In production, prefer Alembic
    migrations (see ``alembic/`` directory).
    """
    from marquee import models  # noqa: F401 — register all models

    engine = _get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def close_db() -> None:
    """Dispose of the engine and connection pool.

    Call once at application shutdown.
    """
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _session_factory = None
