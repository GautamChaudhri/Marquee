"""Async SQLAlchemy engine, session factory, and FastAPI dependency.

PostgreSQL is the sole source of truth for every Marquee runtime role.

Connection lifecycle:
  - The engine is created lazily on first use (no import-time side effects).
  - Sessions are created per-request via FastAPI's dependency injection.
  - ``expire_on_commit=False`` prevents lazy-load issues after commit.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

from sqlalchemy import text
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
        _engine = create_async_engine(settings.db_url_resolved, echo=False, pool_pre_ping=True)

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
    """Verify the migrated database is reachable.

    Schema changes are applied by the dedicated migration service before API,
    worker, and scheduler containers start.  Creating tables from application
    startup races with horizontally scaled services and is intentionally gone.
    """
    engine = _get_engine()
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))


async def close_db() -> None:
    """Dispose of the engine and connection pool.

    Call once at application shutdown.
    """
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _session_factory = None
