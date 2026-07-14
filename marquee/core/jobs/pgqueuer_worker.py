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
from marquee.core.jobs.orphan_reconciliation import reconcile_startup_orphans
from marquee.core.jobs.worker_nodes import record_worker_node
from marquee.core.jobs.workspaces import reconcile_stale_workspaces
from marquee.db_migration import asyncpg_dsn, verify_runtime_schema

logger = logging.getLogger(__name__)


def entrypoint_concurrency_limits() -> dict[str, int]:
    """Return explicit PgQueuer limits for every canonical execution class."""
    return {
        "control": settings.JOB_CONTROL_CONCURRENCY,
        "network": settings.JOB_NETWORK_CONCURRENCY,
        "cpu": settings.JOB_CPU_CONCURRENCY,
        "media_read": settings.JOB_MEDIA_READ_CONCURRENCY,
        "gpu": settings.JOB_GPU_CONCURRENCY,
        "maintenance": settings.JOB_MAINTENANCE_CONCURRENCY,
    }


def _entrypoint_callback(expected_entrypoint: str):
    async def callback(job: PgQueuerJob, context: Context) -> None:
        await deliver_job(
            job,
            context,
            expected_entrypoint=expected_entrypoint,
        )

    callback.__name__ = expected_entrypoint
    return callback


def create_worker(connection: asyncpg.Connection) -> PgQueuer:
    """Build one worker whose entrypoints all call the same fenced delivery kernel."""
    app = PgQueuer.from_asyncpg_connection(connection)

    for entrypoint, concurrency_limit in entrypoint_concurrency_limits().items():
        app.entrypoint(
            entrypoint,
            concurrency_limit=concurrency_limit,
            accepts_context=True,
            on_failure="hold",
        )(_entrypoint_callback(entrypoint))

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
    registered = False
    try:
        await verify_runtime_schema(connection)
        await configuration_provider.start(role="worker")
        await record_worker_node(settings.JOB_WORKER_NODE_ID, readiness="starting")
        registered = True
        reconciliation = await reconcile_startup_orphans(
            worker_node=settings.JOB_WORKER_NODE_ID,
            cooperative_seconds=settings.JOB_PROCESS_COOPERATIVE_SECONDS,
            term_seconds=settings.JOB_PROCESS_TERM_SECONDS,
        )
        if reconciliation["unsafe"]:
            logger.error("startup orphan reconciliation quarantined %s attempts", reconciliation["unsafe"])
        workspaces = await reconcile_stale_workspaces(settings.DATA_DIR)
        if workspaces["quarantined"]:
            logger.error(
                "startup workspace reconciliation quarantined %s workspaces",
                workspaces["quarantined"],
            )
        from marquee.core.jobs.artifact_service import reconcile_artifacts
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
        await record_worker_node(settings.JOB_WORKER_NODE_ID, readiness="ready")
        app = create_worker(connection)
        _install_shutdown_handlers(app)
        await app.qm.run(
            dequeue_timeout=timedelta(seconds=settings.JOB_PGQUEUER_DEQUEUE_SECONDS),
            batch_size=settings.JOB_PGQUEUER_BATCH_SIZE,
            max_concurrent_tasks=settings.JOB_WORKER_CONCURRENCY,
            shutdown_on_listener_failure=True,
            heartbeat_timeout=timedelta(
                seconds=settings.JOB_PGQUEUER_HEARTBEAT_SECONDS
            ),
        )
    finally:
        if registered:
            await record_worker_node(settings.JOB_WORKER_NODE_ID, readiness="stopped")
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
