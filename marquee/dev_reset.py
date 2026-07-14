"""Guarded database-only development reset for the unreleased JMC1 schema."""

from __future__ import annotations

import argparse
import asyncio

import asyncpg

from marquee.config import settings
from marquee.db_migration import (
    MigrationError,
    acquire_migration_lock,
    connect_admin,
    database_url,
    migrate_locked,
    release_migration_lock,
)

RESET_APPLICATION_NAME = "marquee:reset"


def validate_reset_request(*, allow_data_loss: bool, confirm_database: str) -> str:
    """Validate all non-database guards before opening a connection."""
    if settings.MARQUEE_ENVIRONMENT != "development":
        raise MigrationError("reset requires MARQUEE_ENVIRONMENT=development")
    if not allow_data_loss:
        raise MigrationError("reset requires --allow-data-loss")
    url = database_url()
    database_name = url.database
    if confirm_database != database_name:
        raise MigrationError(
            "--confirm-database must exactly match the configured PostgreSQL database name"
        )
    return database_name


async def _assert_no_other_marquee_connections(connection: asyncpg.Connection) -> None:
    rows = await connection.fetch(
        """
        SELECT application_name
        FROM pg_catalog.pg_stat_activity
        WHERE datname = current_database()
          AND pid <> pg_backend_pid()
          AND application_name LIKE 'marquee:%'
        ORDER BY application_name
        """
    )
    if rows:
        roles = ", ".join(sorted({row["application_name"] for row in rows}))
        raise MigrationError(f"other Marquee database connections are active: {roles}")


async def reset_development_database(
    *,
    allow_data_loss: bool,
    confirm_database: str,
) -> tuple[str, str]:
    """Reset only the configured database schema and reapply both schema owners."""
    validate_reset_request(
        allow_data_loss=allow_data_loss,
        confirm_database=confirm_database,
    )
    connection = await connect_admin(RESET_APPLICATION_NAME)
    try:
        await acquire_migration_lock(connection)
        try:
            await _assert_no_other_marquee_connections(connection)
            await connection.execute("DROP SCHEMA public CASCADE")
            await connection.execute("CREATE SCHEMA public")
            return await migrate_locked(connection)
        finally:
            await release_migration_lock(connection)
    finally:
        await connection.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Destroy and recreate the configured development database schema"
    )
    parser.add_argument("--allow-data-loss", action="store_true")
    parser.add_argument("--confirm-database", required=True)
    args = parser.parse_args()
    asyncio.run(
        reset_development_database(
            allow_data_loss=args.allow_data_loss,
            confirm_database=args.confirm_database,
        )
    )


if __name__ == "__main__":
    main()
