"""Migration service, clean baseline, and guarded database reset.

The schema has one root revision and excludes other schema owners. Covers PgQueuer install
verification, credentials never reaching process arguments, runtime schema verification
rejecting incompatible catalogs, and the reset guards — environment, explicit data-loss
acknowledgement, exact database confirmation, active connections, and a held migration
lock."""

from __future__ import annotations

import asyncio
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
from alembic import command
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
async def owned_database(monkeypatch) -> AsyncIterator[_OwnedDatabase]:
    admin_url = database_url()
    name = f"migration_{uuid.uuid4().hex}"
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
            "WHERE datname = $1 AND pid <> pg_backend_pid() "
            "AND backend_type = 'client backend'",
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
    baseline = scripts.get_revision("0001_jmc1")
    assert baseline is not None and baseline.down_revision is None

    # One unbranched line from that baseline to the declared head: every revision
    # except the baseline has exactly one parent, and the chain reaches the root.
    revisions = list(scripts.walk_revisions())
    assert {revision.revision for revision in revisions} == {
        revision.revision for revision in scripts.walk_revisions(base="base", head=ALEMBIC_HEAD)
    }
    for revision in revisions:
        parents = revision.down_revision
        if revision.revision == baseline.revision:
            assert parents is None
        else:
            assert isinstance(parents, str), f"{revision.revision} has a branched parent"

    for revision in scripts.walk_revisions():
        source = Path(revision.path).read_text(encoding="utf-8")
        for table in PGQUEUER_TABLES | EXCLUDED_DEPLOYMENT_TABLES:
            assert f"create_table('{table}'" not in source


@pytest.mark.asyncio
async def test_work_item_migration_upgrades_existing_jobs_and_downgrades_cleanly(
    owned_database: _OwnedDatabase,
) -> None:
    root = Path(__file__).resolve().parent.parent
    config = Config(str(root / "alembic.ini"))
    await asyncio.to_thread(command.upgrade, config, "0019_pipeline_subject_key")

    job_id = uuid.uuid4().hex
    connection = await owned_database.connect()
    try:
        await connection.execute(
            """
            INSERT INTO jobs (
                id, type, payload_version, request, priority, root_id, subject_snapshot
            ) VALUES ($1, 'poster_pipeline_group', 1, '{}'::json, 50, $1, '{}'::json)
            """,
            job_id,
        )
        attempt_id = await connection.fetchval(
            """
            INSERT INTO job_attempts (job_id, number, fence_token)
            VALUES ($1, 1, 1) RETURNING id
            """,
            job_id,
        )
    finally:
        await connection.close()

    await asyncio.to_thread(command.upgrade, config, ALEMBIC_HEAD)
    await asyncio.to_thread(command.check, config)
    connection = await owned_database.connect()
    try:
        job = await connection.fetchrow(
            """
            SELECT work_item_sequence, work_item_summary, work_item_updated_at
            FROM jobs WHERE id = $1
            """,
            job_id,
        )
        assert job is not None
        assert tuple(job.values()) == (0, None, None)
        await connection.execute(
            """
            INSERT INTO job_work_items (
                job_id, subject_key, ordinal, subject_kind, subject_snapshot,
                attempt_id, fence_token, status
            ) VALUES ($1, 'movie:1', 0, 'movie', '{}'::json, $2, 1, 'pending')
            """,
            job_id,
            attempt_id,
        )
        assert (
            await connection.fetchval("SELECT status FROM job_work_items WHERE job_id = $1", job_id)
            == "pending"
        )
    finally:
        await connection.close()

    await asyncio.to_thread(command.downgrade, config, "0019_pipeline_subject_key")
    connection = await owned_database.connect()
    try:
        assert await connection.fetchval("SELECT to_regclass('job_work_items')") is None
        assert (
            await connection.fetchval(
                """
                SELECT count(*)
                FROM information_schema.columns
                WHERE table_schema = current_schema()
                  AND table_name = 'jobs'
                  AND column_name LIKE 'work_item%'
                """
            )
            == 0
        )
    finally:
        await connection.close()


