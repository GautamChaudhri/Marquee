"""Worker-only PgQueuer process for the JMC1 control entrypoint."""

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
from marquee.core.jobs.delivery import deliver_control_job
from marquee.db_migration import asyncpg_dsn, verify_runtime_schema

logger = logging.getLogger(__name__)


def create_worker(connection: asyncpg.Connection) -> PgQueuer:
    """Build the worker manager and register only the locked control entrypoint."""
    app = PgQueuer.from_asyncpg_connection(connection)

    @app.entrypoint(
        "control",
        concurrency_limit=settings.JOB_CONTROL_CONCURRENCY,
        accepts_context=True,
        on_failure="hold",
    )
    async def control(job: PgQueuerJob, context: Context) -> None:
        await deliver_control_job(job, context)

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
    try:
        await verify_runtime_schema(connection)
        await configuration_provider.start(role="worker")
        app = create_worker(connection)
        _install_shutdown_handlers(app)
        batch_size = settings.JOB_PGQUEUER_BATCH_SIZE
        await app.qm.run(
            dequeue_timeout=timedelta(seconds=settings.JOB_PGQUEUER_DEQUEUE_SECONDS),
            batch_size=batch_size,
            max_concurrent_tasks=max(
                settings.JOB_CONTROL_CONCURRENCY,
                2 * batch_size,
            ),
            shutdown_on_listener_failure=True,
            heartbeat_timeout=timedelta(
                seconds=settings.JOB_PGQUEUER_HEARTBEAT_SECONDS
            ),
        )
    finally:
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
