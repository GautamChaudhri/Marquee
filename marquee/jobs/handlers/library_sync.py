"""The ``library_sync`` job: one Radarr sync, as ``marquee sync`` runs it."""

from __future__ import annotations

from typing import Any

from marquee.config import settings
from marquee.db.engine import create_engine, create_session_factory
from marquee.jobs.handlers.poster_run import enqueue_scope
from marquee.jobs.registry import JobContext, PermanentJobError
from marquee.library import sync


async def queue_new_movie_runs() -> dict[str, Any] | None:
    """With ``RUN_NEW_MOVIES``, a batch of runs for the movies never run, if there are any."""
    if not settings.RUN_NEW_MOVIES:
        return None
    engine = create_engine()
    try:
        async with create_session_factory(engine)() as session:
            parent, children, found = await enqueue_scope(session, "never-run")
    finally:
        await engine.dispose()
    return {"batch_id": parent.id, "movies": found, "queued": len(children)}


async def library_sync(ctx: JobContext) -> dict[str, Any]:
    """The sync's report as a dict.

    Another sync holding the lock is a success that says it was skipped. Folders that do
    not map are permanent: retrying cannot mount a share or fix a setting. A sync that ran
    then queues runs of new movies when ``RUN_NEW_MOVIES`` is on.
    """
    try:
        report = await sync.sync_from_settings(cancel=ctx.cancel)
    except sync.SyncAlreadyRunningError as exc:
        return {"skipped": True, "reason": str(exc)}
    except sync.SyncPathError as exc:
        raise PermanentJobError(str(exc)) from exc
    result: dict[str, Any] = {"skipped": False, "ok": report.ok, **report.to_dict()}
    result["new_movie_runs"] = await queue_new_movie_runs()
    return result