@pytest.mark.asyncio
async def test_subject_key_migration_backfills_and_refuses_grouped_downgrade(
    owned_database: _OwnedDatabase,
) -> None:
    root = Path(__file__).resolve().parent.parent
    config = Config(str(root / "alembic.ini"))
    await asyncio.to_thread(command.upgrade, config, "0018_season_series")

    job_id = uuid.uuid4().hex
    connection = await owned_database.connect()
    try:
        await connection.execute(
            """
            INSERT INTO jobs (
                id, type, payload_version, request, priority, root_id, subject_snapshot
            ) VALUES ($1, 'poster_pipeline', 1, '{}'::json, 50, $1, '{}'::json)
            """,
            job_id,
        )
        attempt_id = await connection.fetchval(
            """
            INSERT INTO job_attempts (job_id, number, fence_token)
            VALUES ($1, 1, 1) RETURNING id
            """,
            job_id,
        )
        artifact_id = await connection.fetchval(
            """
            INSERT INTO job_artifacts (
                job_id, attempt_id, kind, name, storage_key
            ) VALUES ($1, $2, 'command_report', 'legacy.json', 'legacy/run.json')
            RETURNING id
            """,
            job_id,
            attempt_id,
        )
        await connection.execute(
            """
            INSERT INTO pipeline_runs (
                run_id, media_type, subject_snapshot, status,
                job_id, attempt_id, fence_token, archive_artifact_id
            ) VALUES (
                'legacy-run', 'movie', '{"display_id":"series:42"}'::json,
                'completed', $1, $2, 1, $3
            )
            """,
            job_id,
            attempt_id,
            artifact_id,
        )
    finally:
        await connection.close()

    await asyncio.to_thread(command.upgrade, config, ALEMBIC_HEAD)
    connection = await owned_database.connect()
    try:
        assert (
            await connection.fetchval(
                "SELECT subject_key FROM pipeline_runs WHERE run_id = 'legacy-run'"
            )
            == "series:42"
        )
        await connection.execute(
            """
            INSERT INTO pipeline_runs (
                run_id, media_type, subject_snapshot, subject_key, status,
                job_id, attempt_id, fence_token, archive_artifact_id
            ) VALUES (
                'grouped-run', 'movie', '{}'::json, 'season:9', 'failed',
                $1, $2, 1, $3
            )
            """,
            job_id,
            attempt_id,
            artifact_id,
        )
    finally:
        await connection.close()

    with pytest.raises(RuntimeError, match="cannot downgrade"):
        await asyncio.to_thread(command.downgrade, config, "0018_season_series")

    connection = await owned_database.connect()
    try:
        assert await connection.fetchval("SELECT version_num FROM alembic_version") == ALEMBIC_HEAD
        assert await connection.fetchval("SELECT count(*) FROM pipeline_runs") == 2
    finally:
        await connection.close()


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
    owned_database: _OwnedDatabase,
) -> None:
    marker = settings.data_dir_path / "reset-must-not-delete"
    marker.write_text("preserve", encoding="utf-8")

    first = await reset_development_database(
        allow_data_loss=True,
        confirm_database=owned_database.name,
    )
    second = await reset_development_database(
        allow_data_loss=True,
        confirm_database=owned_database.name,
    )

    assert first == second
    assert marker.read_text(encoding="utf-8") == "preserve"

    connection = await owned_database.connect()
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
        subject_key = await connection.fetchrow(
            """
            SELECT is_nullable, character_maximum_length
            FROM information_schema.columns
            WHERE table_schema = current_schema()
              AND table_name = 'pipeline_runs'
              AND column_name = 'subject_key'
            """
        )
        assert subject_key is not None
        assert tuple(subject_key.values()) == ("NO", 200)
        constraint = await connection.fetchval(
            """
            SELECT pg_get_constraintdef(oid)
            FROM pg_constraint
            WHERE conrelid = 'pipeline_runs'::regclass
              AND conname = 'uq_pipeline_runs_job_subject'
            """
        )
        assert constraint == "UNIQUE (job_id, subject_key)"
        assert (
            await connection.fetchval(
                """
                SELECT count(*)
                FROM pg_constraint
                WHERE conrelid = 'pipeline_runs'::regclass
                  AND conname = 'uq_pipeline_runs_job_id'
                """
            )
            == 0
        )
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
    owned_database: _OwnedDatabase,
) -> None:
    connection = await owned_database.connect()
    try:
        await connection.execute("CREATE TABLE pgqueuer (id bigint PRIMARY KEY)")
    finally:
        await connection.close()

    with pytest.raises(MigrationError, match="partial PgQueuer installation"):
        await migrate_database()


@pytest.mark.asyncio
async def test_reset_refuses_active_marquee_role_connection(
    owned_database: _OwnedDatabase,
) -> None:
    await reset_development_database(
        allow_data_loss=True,
        confirm_database=owned_database.name,
    )
    api_connection = await owned_database.connect("marquee:api")
    try:
        with pytest.raises(MigrationError, match="marquee:api"):
            await reset_development_database(
                allow_data_loss=True,
                confirm_database=owned_database.name,
            )
        assert await api_connection.fetchval("SELECT count(*) FROM schema_contracts") == 2
    finally:
        await api_connection.close()


