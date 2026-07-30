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
from marquee.core.jobs.batches import (
    MAX_BATCH_FAILURE_ITEMS,
    MAX_DYNAMIC_CHILDREN,
    MAX_FIXED_CHILDREN,
)
from marquee.core.jobs.inventory import BUILTIN_JOB_TYPES
from marquee.core.jobs.manifest import ENABLED_JOB_TYPES, JOB_DEFINITION_REGISTRY
from marquee.core.jobs.pgqueuer_worker import entrypoint_concurrency_limits
from marquee.core.jobs.process_identity import containment_capabilities
from marquee.core.jobs.schedules import (
    ACTIVATED_SCHEDULE_KEYS,
    MAX_SCHEDULE_DIAGNOSTICS,
    PRODUCTION_SCHEDULE_CATALOG,
    load_schedule_configuration,
    schedule_effective_state,
)
from marquee.database import _get_engine
from marquee.db_migration import (
    MIGRATION_ADVISORY_LOCK_ID,
    PGQUEUER_VERSION,
    MigrationError,
    verify_runtime_schema,
)
from marquee.models import JobBatch


def connection_budget_report() -> dict[str, Any]:
    """Return documented role arithmetic without DSNs or connection identities."""
    api_pool = settings.DB_API_POOL_SIZE + settings.DB_API_MAX_OVERFLOW
    worker_each = (
        settings.DB_WORKER_POOL_SIZE
        + settings.DB_WORKER_MAX_OVERFLOW
        + 1
        + settings.JOB_SAFETY_GATE_CONNECTIONS
    )
    scheduler = settings.DB_SCHEDULER_POOL_SIZE + settings.DB_SCHEDULER_MAX_OVERFLOW + 1
    configured = settings.deployment_connection_budget
    maximum = settings.DB_DEPLOYMENT_MAX_CONNECTIONS
    return {
        "api": api_pool,
        "api_event_listener": settings.JOB_EVENT_LISTENER_CONNECTIONS,
        "worker_each": worker_each,
        "worker_processes": settings.JOB_EMBEDDED_WORKER_COUNT,
        "safety_gate_sessions_each": settings.JOB_SAFETY_GATE_CONNECTIONS,
        "scheduler": scheduler,
        "migration": settings.DB_MIGRATION_CONNECTIONS,
        "configured": configured,
        "maximum": maximum,
        "within_budget": configured <= maximum,
    }


def configuration_compatible() -> bool:
    budget = connection_budget_report()
    entrypoint_limits = entrypoint_concurrency_limits()
    return (
        budget["within_budget"]
        and settings.JOB_PGQUEUER_BATCH_SIZE >= 1
        and settings.JOB_PGQUEUER_BATCH_SIZE <= settings.JOB_WORKER_CONCURRENCY
        and settings.JOB_SAFETY_GATE_CONNECTIONS >= settings.JOB_WORKER_CONCURRENCY
        and all(limit >= 1 for limit in entrypoint_limits.values())
        and settings.JOB_PGQUEUER_HEARTBEAT_SECONDS > 0
        and settings.JOB_PGQUEUER_DEQUEUE_SECONDS > 0
    )


def package_compatible() -> bool:
    try:
        return importlib.metadata.version("pgqueuer") == PGQUEUER_VERSION
    except importlib.metadata.PackageNotFoundError:
        return False


_ONNX_RUNTIME_DISTRIBUTIONS = (
    "onnxruntime",
    "onnxruntime-gpu",
    "onnxruntime-openvino",
)


def inference_runtime_report() -> dict[str, Any]:
    """Report whether one ONNX Runtime distribution can honor the provider request."""
    installed: dict[str, str] = {}
    for distribution in _ONNX_RUNTIME_DISTRIBUTIONS:
        try:
            installed[distribution] = importlib.metadata.version(distribution)
        except importlib.metadata.PackageNotFoundError:
            continue

    report: dict[str, Any] = {
        "status": "incompatible",
        "requested_provider": "unavailable",
        "installed_distributions": installed,
        "available_providers": [],
        "selected_provider": None,
        "hardware_tier": None,
    }
    try:
        from marquee.ml import hardware

        requested = hardware.resolve_provider_request(
            hardware.pipeline_settings.EXECUTION_PROVIDER
        )
        available = list(hardware.ort.get_available_providers())
        report["requested_provider"] = requested or "auto"
        report["available_providers"] = available
        profile = hardware.detect_hardware()
    except (AttributeError, ImportError, OSError, RuntimeError, TypeError, ValueError):
        return report

    selected = profile.providers[0] if profile.providers else None
    report["selected_provider"] = selected
    report["hardware_tier"] = profile.tier
    owns_runtime_module = len(installed) == 1
    selected_is_available = selected is not None and selected in available
    requested_is_selected = requested is None or selected == requested
    if owns_runtime_module and selected_is_available and requested_is_selected:
        report["status"] = "ok"
    return report


