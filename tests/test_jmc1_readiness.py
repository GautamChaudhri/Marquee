from __future__ import annotations

import importlib.metadata

import pytest
from pydantic import ValidationError

from marquee.config import Settings, settings
from marquee.core.jobs import readiness
from marquee.db_migration import MigrationError


class _FakeSqlAlchemyConnection:
    def __init__(self) -> None:
        self.closed = False

    async def close(self) -> None:
        self.closed = True


class _FakePgConnection:
    def __init__(self, *, lock_available: bool = True) -> None:
        self.lock_available = lock_available
        self.unlocked = False

    async def fetchval(self, query, *args):
        if "pg_try_advisory_lock" in query:
            return self.lock_available
        if "pg_advisory_unlock" in query:
            self.unlocked = True
            return True
        raise AssertionError("unexpected readiness query")


async def _install_fake_connection(monkeypatch, *, lock_available=True):
    from marquee.core.jobs.event_stream import job_event_tailer

    sqlalchemy_connection = _FakeSqlAlchemyConnection()
    pg_connection = _FakePgConnection(lock_available=lock_available)

    async def connect():
        return sqlalchemy_connection, pg_connection

    async def compatible_schema(connection):
        assert connection is pg_connection

    monkeypatch.setattr(readiness, "_raw_pool_connection", connect)
    monkeypatch.setattr(readiness, "verify_runtime_schema", compatible_schema)
    monkeypatch.setattr(job_event_tailer, "health", lambda: {"status": "ok"})
    return sqlalchemy_connection, pg_connection


async def test_healthy_readiness_checks_every_component(db, monkeypatch):
    # `db` loads the initial configuration revision so the "configuration"
    # readiness component is healthy regardless of test-collection order.
    sqlalchemy_connection, pg_connection = await _install_fake_connection(monkeypatch)
    report = await readiness.check_readiness()

    failed = {
        key: value
        for key, value in report["components"].items()
        if value["status"] != "ok"
    }
    assert report["status"] == "ready", failed
    assert {result["status"] for result in report["components"].values()} == {"ok"}
    assert sqlalchemy_connection.closed
    assert pg_connection.unlocked


async def test_readiness_reports_held_migration_lock(monkeypatch):
    await _install_fake_connection(monkeypatch, lock_available=False)
    report = await readiness.check_readiness()

    assert report["status"] == "not_ready"
    assert report["components"]["migration_lock"]["status"] == "held"
    assert report["components"]["schema"]["status"] == "unavailable"


async def test_readiness_reports_schema_incompatibility_without_details(monkeypatch):
    await _install_fake_connection(monkeypatch)

    async def incompatible_schema(connection):
        raise MigrationError("secret DSN and raw pgqueuer row")

    monkeypatch.setattr(readiness, "verify_runtime_schema", incompatible_schema)
    report = await readiness.check_readiness()

    assert report["components"]["schema"]["status"] == "incompatible"
    serialized = str(report)
    assert "secret" not in serialized
    assert "raw pgqueuer" not in serialized


async def test_readiness_reports_unreachable_database_and_recovers(db, monkeypatch):
    # `db` loads the initial configuration revision so the eventual recovery to
    # "ready" is independent of test-collection order.
    from marquee.core.jobs.event_stream import job_event_tailer

    calls = 0
    sqlalchemy_connection = _FakeSqlAlchemyConnection()
    pg_connection = _FakePgConnection()

    async def connect():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("postgresql://user:secret@internal/database")
        return sqlalchemy_connection, pg_connection

    async def compatible_schema(connection):
        return None

    monkeypatch.setattr(readiness, "_raw_pool_connection", connect)
    monkeypatch.setattr(readiness, "verify_runtime_schema", compatible_schema)
    monkeypatch.setattr(job_event_tailer, "health", lambda: {"status": "ok"})
    first = await readiness.check_readiness()
    second = await readiness.check_readiness()

    assert first["status"] == "not_ready"
    assert first["components"]["database"]["status"] == "unavailable"
    assert "secret" not in str(first)
    assert second["status"] == "ready"


async def test_readiness_reports_wrong_package_and_invalid_budget(monkeypatch):
    await _install_fake_connection(monkeypatch)
    monkeypatch.setattr(importlib.metadata, "version", lambda name: "9.9.9")
    monkeypatch.setattr(settings, "DB_DEPLOYMENT_MAX_CONNECTIONS", 1)

    report = await readiness.check_readiness()

    assert report["components"]["package"]["status"] == "incompatible"
    assert report["components"]["configuration"]["status"] == "incompatible"


