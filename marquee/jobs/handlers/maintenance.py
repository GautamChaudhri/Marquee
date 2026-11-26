"""The ``maintenance`` jobs: the scheduled database dump, and retention."""

from __future__ import annotations

import asyncio
from typing import Any

from marquee.config import settings
from marquee.db.engine import create_engine, create_session_factory
from marquee.jobs.registry import JobContext, JobError, JobKind, PermanentJobError
from marquee.maintenance import dumps, retention


def _backup(ctx: JobContext) -> dict[str, Any]:
    result = dumps.make_dump("scheduled", cancel=ctx.cancel)
    deleted = dumps.prune(settings.DB_BACKUP_KEEP, "scheduled")
    return {**result.to_dict(), "deleted": deleted, "keep": settings.DB_BACKUP_KEEP}


async def db_backup(ctx: JobContext) -> dict[str, Any]:
    """Dump the database, check the dump reads back, then keep the newest
    ``DB_BACKUP_KEEP`` scheduled dumps. Missing or old client tools are permanent; anything
    else (the database not answering, a full disk) is retried."""
    try:
        return await asyncio.to_thread(_backup, ctx)
    except dumps.DumpToolsError as exc:
        raise PermanentJobError(str(exc)) from exc
    except dumps.DumpError as exc:
        raise JobError(str(exc)) from exc


DB_BACKUP = JobKind(
    "db_backup",
    "maintenance",
    handler=db_backup,
    max_attempts=3,
    timeout_s=2 * 60 * 60,
    dedupe_key="db_backup",
)


async def retention_pass(ctx: JobContext) -> dict[str, Any]:
    """Delete what the retention rules say has gone stale; the result holds counts and bytes
    per rule, never a path. A data folder that overlaps a movie root is a permanent refusal."""
    engine = create_engine()
    try:
        async with create_session_factory(engine)() as session:
            report = await retention.run(session)
    except retention.RetentionRefusedError as exc:
        raise PermanentJobError(str(exc)) from exc
    finally:
        await engine.dispose()
    return report.to_result()


RETENTION = JobKind(
    "retention",
    "maintenance",
    handler=retention_pass,
    max_attempts=2,
    timeout_s=30 * 60,
    dedupe_key="retention",
)
