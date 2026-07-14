"""Bounded, sanitized JMC1 dependency readiness."""

from __future__ import annotations

import asyncio
import contextlib
import importlib.metadata
from typing import Any

import asyncpg
from sqlalchemy.exc import SQLAlchemyError

from marquee.config import settings
from marquee.core.configuration_cache import configuration_provider
from marquee.database import _get_engine
from marquee.db_migration import (
    MIGRATION_ADVISORY_LOCK_ID,
    PGQUEUER_VERSION,
    MigrationError,
    verify_runtime_schema,
)


def connection_budget_report() -> dict[str, Any]:
    """Return documented role arithmetic without DSNs or connection identities."""
    api = settings.DB_API_POOL_SIZE + settings.DB_API_MAX_OVERFLOW
    worker_each = settings.DB_WORKER_POOL_SIZE + settings.DB_WORKER_MAX_OVERFLOW + 1
    scheduler = settings.DB_SCHEDULER_POOL_SIZE + settings.DB_SCHEDULER_MAX_OVERFLOW + 1
    configured = settings.deployment_connection_budget
    maximum = settings.DB_DEPLOYMENT_MAX_CONNECTIONS
    return {
        "api": api,
        "worker_each": worker_each,
        "worker_processes": settings.JOB_EMBEDDED_WORKER_COUNT,
        "scheduler": scheduler,
        "migration": settings.DB_MIGRATION_CONNECTIONS,
        "configured": configured,
        "maximum": maximum,
        "within_budget": configured <= maximum,
    }


def configuration_compatible() -> bool:
    budget = connection_budget_report()
    return (
        budget["within_budget"]
        and settings.JOB_PGQUEUER_BATCH_SIZE >= 1
        and settings.JOB_CONTROL_CONCURRENCY >= 1
        and settings.JOB_PGQUEUER_HEARTBEAT_SECONDS > 0
        and settings.JOB_PGQUEUER_DEQUEUE_SECONDS > 0
    )


def package_compatible() -> bool:
    try:
        return importlib.metadata.version("pgqueuer") == PGQUEUER_VERSION
    except importlib.metadata.PackageNotFoundError:
        return False


async def _raw_pool_connection() -> tuple[Any, asyncpg.Connection]:
    sqlalchemy_connection = await _get_engine().connect()
    try:
        raw = await sqlalchemy_connection.get_raw_connection()
        driver = raw.driver_connection
        if not isinstance(driver, asyncpg.Connection):
            raise RuntimeError("readiness requires the asyncpg PostgreSQL driver")
        return sqlalchemy_connection, driver
    except BaseException:
        await sqlalchemy_connection.close()
        raise


async def check_readiness() -> dict[str, Any]:
    """Check every mandatory JMC1 dependency within one global timeout."""
    components: dict[str, dict[str, str]] = {
        "configuration": {
            "status": "ok" if configuration_compatible() else "incompatible"
        },
        "package": {
            "status": "ok" if package_compatible() else "incompatible"
        },
        "database": {"status": "unavailable"},
        "migration_lock": {"status": "unavailable"},
        "schema": {"status": "unavailable"},
        "versioned_configuration": {
            "status": "ok"
            if configuration_provider.health()["status"] == "valid"
            else str(configuration_provider.health()["status"])
        },
    }
    sqlalchemy_connection = None
    connection: asyncpg.Connection | None = None
    acquired_lock = False
    try:
        async with asyncio.timeout(settings.HEALTH_READY_TIMEOUT_SECONDS):
            sqlalchemy_connection, connection = await _raw_pool_connection()
            components["database"]["status"] = "ok"
            acquired_lock = bool(
                await connection.fetchval(
                    "SELECT pg_try_advisory_lock($1)",
                    MIGRATION_ADVISORY_LOCK_ID,
                )
            )
            components["migration_lock"]["status"] = "ok" if acquired_lock else "held"
            if acquired_lock:
                try:
                    await verify_runtime_schema(connection)
                except (
                    MigrationError,
                    asyncpg.PostgresError,
                    importlib.metadata.PackageNotFoundError,
                ):
                    components["schema"]["status"] = "incompatible"
                else:
                    components["schema"]["status"] = "ok"
    except (TimeoutError, OSError, asyncpg.PostgresError, RuntimeError, SQLAlchemyError):
        pass
    finally:
        if sqlalchemy_connection is not None:
            if acquired_lock:
                with contextlib.suppress(
                    OSError, asyncpg.PostgresError, RuntimeError, SQLAlchemyError
                ):
                    assert connection is not None
                    await connection.fetchval(
                        "SELECT pg_advisory_unlock($1)",
                        MIGRATION_ADVISORY_LOCK_ID,
                    )
            with contextlib.suppress(
                OSError, asyncpg.PostgresError, RuntimeError, SQLAlchemyError
            ):
                await sqlalchemy_connection.close()

    ready = all(component["status"] == "ok" for component in components.values())
    return {"status": "ready" if ready else "not_ready", "components": components}


async def require_startup_readiness() -> None:
    """Retry transient startup states, then fail with a sanitized component list."""
    report: dict[str, Any] | None = None
    for attempt in range(settings.HEALTH_STARTUP_ATTEMPTS):
        report = await check_readiness()
        if report["status"] == "ready":
            return
        if report["components"]["schema"]["status"] == "incompatible":
            break
        if report["components"]["package"]["status"] == "incompatible":
            break
        if report["components"]["configuration"]["status"] == "incompatible":
            break
        if attempt + 1 < settings.HEALTH_STARTUP_ATTEMPTS:
            await asyncio.sleep(settings.HEALTH_STARTUP_RETRY_SECONDS)

    assert report is not None
    failed = sorted(
        name for name, result in report["components"].items() if result["status"] != "ok"
    )
    raise RuntimeError(f"JMC1 startup readiness failed: {', '.join(failed)}")
