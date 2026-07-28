"""Database migration and schema-contract verification service."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import hashlib
import importlib.metadata
import json
import os
import subprocess
import sys
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import asyncpg
from alembic.config import Config
from sqlalchemy.engine import URL, make_url

from alembic import command
from marquee import __version__
from marquee.config import settings
from marquee.models.deployment import EXCLUDED_DEPLOYMENT_TABLES, get_deployment_metadata

ALEMBIC_HEAD = "0017_jobs_root"
PGQUEUER_VERSION = "1.1.1"
PGQUEUER_DURABILITY = "durable"
MIGRATION_ADVISORY_LOCK_ID = 0x4D4152514A4D4331
MIGRATION_APPLICATION_NAME = "marquee:migration"

PGQUEUER_TABLES = frozenset(
    {"pgqueuer", "pgqueuer_log", "pgqueuer_schedules", "pgqueuer_statistics"}
)
PGQUEUER_COLUMNS = {
    "pgqueuer": (
        "id",
        "priority",
        "queue_manager_id",
        "created",
        "updated",
        "heartbeat",
        "execute_after",
        "status",
        "entrypoint",
        "dedupe_key",
        "payload",
        "headers",
        "attempts",
    ),
    "pgqueuer_log": (
        "id",
        "created",
        "job_id",
        "status",
        "priority",
        "entrypoint",
        "traceback",
        "aggregated",
    ),
    "pgqueuer_schedules": (
        "id",
        "expression",
        "entrypoint",
        "heartbeat",
        "created",
        "updated",
        "next_run",
        "last_run",
        "status",
    ),
    "pgqueuer_statistics": ("id", "created", "count", "priority", "status", "entrypoint"),
}
PGQUEUER_INDEXES = frozenset(
    {
        "pgqueuer_ep_ea_idx",
        "pgqueuer_ep_prio_id_idx",
        "pgqueuer_heartbeat_id_id1_idx",
        "pgqueuer_log_created",
        "pgqueuer_log_job_id_status",
        "pgqueuer_log_not_aggregated",
        "pgqueuer_log_pkey",
        "pgqueuer_log_status",
        "pgqueuer_pkey",
        "pgqueuer_priority_id_id1_idx",
        "pgqueuer_queue_manager_id_idx",
        "pgqueuer_schedules_expression_entrypoint_key",
        "pgqueuer_schedules_pkey",
        "pgqueuer_statistics_pkey",
        "pgqueuer_statistics_unique_count",
        "pgqueuer_unique_dedupe_key",
        "pgqueuer_updated_id_id1_idx",
    }
)
PGQUEUER_ENUM_VALUES = (
    "queued",
    "picked",
    "successful",
    "exception",
    "canceled",
    "deleted",
    "failed",
)
PGQUEUER_FUNCTION = "fn_pgqueuer_changed"
PGQUEUER_TRIGGER = "tg_pgqueuer_changed"


class MigrationError(RuntimeError):
    """Raised when database ownership or schema verification fails closed."""


def database_url() -> URL:
    """Return and validate the configured PostgreSQL/asyncpg URL."""
    url = make_url(settings.db_url_resolved)
    if url.get_backend_name() != "postgresql" or url.get_driver_name() != "asyncpg":
        raise MigrationError("database administration requires PostgreSQL with asyncpg")
    if not url.database:
        raise MigrationError("configured PostgreSQL URL has no database name")
    return url


def asyncpg_dsn(url: URL | None = None) -> str:
    """Build an in-memory asyncpg DSN; never place it in subprocess arguments."""
    resolved = url or database_url()
    return resolved.set(drivername="postgresql").render_as_string(hide_password=False)


def libpq_environment(url: URL | None = None) -> dict[str, str]:
    """Return a confined subprocess environment using standard libpq variables."""
    resolved = url or database_url()
    env = os.environ.copy()
    if resolved.host:
        env["PGHOST"] = resolved.host
    if resolved.port:
        env["PGPORT"] = str(resolved.port)
    if resolved.username:
        env["PGUSER"] = resolved.username
    if resolved.password:
        env["PGPASSWORD"] = resolved.password
    if resolved.database:
        env["PGDATABASE"] = resolved.database
    env.pop("PGDSN", None)
    return env


async def connect_admin(application_name: str = MIGRATION_APPLICATION_NAME) -> asyncpg.Connection:
    """Open one role-tagged direct connection for locks and catalog checks."""
    return await asyncpg.connect(
        asyncpg_dsn(),
        server_settings={"application_name": application_name},
    )


async def acquire_migration_lock(connection: asyncpg.Connection) -> None:
    acquired = await connection.fetchval(
        "SELECT pg_try_advisory_lock($1)", MIGRATION_ADVISORY_LOCK_ID
    )
    if not acquired:
        raise MigrationError("the Marquee migration advisory lock is already held")


async def release_migration_lock(connection: asyncpg.Connection) -> None:
    await connection.fetchval("SELECT pg_advisory_unlock($1)", MIGRATION_ADVISORY_LOCK_ID)


def _run_alembic() -> None:
    package_root = Path(__file__).resolve().parent
    config_path = package_root / "alembic.ini"
    if not config_path.is_file():
        config_path = package_root.parent / "alembic.ini"
    config = Config(str(config_path))
    command.upgrade(config, "head")


def _run_pgqueuer_cli(*arguments: str) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "pgqueuer", *arguments],
        check=False,
        capture_output=True,
        env=libpq_environment(),
        text=True,
    )
    if result.returncode:
        detail = (result.stderr or result.stdout).strip()
        raise MigrationError(f"PgQueuer command {arguments[0]!r} failed: {detail[:500]}")


async def _object_presence(connection: asyncpg.Connection) -> dict[str, bool]:
    table_rows = await connection.fetch(
        """
        SELECT c.relname
        FROM pg_catalog.pg_class c
        JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = current_schema()
        """
    )
    tables = {row["relname"] for row in table_rows}
    enum_present = bool(
        await connection.fetchval(
            """
            SELECT EXISTS (
                SELECT 1 FROM pg_catalog.pg_type t
                JOIN pg_catalog.pg_namespace n ON n.oid = t.typnamespace
                WHERE n.nspname = current_schema() AND t.typname = 'pgqueuer_status'
            )
            """
        )
    )
    function_present = bool(
        await connection.fetchval(
            """
            SELECT EXISTS (
                SELECT 1 FROM pg_catalog.pg_proc p
                JOIN pg_catalog.pg_namespace n ON n.oid = p.pronamespace
                WHERE n.nspname = current_schema() AND p.proname = $1
            )
            """,
            PGQUEUER_FUNCTION,
        )
    )
    trigger_present = bool(
        await connection.fetchval(
            """
            SELECT EXISTS (
                SELECT 1
                FROM pg_catalog.pg_trigger t
                JOIN pg_catalog.pg_class c ON c.oid = t.tgrelid
                JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = current_schema()
                  AND c.relname = 'pgqueuer'
                  AND t.tgname = $1
                  AND NOT t.tgisinternal
            )
            """,
            PGQUEUER_TRIGGER,
        )
    )
    result = {f"table:{name}": name in tables for name in PGQUEUER_TABLES}
    result.update(
        {
            "enum:pgqueuer_status": enum_present,
            f"function:{PGQUEUER_FUNCTION}": function_present,
            f"trigger:{PGQUEUER_TRIGGER}": trigger_present,
        }
    )
    return result


async def pgqueuer_install_state(connection: asyncpg.Connection) -> str:
    presence = await _object_presence(connection)
    count = sum(presence.values())
    if count == 0:
        return "absent"
    if count == len(presence):
        return "present"
    missing = ", ".join(sorted(name for name, found in presence.items() if not found))
    raise MigrationError(f"partial PgQueuer installation; missing: {missing}")


def _fingerprint(facts: Mapping[str, Any]) -> str:
    encoded = json.dumps(facts, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


async def verify_marquee_catalog(connection: asyncpg.Connection) -> str:
    """Verify exact Alembic head/table ownership and return its live fingerprint."""
    revision = await connection.fetchval("SELECT version_num FROM alembic_version")
    if revision != ALEMBIC_HEAD:
        raise MigrationError(f"expected Alembic head {ALEMBIC_HEAD!r}, found {revision!r}")

    rows = await connection.fetch(
        """
        SELECT c.relname AS table_name, c.relpersistence::text AS persistence
        FROM pg_catalog.pg_class c
        JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = current_schema() AND c.relkind IN ('r', 'p')
        ORDER BY c.relname
        """
    )
    live = {row["table_name"]: row["persistence"] for row in rows}
    expected = set(get_deployment_metadata().tables)
    marquee_live = set(live) - PGQUEUER_TABLES - {"alembic_version"}
    if marquee_live != expected:
        missing = sorted(expected - marquee_live)
        extra = sorted(marquee_live - expected)
        raise MigrationError(f"Marquee catalog mismatch; missing={missing}, extra={extra}")
    excluded = sorted(set(live) & EXCLUDED_DEPLOYMENT_TABLES)
    if excluded:
        raise MigrationError(f"excluded legacy tables are deployed: {excluded}")
    if any(live[name] != "p" for name in expected):
        raise MigrationError("all Marquee deployment tables must be durable/logged")

    columns = await connection.fetch(
        """
        SELECT table_name, column_name, data_type, is_nullable
        FROM information_schema.columns
        WHERE table_schema = current_schema() AND table_name = ANY($1::text[])
        ORDER BY table_name, ordinal_position
        """,
        sorted(expected),
    )
    facts = {
        "revision": revision,
        "tables": sorted((name, live[name]) for name in expected),
        "columns": [tuple(row.values()) for row in columns],
    }
    return _fingerprint(facts)


async def verify_pgqueuer_catalog(connection: asyncpg.Connection) -> str:
    """Verify pinned 1.1.1 catalog facts without reproducing package DDL."""
    installed_version = importlib.metadata.version("pgqueuer")
    if installed_version != PGQUEUER_VERSION:
        raise MigrationError(f"expected pgqueuer {PGQUEUER_VERSION}, installed {installed_version}")
    if await pgqueuer_install_state(connection) != "present":
        raise MigrationError("PgQueuer is not installed")

    table_rows = await connection.fetch(
        """
        SELECT c.relname AS table_name, c.relpersistence::text AS persistence,
               COALESCE(c.reloptions, ARRAY[]::text[]) AS reloptions
        FROM pg_catalog.pg_class c
        JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = current_schema() AND c.relname = ANY($1::text[])
        ORDER BY c.relname
        """,
        sorted(PGQUEUER_TABLES),
    )
    persistence = {row["table_name"]: row["persistence"] for row in table_rows}
    if set(persistence) != PGQUEUER_TABLES or any(value != "p" for value in persistence.values()):
        raise MigrationError("PgQueuer tables must all be present and durable/logged")

    column_rows = await connection.fetch(
        """
        SELECT table_name, column_name
        FROM information_schema.columns
        WHERE table_schema = current_schema() AND table_name = ANY($1::text[])
        ORDER BY table_name, ordinal_position
        """,
        sorted(PGQUEUER_TABLES),
    )
    columns: dict[str, list[str]] = {name: [] for name in PGQUEUER_TABLES}
    for row in column_rows:
        columns[row["table_name"]].append(row["column_name"])
    if {name: tuple(value) for name, value in columns.items()} != PGQUEUER_COLUMNS:
        raise MigrationError("PgQueuer 1.1.1 column contract mismatch")

    indexes = set(
        await connection.fetch(
            """
            SELECT indexname FROM pg_catalog.pg_indexes
            WHERE schemaname = current_schema() AND tablename = ANY($1::text[])
            """,
            sorted(PGQUEUER_TABLES),
        )
    )
    index_names = {row["indexname"] for row in indexes}
    if index_names != PGQUEUER_INDEXES:
        raise MigrationError("PgQueuer 1.1.1 index contract mismatch")

    enum_values = tuple(
        await connection.fetch(
            """
            SELECT e.enumlabel
            FROM pg_catalog.pg_enum e
            JOIN pg_catalog.pg_type t ON t.oid = e.enumtypid
            JOIN pg_catalog.pg_namespace n ON n.oid = t.typnamespace
            WHERE n.nspname = current_schema() AND t.typname = 'pgqueuer_status'
            ORDER BY e.enumsortorder
            """
        )
    )
    values = tuple(row["enumlabel"] for row in enum_values)
    if values != PGQUEUER_ENUM_VALUES:
        raise MigrationError("PgQueuer 1.1.1 status enum contract mismatch")

    trigger_function = await connection.fetchrow(
        """
        SELECT t.tgname, p.proname
        FROM pg_catalog.pg_trigger t
        JOIN pg_catalog.pg_proc p ON p.oid = t.tgfoid
        JOIN pg_catalog.pg_class c ON c.oid = t.tgrelid
        JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = current_schema() AND c.relname = 'pgqueuer'
          AND t.tgname = $1 AND NOT t.tgisinternal
        """,
        PGQUEUER_TRIGGER,
    )
    if trigger_function is None or trigger_function["proname"] != PGQUEUER_FUNCTION:
        raise MigrationError("PgQueuer notification trigger/function contract mismatch")

    facts = {
        "package": installed_version,
        "durability": PGQUEUER_DURABILITY,
        "tables": [tuple(row.values()) for row in table_rows],
        "columns": {name: columns[name] for name in sorted(columns)},
        "indexes": sorted(index_names),
        "enum": values,
        "trigger": tuple(trigger_function.values()),
    }
    return _fingerprint(facts)


async def verify_runtime_schema(connection: asyncpg.Connection) -> None:
    """Fail closed unless live catalogs exactly match the recorded JMC1 contract."""
    marquee_fingerprint = await verify_marquee_catalog(connection)
    pgqueuer_fingerprint = await verify_pgqueuer_catalog(connection)
    rows = await connection.fetch(
        """
        SELECT component, expected_version, durability, catalog_fingerprint
        FROM schema_contracts
        WHERE component = ANY($1::text[])
        """,
        ["marquee", "pgqueuer"],
    )
    contracts = {row["component"]: row for row in rows}
    expected = {
        "marquee": (ALEMBIC_HEAD, None, marquee_fingerprint),
        "pgqueuer": (PGQUEUER_VERSION, PGQUEUER_DURABILITY, pgqueuer_fingerprint),
    }
    for component, values in expected.items():
        marker = contracts.get(component)
        actual = (
            None
            if marker is None
            else (
                marker["expected_version"],
                marker["durability"],
                marker["catalog_fingerprint"],
            )
        )
        if actual != values:
            raise MigrationError(
                f"{component} schema contract marker mismatch; run the migration command"
            )


async def _write_contract_markers(
    connection: asyncpg.Connection,
    marquee_fingerprint: str,
    pgqueuer_fingerprint: str,
) -> None:
    verified_at = datetime.now(UTC)
    async with connection.transaction():
        await connection.executemany(
            """
            INSERT INTO schema_contracts (
                component, expected_version, durability, catalog_fingerprint,
                verified_at, verifier_build
            ) VALUES ($1, $2, $3, $4, $5, $6)
            ON CONFLICT (component) DO UPDATE SET
                expected_version = EXCLUDED.expected_version,
                durability = EXCLUDED.durability,
                catalog_fingerprint = EXCLUDED.catalog_fingerprint,
                verified_at = EXCLUDED.verified_at,
                verifier_build = EXCLUDED.verifier_build
            """,
            [
                (
                    "marquee",
                    ALEMBIC_HEAD,
                    None,
                    marquee_fingerprint,
                    verified_at,
                    __version__,
                ),
                (
                    "pgqueuer",
                    PGQUEUER_VERSION,
                    PGQUEUER_DURABILITY,
                    pgqueuer_fingerprint,
                    verified_at,
                    __version__,
                ),
            ],
        )


def migrate_evidence_root(data_dir: str | Path) -> str:
    """Move the job evidence root from its former ``jmc3`` name to ``jobs``.

    The directory holds attempt workspaces, captured logs, and registered
    artifacts. Alembic rewrites the stored ``storage_key`` prefixes; this moves
    the bytes they point at. Idempotent, and safe to run when neither, either,
    or both directories exist.

    The migration advisory lock excludes a second migration, not a running
    worker: stop the API and workers before migrating, or a worker can create an
    attempt workspace under the old root after it has been moved.

    Returns a short status for the operator: ``absent``, ``current``, ``moved``,
    or a ``merged`` description.
    """
    root = Path(data_dir)
    legacy, current = root / "jmc3", root / "jobs"
    if not legacy.is_dir() or legacy.is_symlink():
        return "absent" if not current.is_dir() else "current"
    if not current.exists():
        legacy.rename(current)
        return "moved"

    # Both exist — an interrupted move, or a downgrade and re-upgrade. Merge file
    # by file: the migrated copy always wins, and a legacy file that would
    # overwrite one is left in place for inspection rather than destroyed.
    stranded = 0
    for source in sorted(legacy.rglob("*")):
        if source.is_symlink() or not source.is_file():
            continue
        destination = current / source.relative_to(legacy)
        if destination.exists():
            stranded += 1
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        source.rename(destination)
    for directory in sorted((p for p in legacy.rglob("*") if p.is_dir()), reverse=True):
        with contextlib.suppress(OSError):
            directory.rmdir()
    if stranded:
        return f"merged with {stranded} legacy file(s) left under {legacy}"
    with contextlib.suppress(OSError):
        legacy.rmdir()
    return "merged"


async def migrate_locked(connection: asyncpg.Connection) -> tuple[str, str]:
    """Apply and verify both schema owners while the caller holds the lock."""
    await asyncio.to_thread(_run_alembic)
    evidence_root = await asyncio.to_thread(migrate_evidence_root, settings.DATA_DIR)
    if evidence_root not in ("absent", "current"):
        print(f"INFO  [marquee.evidence_root] {evidence_root}")
    state = await pgqueuer_install_state(connection)
    if state == "absent":
        await asyncio.to_thread(_run_pgqueuer_cli, "install", "--durability", "durable")
    # PgQueuer 1.1.1's official upgrade adds an index omitted by its fresh
    # installer. Running the public upgrade after either state makes repeated
    # migrations and fresh resets converge without reproducing package DDL.
    await asyncio.to_thread(_run_pgqueuer_cli, "upgrade", "--durability", "durable")
    await asyncio.to_thread(_run_pgqueuer_cli, "durability", "durable")
    await asyncio.to_thread(_run_pgqueuer_cli, "autovac")
    await asyncio.to_thread(_run_pgqueuer_cli, "verify", "--expect", "present")

    marquee_fingerprint = await verify_marquee_catalog(connection)
    pgqueuer_fingerprint = await verify_pgqueuer_catalog(connection)
    await _write_contract_markers(connection, marquee_fingerprint, pgqueuer_fingerprint)
    return marquee_fingerprint, pgqueuer_fingerprint


async def migrate_database() -> tuple[str, str]:
    """Acquire the fixed advisory lock and run the complete migration service."""
    connection = await connect_admin()
    try:
        await acquire_migration_lock(connection)
        try:
            return await migrate_locked(connection)
        finally:
            await release_migration_lock(connection)
    finally:
        await connection.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Apply and verify the JMC1 database schemas")
    parser.parse_args()
    asyncio.run(migrate_database())


if __name__ == "__main__":
    main()
