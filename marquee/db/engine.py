"""The async engine and session factory.

Nothing here connects at import time: callers build an engine when they need one and dispose
of it when they are done (the API in its lifespan, a command when it finishes). The URL
carries the password, so it is never logged; SQLAlchemy masks it in an engine's repr.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from sqlalchemy.ext.asyncio import create_async_engine as _create_async_engine

from marquee.config import settings

# A database that is down should fail a request or a command quickly, not hang it.
CONNECT_TIMEOUT_S = 10


def create_engine(url: str | None = None) -> AsyncEngine:
    """An async engine on psycopg 3, for ``url`` or the configured database."""
    return _create_async_engine(
        url or settings.database_url,
        # A pooled connection can die with the database container; check before use.
        pool_pre_ping=True,
        connect_args={"application_name": "marquee", "connect_timeout": CONNECT_TIMEOUT_S},
    )


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Sessions that keep loaded attributes after commit, as asyncio code needs."""
    return async_sessionmaker(engine, expire_on_commit=False)
