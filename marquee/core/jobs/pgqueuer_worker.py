"""Worker-only PgQueuer process for manifest-owned execution entrypoints."""

from __future__ import annotations

import asyncio
import logging
import signal
from datetime import timedelta

import asyncpg
from pgqueuer import PgQueuer
from pgqueuer.models import Context
from pgqueuer.models import Job as PgQueuerJob

from marquee.config import settings
from marquee.core.configuration_cache import configuration_provider
from marquee.core.jobs.delivery import deliver_job
from marquee.core.jobs.manifest import JOB_DEFINITION_REGISTRY
from marquee.core.jobs.orphan_reconciliation import reconcile_startup_orphans
from marquee.core.jobs.runtime_instances import RuntimeInstanceHandle, capability_snapshot
from marquee.core.jobs.workspaces import reconcile_stale_workspaces
from marquee.db_migration import asyncpg_dsn, verify_runtime_schema

logger = logging.getLogger(__name__)


def entrypoint_concurrency_limits() -> dict[str, int]:
    """Return configured, locally supported PgQueuer execution-class limits."""
    limits = {
        "control": settings.JOB_CONTROL_CONCURRENCY,
        "network": settings.JOB_NETWORK_CONCURRENCY,
        "cpu": settings.JOB_CPU_CONCURRENCY,
        "media_read": settings.JOB_MEDIA_READ_CONCURRENCY,
        "media_write": settings.JOB_MEDIA_WRITE_CONCURRENCY,
        "gpu": settings.JOB_GPU_CONCURRENCY,
        "maintenance": settings.JOB_MAINTENANCE_CONCURRENCY,
    }
    configured = {
        value.strip() for value in settings.JOB_WORKER_ENTRYPOINTS.split(",") if value.strip()
    }
    unknown = configured - set(limits)
    if unknown:
        raise RuntimeError(f"unknown configured worker entrypoints: {sorted(unknown)}")
    supported = {
        definition.entrypoint for definition in JOB_DEFINITION_REGISTRY if definition.enabled
    }
    return {name: limit for name, limit in limits.items() if name in configured & supported}


def _entrypoint_callback(expected_entrypoint: str, *, runtime_instance_id: str | None = None):
    async def callback(job: PgQueuerJob, context: Context) -> None:
        await deliver_job(
            job,
            context,
            expected_entrypoint=expected_entrypoint,
            runtime_instance_id=runtime_instance_id,
        )

    callback.__name__ = expected_entrypoint
    return callback


def create_worker(
    connection: asyncpg.Connection, *, runtime_instance_id: str | None = None
) -> PgQueuer:
    """Build one worker whose entrypoints all call the same fenced delivery kernel."""
    app = PgQueuer.from_asyncpg_connection(connection)

    for entrypoint, concurrency_limit in entrypoint_concurrency_limits().items():
        app.entrypoint(
            entrypoint,
            concurrency_limit=concurrency_limit,
            accepts_context=True,
            on_failure="hold",
        )(_entrypoint_callback(entrypoint, runtime_instance_id=runtime_instance_id))

    return app


def _install_shutdown_handlers(app: PgQueuer) -> None:
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(signum, app.shutdown.set)
        except NotImplementedError:  # pragma: no cover - Windows fallback
            signal.signal(signum, lambda *_: app.shutdown.set())


async def run() -> None:
    """Run only PgQueuer's queue manager; never run schedules in this process."""
    connection = await asyncpg.connect(
        asyncpg_dsn(),
        server_settings={"application_name": "marquee:worker:pgqueuer"},
    )
    runtime: RuntimeInstanceHandle | None = None
    try:
        await verify_runtime_schema(connection)
        await configuration_provider.start(role="worker")
        entrypoints = entrypoint_concurrency_limits()
        runtime = RuntimeInstanceHandle(
            role="worker",
            node_label=settings.JOB_WORKER_NODE_ID,
            advertised_entrypoints=entrypoints,
            capabilities=capability_snapshot(entrypoints),
        )
        await runtime.start()
        reconciliation = await reconcile_startup_orphans(
            worker_node=settings.JOB_WORKER_NODE_ID,
            cooperative_seconds=settings.JOB_PROCESS_COOPERATIVE_SECONDS,
            term_seconds=settings.JOB_PROCESS_TERM_SECONDS,
        )
        if reconciliation["unsafe"]:
            logger.error(
                "startup orphan reconciliation quarantined %s attempts", reconciliation["unsafe"]
            )
        workspaces = await reconcile_stale_workspaces(settings.DATA_DIR)
        if workspaces["quarantined"]:
            logger.error(
                "startup workspace reconciliation quarantined %s workspaces",
                workspaces["quarantined"],
            )
        from marquee.core.jobs.artifact_service import (
            reconcile_artifacts,
            repair_terminal_virtual_artifacts,
        )
        from marquee.core.jobs.log_capture import recover_abandoned_attempt_logs

        logs = await recover_abandoned_attempt_logs(
            worker_node=settings.JOB_WORKER_NODE_ID,
            data_dir=settings.DATA_DIR,
        )
        if logs["failed"] or logs["deferred"]:
            logger.error("startup attempt-log reconciliation: %s", logs)
        artifacts = await reconcile_artifacts(data_dir=settings.DATA_DIR)
        if artifacts["missing"] or artifacts["untracked"]:
            logger.error("startup artifact reconciliation: %s", artifacts)
        terminal_artifacts = await repair_terminal_virtual_artifacts()
        if terminal_artifacts["failed"]:
            logger.error("startup terminal artifact repair: %s", terminal_artifacts)
        await runtime.ready()
        app = create_worker(connection, runtime_instance_id=runtime.instance_id)
        _install_shutdown_handlers(app)
        await app.qm.run(
            dequeue_timeout=timedelta(seconds=settings.JOB_PGQUEUER_DEQUEUE_SECONDS),
            batch_size=settings.JOB_PGQUEUER_BATCH_SIZE,
            max_concurrent_tasks=settings.JOB_WORKER_CONCURRENCY,
            shutdown_on_listener_failure=True,
            heartbeat_timeout=timedelta(seconds=settings.JOB_PGQUEUER_HEARTBEAT_SECONDS),
        )
    finally:
        if runtime is not None:
            await runtime.stop()
        await configuration_provider.stop()
        await connection.close()


def main() -> None:
    logging.basicConfig(
        level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    asyncio.run(run())


if __name__ == "__main__":
    main()
