"""Scheduler-only PgQueuer process for JMC1."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal

import asyncpg
from pgqueuer import PgQueuer

from marquee.config import settings
from marquee.core.configuration_cache import configuration_provider
from marquee.core.jobs.transport_intent_monitor import monitor_until_shutdown
from marquee.db_migration import asyncpg_dsn, verify_runtime_schema

logger = logging.getLogger(__name__)


def create_scheduler(connection: asyncpg.Connection) -> PgQueuer:
    """Build the scheduler manager without registering worker handlers."""
    return PgQueuer.from_asyncpg_connection(connection)


def _install_shutdown_handlers(app: PgQueuer) -> None:
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(signum, app.shutdown.set)
        except NotImplementedError:  # pragma: no cover - Windows fallback
            signal.signal(signum, lambda *_: app.shutdown.set())


async def run() -> None:
    """Run only PgQueuer's scheduler manager; JMC1 ships no schedules."""
    connection = await asyncpg.connect(
        asyncpg_dsn(),
        server_settings={"application_name": "marquee:scheduler:pgqueuer"},
    )
    try:
        await verify_runtime_schema(connection)
        await configuration_provider.start(role="scheduler")
        app = create_scheduler(connection)
        _install_shutdown_handlers(app)
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