@pytest.mark.asyncio
async def test_reset_refuses_held_migration_lock(
    owned_database: _OwnedDatabase,
) -> None:
    locker = await owned_database.connect()
    await locker.fetchval("SELECT pg_advisory_lock($1)", migration.MIGRATION_ADVISORY_LOCK_ID)
    try:
        with pytest.raises(MigrationError, match="already held"):
            await reset_development_database(
                allow_data_loss=True,
                confirm_database=owned_database.name,
            )
    finally:
        await locker.fetchval("SELECT pg_advisory_unlock($1)", migration.MIGRATION_ADVISORY_LOCK_ID)
        await locker.close()


@pytest.mark.asyncio
async def test_pgqueuer_presence_is_scoped_to_target_schema(
    owned_database: _OwnedDatabase,
) -> None:
    public_connection = await owned_database.connect()
    await public_connection.execute("CREATE SCHEMA other_pgq")
    other_connection = await asyncpg.connect(
        asyncpg_dsn(owned_database.url),
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
    owned_database: _OwnedDatabase,
    mutation: str,
) -> None:
    await reset_development_database(
        allow_data_loss=True,
        confirm_database=owned_database.name,
    )
    connection = await owned_database.connect()
    try:
        await verify_runtime_schema(connection)
        await connection.execute(mutation)
        with pytest.raises(MigrationError):
            await verify_runtime_schema(connection)
    finally:
        await connection.close()


# --- evidence root rename (jmc3 -> jobs) -------------------------------------


def _seed(root: Path, relative: str, content: str) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return path


def test_evidence_root_move_is_a_plain_rename_when_only_the_legacy_root_exists(
    tmp_path: Path,
) -> None:
    _seed(tmp_path, "jmc3/evidence/logs/job/1/segment-0.jsonl", "log")
    _seed(tmp_path, "jmc3/workspaces/job/1-1/run.json", "run")

    assert migration.migrate_evidence_root(tmp_path) == "moved"

    assert (tmp_path / "jobs/evidence/logs/job/1/segment-0.jsonl").read_text() == "log"
    assert (tmp_path / "jobs/workspaces/job/1-1/run.json").read_text() == "run"
    assert not (tmp_path / "jmc3").exists()


def test_evidence_root_move_is_idempotent_and_reports_the_absent_case(tmp_path: Path) -> None:
    assert migration.migrate_evidence_root(tmp_path) == "absent"
    _seed(tmp_path, "jobs/evidence/logs/job/1/segment-0.jsonl", "log")
    assert migration.migrate_evidence_root(tmp_path) == "current"
    assert (tmp_path / "jobs/evidence/logs/job/1/segment-0.jsonl").read_text() == "log"


def test_interrupted_move_merges_nested_files_without_overwriting_migrated_ones(
    tmp_path: Path,
) -> None:
    """An interrupted move leaves both roots. Deeply nested legacy files must still
    arrive, the already-migrated copy must win, and the losing legacy file must be
    left behind for inspection rather than deleted."""
    _seed(tmp_path, "jobs/evidence/logs/job/1/segment-0.jsonl", "migrated")
    _seed(tmp_path, "jmc3/evidence/logs/job/1/segment-0.jsonl", "legacy-conflict")
    _seed(tmp_path, "jmc3/evidence/logs/late/1/segment-0.jsonl", "late")
    _seed(tmp_path, "jmc3/evidence/artifacts/deep/a/b/artifact.json", "deep")
    _seed(tmp_path, "jmc3/workspaces/job/1-1/run.json", "run")

    result = migration.migrate_evidence_root(tmp_path)

    assert result.startswith("merged with 1 legacy file(s)")
    # Nesting deeper than one level is carried across.
    assert (tmp_path / "jobs/evidence/logs/late/1/segment-0.jsonl").read_text() == "late"
    assert (tmp_path / "jobs/evidence/artifacts/deep/a/b/artifact.json").read_text() == "deep"
    assert (tmp_path / "jobs/workspaces/job/1-1/run.json").read_text() == "run"
    # The migrated copy wins and the conflicting legacy file survives untouched.
    assert (tmp_path / "jobs/evidence/logs/job/1/segment-0.jsonl").read_text() == "migrated"
    assert (tmp_path / "jmc3/evidence/logs/job/1/segment-0.jsonl").read_text() == "legacy-conflict"
    # Re-running changes nothing further.
    assert migration.migrate_evidence_root(tmp_path) == result
