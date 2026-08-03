"""Scheduler-only PgQueuer process for canonical job production."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal
from collections.abc import Callable

import asyncpg
from pgqueuer import PgQueuer

from marquee.core.configuration_cache import configuration_provider
from marquee.core.jobs.runtime_instances import RuntimeInstanceHandle
from marquee.core.jobs.schedules import (
    PRODUCTION_SCHEDULE_CATALOG,
    ScheduleCatalog,
    ScheduleConfiguration,
    ScheduleDiagnostics,
    load_schedule_configuration,
    register_schedule_callbacks,
    schedule_diagnostics,
)
from marquee.core.jobs.transport_intent_monitor import monitor_until_shutdown
from marquee.core.runtime_settings import effective_settings as settings
from marquee.db_migration import asyncpg_dsn, verify_runtime_schema

logger = logging.getLogger(__name__)


def create_scheduler(
    connection: asyncpg.Connection,
    *,
    catalog: ScheduleCatalog = PRODUCTION_SCHEDULE_CATALOG,
    configuration_loader: Callable[[], ScheduleConfiguration] = load_schedule_configuration,
    diagnostics: ScheduleDiagnostics = schedule_diagnostics,
) -> PgQueuer:
    """Build the scheduler manager without registering worker handlers."""
    app = PgQueuer.from_asyncpg_connection(connection)
    register_schedule_callbacks(
        app,
        catalog=catalog,
        configuration_loader=configuration_loader,
        diagnostics=diagnostics,
    )
    return app


def _install_shutdown_handlers(app: PgQueuer) -> None:
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(signum, app.shutdown.set)
        except NotImplementedError:  # pragma: no cover - Windows fallback
            signal.signal(signum, lambda *_: app.shutdown.set())


async def run() -> None:
    """Run only PgQueuer's scheduler manager and code-owned callbacks."""
    connection = await asyncpg.connect(
        asyncpg_dsn(),
        server_settings={"application_name": "marquee:scheduler:pgqueuer"},
    )
    runtime: RuntimeInstanceHandle | None = None
    try:
        await verify_runtime_schema(connection)
        await configuration_provider.start(role="scheduler")
        from marquee.core.runtime_settings import initialize_effective_settings
        from marquee.logging import setup_logging

        initialize_effective_settings()
        setup_logging(settings.LOG_LEVEL, settings.LOG_FORMAT)
        runtime = RuntimeInstanceHandle(
            role="scheduler",
            node_label=settings.JOB_WORKER_NODE_ID,
            advertised_entrypoints=(
                definition.entrypoint for definition in PRODUCTION_SCHEDULE_CATALOG
            ),
            capabilities={
                "schedule_entrypoints": sorted(
                    definition.entrypoint for definition in PRODUCTION_SCHEDULE_CATALOG
                ),
                "transport": "pgqueuer",
            },
        )
        await runtime.start()
        app = create_scheduler(connection)
        _install_shutdown_handlers(app)
        await runtime.ready()
        monitor = asyncio.create_task(
            monitor_until_shutdown(
                app.shutdown,
                interval_seconds=settings.JOB_INTENT_MONITOR_SECONDS,
            )
        )
        try:
            await app.sm.run()
        finally:
            monitor.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await monitor
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