def registry_compatible() -> bool:
    try:
        JOB_DEFINITION_REGISTRY.validate_complete(BUILTIN_JOB_TYPES)
    except (TypeError, ValueError, RuntimeError):
        return False
    registered_entrypoints = frozenset(entrypoint_concurrency_limits())
    return JOB_DEFINITION_REGISTRY.enabled_types == ENABLED_JOB_TYPES and all(
        definition.entrypoint in registered_entrypoints for definition in JOB_DEFINITION_REGISTRY
    )


def worker_entrypoint_report() -> dict[str, Any]:
    limits = entrypoint_concurrency_limits()
    enabled = sorted(
        {definition.entrypoint for definition in JOB_DEFINITION_REGISTRY if definition.enabled}
    )
    return {
        "status": "ok" if registry_compatible() else "incompatible",
        "registered": sorted(limits),
        "enabled": enabled,
        "limits": {key: limits[key] for key in sorted(limits)},
        "worker_global_limit": settings.JOB_WORKER_CONCURRENCY,
        "dequeue_batch_size": settings.JOB_PGQUEUER_BATCH_SIZE,
        "media_write_product_available": True,
    }


def schedule_catalog_report() -> dict[str, Any]:
    definitions = tuple(PRODUCTION_SCHEDULE_CATALOG)
    configuration = load_schedule_configuration()
    compatible = all(
        (job_definition := JOB_DEFINITION_REGISTRY.find(definition.produced_job_type)) is not None
        and definition.trigger in job_definition.trigger_kinds
        for definition in definitions
    )
    schedules = []
    for definition in definitions:
        effective = schedule_effective_state(definition, configuration)
        schedules.append(
            {
                "key": definition.key,
                "entrypoint": definition.entrypoint,
                "registered": effective.registered,
                "individually_activated": effective.individually_activated,
                "configured": effective.configured,
                "effectively_enabled": effective.effectively_enabled,
                "disabled_reason": effective.disabled_reason,
            }
        )
    return {
        "status": "ok" if compatible else "incompatible",
        "definition_count": len(definitions),
        "keys": sorted(definition.key for definition in definitions),
        "entrypoints": sorted(definition.entrypoint for definition in definitions),
        "occurrence_policies": sorted(
            {definition.occurrence_policy.value for definition in definitions}
        ),
        "production_schedules_enabled": configuration.production_occurrences_enabled,
        "activated_keys": sorted(
            definition.key
            for definition in definitions
            if definition.key in ACTIVATED_SCHEDULE_KEYS
        ),
        "schedules": schedules,
        "diagnostic_limit": MAX_SCHEDULE_DIAGNOSTICS,
    }


def batch_projection_report() -> dict[str, Any]:
    required_columns = {
        "parent_job_id",
        "generation",
        "sealed",
        "created_total",
        "terminal_total",
        "projection_sequence",
        "failure_summary",
        "attention_summary",
    }
    columns = {column.name for column in JobBatch.__table__.columns}
    return {
        "status": "ok" if required_columns <= columns else "incompatible",
        "version": 1,
        "fixed_child_cap": MAX_FIXED_CHILDREN,
        "dynamic_child_cap": MAX_DYNAMIC_CHILDREN,
        "failure_summary_cap": MAX_BATCH_FAILURE_ITEMS,
        "required_projection_fields": sorted(required_columns),
    }


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
    from marquee.core.jobs.artifact_service import ensure_artifact_root
    from marquee.core.jobs.event_stream import job_event_tailer
    from marquee.core.jobs.log_capture import AttemptLogFiles

    containment = containment_capabilities()
    event_stream = job_event_tailer.health()
    try:
        writable = AttemptLogFiles.for_data_dir(settings.DATA_DIR).ensure_writable()
        log_storage_status = "ok" if writable else "unavailable"
    except (OSError, RuntimeError, ValueError):
        log_storage_status = "unavailable"
    try:
        ensure_artifact_root(settings.DATA_DIR)
        artifact_storage_status = "ok"
    except (OSError, RuntimeError, ValueError):
        artifact_storage_status = "unavailable"
    components: dict[str, dict[str, Any]] = {
        "configuration": {"status": "ok" if configuration_compatible() else "incompatible"},
        "package": {"status": "ok" if package_compatible() else "incompatible"},
        "inference_runtime": inference_runtime_report(),
        "definition_registry": {"status": "ok" if registry_compatible() else "incompatible"},
        "worker_entrypoints": worker_entrypoint_report(),
        "schedule_catalog": schedule_catalog_report(),
        "batch_projection": batch_projection_report(),
        "connection_budget": {
            "status": "ok" if connection_budget_report()["within_budget"] else "incompatible",
            **connection_budget_report(),
        },
        "database": {"status": "unavailable"},
        "migration_lock": {"status": "unavailable"},
        "schema": {"status": "unavailable"},
        "safety_gates": {"status": "unavailable"},
        "event_stream": event_stream,
        "attempt_logs": {"status": log_storage_status},
        "job_artifacts": {"status": artifact_storage_status},
        "process_containment": {
            "status": "ok",
            **containment.public(),
        },
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
            components["safety_gates"]["status"] = "ok"
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
            with contextlib.suppress(OSError, asyncpg.PostgresError, RuntimeError, SQLAlchemyError):
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
