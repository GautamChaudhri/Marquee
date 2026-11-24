"""The ``library_sync`` job: one Radarr sync, as ``marquee sync`` runs it."""

from __future__ import annotations

from typing import Any

from marquee.jobs.registry import JobContext, PermanentJobError
from marquee.library import sync


async def library_sync(ctx: JobContext) -> dict[str, Any]:
    """The sync's report as a dict.

    Another sync holding the lock is a success that says it was skipped. Folders that do
    not map are permanent: retrying cannot mount a share or fix a setting.
    """
    try:
        report = await sync.sync_from_settings(cancel=ctx.cancel)
    except sync.SyncAlreadyRunningError as exc:
        return {"skipped": True, "reason": str(exc)}
    except sync.SyncPathError as exc:
        raise PermanentJobError(str(exc)) from exc
    return {"skipped": False, "ok": report.ok, **report.to_dict()}
