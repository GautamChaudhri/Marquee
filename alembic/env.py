"""Alembic environment configuration.

The application uses PostgreSQL's async driver at runtime; Alembic uses
psycopg's synchronous PostgreSQL driver for schema changes.
"""

from logging.config import fileConfig

from sqlalchemy import create_engine

from alembic import context

# Import all models so Alembic can detect schema changes
from marquee import models  # noqa: F401
from marquee.config import settings
from marquee.database import Base

# Alembic Config object
config = context.config

# Set up Python logging from alembic.ini
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# MetaData for autogenerate
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (generate SQL without connecting)."""
    url = settings.db_url_resolved
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection):
    """Execute migrations inside a transaction."""
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


def _sync_url(url: str) -> str:
    """Convert app async DB URLs to equivalent sync URLs for Alembic."""
    if url.startswith("postgresql+asyncpg:"):
        return url.replace("postgresql+asyncpg:", "postgresql+psycopg:", 1)
    return url


def run_migrations_online() -> None:
    """Run migrations in 'online' mode (connect to the database)."""
    connectable = create_engine(_sync_url(settings.db_url_resolved))

    with connectable.connect() as connection:
        do_run_migrations(connection)

    connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