async def test_startup_fails_closed_with_sanitized_components(monkeypatch):
    async def incompatible():
        return {
            "status": "not_ready",
            "components": {
                "configuration": {"status": "ok"},
                "package": {"status": "ok"},
                "database": {"status": "ok"},
                "migration_lock": {"status": "ok"},
                "schema": {"status": "incompatible"},
            },
        }

    monkeypatch.setattr(readiness, "check_readiness", incompatible)
    with pytest.raises(RuntimeError, match=r"failed: schema$"):
        await readiness.require_startup_readiness()


def test_role_connection_budget_is_enforced():
    report = readiness.connection_budget_report()
    assert report == {
        "api": 15,
        "api_event_listener": 1,
        "worker_each": 8,
        "worker_processes": 1,
        "safety_gate_sessions_each": 4,
        "scheduler": 3,
        "migration": 1,
        "configured": 28,
        "maximum": 32,
        "within_budget": True,
    }
    with pytest.raises(ValidationError, match="configured role connection budget"):
        Settings(_env_file=None, DEBUG=True, DB_DEPLOYMENT_MAX_CONNECTIONS=26)


def test_role_pool_budgets_are_explicit(monkeypatch):
    for role, expected in {
        "api": (10, 5),
        "worker": (2, 1),
        "scheduler": (1, 1),
    }.items():
        monkeypatch.setattr(settings, "MARQUEE_PROCESS_ROLE", role)
        assert settings.db_pool_budget == expected


def test_engine_creation_applies_current_role_budget(monkeypatch):
    import marquee.database as database

    captured = {}
    sentinel = object()

    def create_engine(url, **kwargs):
        captured.update(kwargs)
        return sentinel

    monkeypatch.setattr(settings, "MARQUEE_PROCESS_ROLE", "worker")
    monkeypatch.setattr(database, "_engine", None)
    monkeypatch.setattr(database, "create_async_engine", create_engine)

    assert database._get_engine() is sentinel
    assert captured["pool_size"] == settings.DB_WORKER_POOL_SIZE
    assert captured["max_overflow"] == settings.DB_WORKER_MAX_OVERFLOW
    assert captured["connect_args"]["server_settings"]["application_name"] == "marquee:worker"


@pytest.mark.parametrize(
    ("module_name", "manager_name"),
    [
        ("marquee.core.jobs.pgqueuer_worker", "qm"),
        ("marquee.core.jobs.pgqueuer_scheduler", "sm"),
    ],
)
async def test_runtime_roles_fail_before_manager_start(
    monkeypatch,
    module_name,
    manager_name,
):
    import asyncio
    import importlib

    module = importlib.import_module(module_name)

    class Connection:
        closed = False

        async def close(self):
            self.closed = True

    class Manager:
        called = False

        async def run(self, **kwargs):
            self.called = True

    connection = Connection()
    manager = Manager()
    app = type(
        "App",
        (),
        {
            "shutdown": asyncio.Event(),
            manager_name: manager,
        },
    )()

    async def connect(*args, **kwargs):
        return connection

    async def incompatible_schema(connection):
        raise MigrationError("wrong schema")

    monkeypatch.setattr(module.asyncpg, "connect", connect)
    factory_name = f"create_{'worker' if manager_name == 'qm' else 'scheduler'}"
    monkeypatch.setattr(module, factory_name, lambda conn: app)
    monkeypatch.setattr(module, "_install_shutdown_handlers", lambda app: None)
    monkeypatch.setattr(module, "verify_runtime_schema", incompatible_schema)

    with pytest.raises(MigrationError, match="wrong schema"):
        await module.run()
    assert manager.called is False
    assert connection.closed is True


async def test_api_startup_fails_closed_before_serving(db, monkeypatch):
    # `db` loads the initial configuration revision so lifespan startup reaches
    # the readiness gate regardless of test-collection order.
    import marquee.main as main_module

    async def init_database():
        return None

    async def incompatible_startup():
        raise RuntimeError("JMC1 startup readiness failed: schema")

    monkeypatch.setattr(main_module, "init_db", init_database)
    monkeypatch.setattr(main_module, "migrate_legacy_runtime_state", lambda: [])
    monkeypatch.setattr(main_module, "migrate_live_artifacts", lambda: [])
    monkeypatch.setattr(readiness, "require_startup_readiness", incompatible_startup)
    monkeypatch.setattr(settings, "RADARR_URL", "")
    monkeypatch.setattr(settings, "RADARR_API_KEY", "")
    monkeypatch.setattr(settings, "SONARR_URL", "")
    monkeypatch.setattr(settings, "SONARR_API_KEY", "")
    monkeypatch.setattr(settings, "TMDB_READ_ACCESS_TOKEN", "")

    with pytest.raises(RuntimeError, match="failed: schema"):
        async with main_module.lifespan(main_module.app):
            pytest.fail("incompatible API startup served requests")
