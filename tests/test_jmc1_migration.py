"""JMC1 clean-baseline, migration-service, and guarded-reset integration tests."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import asyncpg
import pytest
import pytest_asyncio
from alembic.config import Config
from alembic.script import ScriptDirectory
from pgqueuer import Queries
from sqlalchemy.engine import URL

import marquee.db_migration as migration
from marquee.config import settings
from marquee.db_migration import (
    ALEMBIC_HEAD,
    PGQUEUER_TABLES,
    MigrationError,
    asyncpg_dsn,
    database_url,
    migrate_database,
    pgqueuer_install_state,
    verify_runtime_schema,
)
from marquee.dev_reset import reset_development_database, validate_reset_request
from marquee.models.deployment import EXCLUDED_DEPLOYMENT_TABLES, get_deployment_metadata


@dataclass(frozen=True)
class _OwnedDatabase:
    name: str
    url: URL

    async def connect(self, application_name: str = "") -> asyncpg.Connection:
        server_settings = {"application_name": application_name} if application_name else None
        return await asyncpg.connect(asyncpg_dsn(self.url), server_settings=server_settings)


@pytest_asyncio.fixture
async def owned_jmc1_database(monkeypatch) -> AsyncIterator[_OwnedDatabase]:
    admin_url = database_url()
    name = f"jmc1_{uuid.uuid4().hex}"
    admin = await asyncpg.connect(asyncpg_dsn(admin_url))
    await admin.execute(f'CREATE DATABASE "{name}"')
    owned_url = admin_url.set(database=name)
    monkeypatch.setattr(settings, "DB_URL", owned_url.render_as_string(hide_password=False))
    monkeypatch.setattr(settings, "MARQUEE_ENVIRONMENT", "development")
    try:
        yield _OwnedDatabase(name=name, url=owned_url)
    finally:
        await admin.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname = $1 AND pid <> pg_backend_pid()",
            name,
        )
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}"')
        await admin.close()


def test_reset_guards_fail_closed(monkeypatch) -> None:
    monkeypatch.setattr(settings, "MARQUEE_ENVIRONMENT", "production")
    with pytest.raises(MigrationError, match="MARQUEE_ENVIRONMENT=development"):
        validate_reset_request(allow_data_loss=True, confirm_database="marquee")

    monkeypatch.setattr(settings, "MARQUEE_ENVIRONMENT", "development")
    with pytest.raises(MigrationError, match="allow-data-loss"):
        validate_reset_request(allow_data_loss=False, confirm_database="marquee")
    with pytest.raises(MigrationError, match="exactly match"):
        validate_reset_request(allow_data_loss=True, confirm_database="wrong")

    monkeypatch.setattr(settings, "DB_URL", "sqlite+aiosqlite:///unsafe.db")
    with pytest.raises(MigrationError, match="PostgreSQL with asyncpg"):
        validate_reset_request(allow_data_loss=True, confirm_database="unsafe.db")


def test_clean_baseline_is_one_root_and_excludes_other_schema_owners() -> None:
    root = Path(__file__).resolve().parent.parent
    scripts = ScriptDirectory.from_config(Config(str(root / "alembic.ini")))
    assert scripts.get_heads() == [ALEMBIC_HEAD]
    head = scripts.get_revision(ALEMBIC_HEAD)
    assert head is not None and head.down_revision == "0012_jmc6h"
    baseline = scripts.get_revision("0001_jmc1")
    assert baseline is not None and baseline.down_revision is None

    for revision in scripts.walk_revisions():
        source = Path(revision.path).read_text(encoding="utf-8")
        for table in PGQUEUER_TABLES | EXCLUDED_DEPLOYMENT_TABLES:
            assert f"create_table('{table}'" not in source


def test_pgqueuer_cli_keeps_password_out_of_process_arguments(monkeypatch) -> None:
    monkeypatch.setattr(
        settings,
        "DB_URL",
        "postgresql+asyncpg://marquee:process-secret@db.example:5432/marquee",
    )
    captured = {}

    def fake_run(arguments, **kwargs):
        captured["arguments"] = arguments
        captured["env"] = kwargs["env"]
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(migration.subprocess, "run", fake_run)
    migration._run_pgqueuer_cli("verify", "--expect", "present")

    assert "process-secret" not in " ".join(captured["arguments"])
    assert captured["env"]["PGPASSWORD"] == "process-secret"


@pytest.mark.asyncio
async def test_reset_is_repeatable_and_database_only(
    owned_jmc1_database: _OwnedDatabase,
) -> None:
    marker = settings.data_dir_path / "jmc1-reset-must-not-delete"
    marker.write_text("preserve", encoding="utf-8")

    first = await reset_development_database(
        allow_data_loss=True,
        confirm_database=owned_jmc1_database.name,
    )
    second = await reset_development_database(
        allow_data_loss=True,
        confirm_database=owned_jmc1_database.name,
    )

    assert first == second
    assert marker.read_text(encoding="utf-8") == "preserve"

    connection = await owned_jmc1_database.connect()
    try:
        revision = await connection.fetchval("SELECT version_num FROM alembic_version")
        assert revision == ALEMBIC_HEAD
        table_rows = await connection.fetch(
            """
            SELECT tablename FROM pg_catalog.pg_tables
            WHERE schemaname = current_schema()
            """
        )
        tables = {row["tablename"] for row in table_rows}
        assert tables == set(get_deployment_metadata().tables) | PGQUEUER_TABLES | {
            "alembic_version"
        }
        assert not (EXCLUDED_DEPLOYMENT_TABLES & tables)
        markers = await connection.fetch(
            "SELECT component, expected_version, durability FROM schema_contracts"
        )
        assert {tuple(row.values()) for row in markers} == {
            ("marquee", ALEMBIC_HEAD, None),
            ("pgqueuer", "1.1.1", "durable"),
        }
    finally:
        await connection.close()


@pytest.mark.asyncio
async def test_migration_refuses_partial_pgqueuer_install(
    owned_jmc1_database: _OwnedDatabase,
) -> None:
    connection = await owned_jmc1_database.connect()
    try:
        await connection.execute("CREATE TABLE pgqueuer (id bigint PRIMARY KEY)")
    finally:
        await connection.close()

    with pytest.raises(MigrationError, match="partial PgQueuer installation"):
        await migrate_database()


@pytest.mark.asyncio
async def test_reset_refuses_active_marquee_role_connection(
    owned_jmc1_database: _OwnedDatabase,
) -> None:
    await reset_development_database(
        allow_data_loss=True,
        confirm_database=owned_jmc1_database.name,
    )
    api_connection = await owned_jmc1_database.connect("marquee:api")
    try:
        with pytest.raises(MigrationError, match="marquee:api"):
            await reset_development_database(
                allow_data_loss=True,
                confirm_database=owned_jmc1_database.name,
            )
        assert await api_connection.fetchval("SELECT count(*) FROM schema_contracts") == 2
    finally:
        await api_connection.close()


@pytest.mark.asyncio
async def test_reset_refuses_held_migration_lock(
    owned_jmc1_database: _OwnedDatabase,
) -> None:
    locker = await owned_jmc1_database.connect()
    await locker.fetchval("SELECT pg_advisory_lock($1)", migration.MIGRATION_ADVISORY_LOCK_ID)
    try:
        with pytest.raises(MigrationError, match="already held"):
            await reset_development_database(
                allow_data_loss=True,
                confirm_database=owned_jmc1_database.name,
            )
    finally:
        await locker.fetchval("SELECT pg_advisory_unlock($1)", migration.MIGRATION_ADVISORY_LOCK_ID)
        await locker.close()


@pytest.mark.asyncio
async def test_pgqueuer_presence_is_scoped_to_target_schema(
    owned_jmc1_database: _OwnedDatabase,
) -> None:
    public_connection = await owned_jmc1_database.connect()
    await public_connection.execute("CREATE SCHEMA other_pgq")
    other_connection = await asyncpg.connect(
        asyncpg_dsn(owned_jmc1_database.url),
        server_settings={"search_path": "other_pgq"},
    )
    queries = Queries.from_asyncpg_connection(other_connection)
    await queries.install()
    try:
        assert await pgqueuer_install_state(public_connection) == "absent"
    finally:
        await queries.uninstall()
        await other_connection.close()
        await public_connection.execute("DROP SCHEMA other_pgq CASCADE")
        await public_connection.close()


@pytest.mark.parametrize(
    "mutation",
    [
        "UPDATE alembic_version SET version_num = 'wrong_head'",
        """
        UPDATE schema_contracts
        SET catalog_fingerprint = \'wrong-fingerprint\'
        WHERE component = \'pgqueuer\'
        """,
        "ALTER TABLE pgqueuer SET UNLOGGED",
        "DROP INDEX pgqueuer_heartbeat_id_id1_idx",
        "DROP TRIGGER tg_pgqueuer_changed ON pgqueuer",
        """
        DROP TRIGGER tg_pgqueuer_changed ON pgqueuer;
        DROP FUNCTION fn_pgqueuer_changed();
        """,
    ],
)
async def test_runtime_schema_verification_rejects_incompatible_catalogs(
    owned_jmc1_database: _OwnedDatabase,
    mutation: str,
) -> None:
    await reset_development_database(
        allow_data_loss=True,
        confirm_database=owned_jmc1_database.name,
    )
    connection = await owned_jmc1_database.connect()
    try:
        await verify_runtime_schema(connection)
        await connection.execute(mutation)
        with pytest.raises(MigrationError):
            await verify_runtime_schema(connection)
    finally:
        await connection.close()
