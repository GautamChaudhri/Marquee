"""Alembic's environment script.

Marquee builds its Alembic configuration in code (``marquee.db.migrate``) and always hands
this script an open connection, so no database URL, and so no password, ever passes through
Alembic's configuration. Migrations run online only.
"""

from __future__ import annotations

from alembic import context
from sqlalchemy import Connection

from marquee.db import models  # noqa: F401 - registers every table on Base.metadata
from marquee.db.base import Base


def _run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=Base.metadata,
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    raise RuntimeError("Marquee runs migrations against a database only; use `marquee db`")

_connection = context.config.attributes.get("connection")
if not isinstance(_connection, Connection):
    raise RuntimeError("no database connection was given; run migrations through `marquee db`")
_run_migrations(_connection)
