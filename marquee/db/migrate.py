"""Apply and inspect the schema migrations.

The Alembic configuration is built here, in code: there is no ``alembic.ini``, and the
migration scripts live inside the package (``marquee/db/migrations/``) so an installed
package, such as the one in the container image, carries them. Each function opens its own
short-lived synchronous connection on psycopg 3 and hands it to the environment script; the
database URL never enters Alembic's configuration.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Connection, create_engine, pool

from marquee.config import settings

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"
CONNECT_TIMEOUT_S = 10


class MigrationError(Exception):
    """The migrations cannot be applied or inspected as asked; the message says why."""


@dataclass(frozen=True)
class MigrationStatus:
    current: str | None
    """The database's revision; ``None`` for a database no migration has touched."""
    head: str

    @property
    def at_head(self) -> bool:
        return self.current == self.head


def alembic_config(connection: Connection | None = None) -> Config:
    """Alembic's configuration, with ``connection`` for the environment script to use."""
    config = Config()
    # Option values go through ConfigParser interpolation; a literal % must be doubled.
    config.set_main_option("script_location", str(MIGRATIONS_DIR).replace("%", "%%"))
    if connection is not None:
        config.attributes["connection"] = connection
    return config


def head_revision() -> str:
    """The newest revision in the package. Reads the scripts; touches no database."""
    head = ScriptDirectory.from_config(alembic_config()).get_current_head()
    if head is None:
        raise MigrationError(f"no migration scripts under {MIGRATIONS_DIR}")
    return head


def current_revision(connection: Connection) -> str | None:
    """The revision the database on ``connection`` is at, or ``None``."""
    return MigrationContext.configure(connection).get_current_revision()


@contextmanager
def _connection(url: str | None) -> Iterator[Connection]:
    engine = create_engine(
        url or settings.database_url,
        poolclass=pool.NullPool,
        connect_args={"connect_timeout": CONNECT_TIMEOUT_S, "application_name": "marquee"},
    )
    try:
        # One transaction: PostgreSQL runs DDL transactionally, so a failed migration
        # leaves the schema as it was.
        with engine.begin() as connection:
            yield connection
    finally:
        engine.dispose()


def status(*, url: str | None = None) -> MigrationStatus:
    """Where the database is against the newest migration."""
    head = head_revision()
    with _connection(url) as connection:
        return MigrationStatus(current=current_revision(connection), head=head)


def upgrade(revision: str = "head", *, url: str | None = None) -> MigrationStatus:
    """Apply every migration up to ``revision`` and report where the database ends up."""
    head = head_revision()
    with _connection(url) as connection:
        command.upgrade(alembic_config(connection), revision)
        return MigrationStatus(current=current_revision(connection), head=head)


def downgrade(revision: str, *, url: str | None = None) -> MigrationStatus:
    """Undo migrations down to ``revision`` (``"base"`` undoes all of them)."""
    head = head_revision()
    with _connection(url) as connection:
        command.downgrade(alembic_config(connection), revision)
        return MigrationStatus(current=current_revision(connection), head=head)


def _next_revision_id() -> str:
    """Revisions are numbered in order: ``0001``, ``0002``, ..."""
    script = ScriptDirectory.from_config(alembic_config())
    numbers = [
        int(rev.revision) for rev in script.walk_revisions() if re.fullmatch(r"\d+", rev.revision)
    ]
    return f"{max(numbers, default=0) + 1:04d}"


def revision(message: str, *, url: str | None = None) -> Path:
    """Write a new migration script from the difference between the models and the database.

    The database must be at head. Read and tidy the script before committing it.
    """
    current = status(url=url)
    if not current.at_head:
        raise MigrationError(
            f"the database is at {current.current or 'no revision'}, not {current.head}; "
            "run `marquee db upgrade` first"
        )
    with _connection(url) as connection:
        script = command.revision(
            alembic_config(connection),
            message=message,
            autogenerate=True,
            rev_id=_next_revision_id(),
        )
    if script is None or isinstance(script, list):
        raise MigrationError("Alembic did not write exactly one revision")
    return Path(script.path)
