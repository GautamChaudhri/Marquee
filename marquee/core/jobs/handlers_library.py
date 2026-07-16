"""Migrated library-synchronization kernel handler (JMC4B B2).

Reuses the existing :class:`~marquee.core.sync_service.SyncService` domain logic through the
JMC3 execution context: cancellation, progress, and the domain-projection session all come from
the immutable :class:`~marquee.core.jobs.delivery.ExecutionContext`. It does not open a parallel
lifecycle or persist provider credentials.
"""

from __future__ import annotations

import asyncio
from typing import Any

from marquee.config import settings
from marquee.core.cancellation import JobCancelledError
from marquee.core.jobs.delivery import ExecutionContext, register_execution_handler
from marquee.core.jobs.progress import MeasurementMode, ProgressMeasurementUpdate
from marquee.core.jobs.progress_service import ProgressObservation, progress_writer


class _ContextCancellation:
    """Duck-typed ``is_set()`` event bridging SyncService checks to the kernel token."""

    __slots__ = ("_cancellation",)

    def __init__(self, cancellation: Any) -> None:
        self._cancellation = cancellation

    def is_set(self) -> bool:
        return bool(getattr(self._cancellation, "cancel_called", False))


async def _emit_stage(context: ExecutionContext, *, stage_key: str, ordinal: int) -> None:
    """Best-effort indeterminate phase progress; telemetry never fails the sync."""
    await progress_writer.safe_write(
        job_id=context.delivery.canonical_job_id,
        attempt_id=context.attempt.attempt_id,
        fence_token=context.attempt.fence_token,
        observation=ProgressObservation(
            stage_key=stage_key,
            overall=ProgressMeasurementUpdate(
                scope_id="library-sync:overall", mode=MeasurementMode.INDETERMINATE
            ),
            current=ProgressMeasurementUpdate(
                scope_id=f"library-sync:{stage_key}", mode=MeasurementMode.INDETERMINATE
            ),
            producer_ordinal=ordinal,
        ),
    )


def _counts(result: Any) -> dict[str, int]:
    return {"created": result.created, "updated": result.updated, "errors": result.errors}


async def execute_library_sync(context: ExecutionContext) -> dict[str, Any]:
    """Synchronize Radarr/Sonarr/TMDB into the derived library projection."""
    from marquee.core.arr_clients.radarr_client import RadarrClient  # noqa: PLC0415
    from marquee.core.arr_clients.sonarr_client import SonarrClient  # noqa: PLC0415
    from marquee.core.poster_sources.tmdb import TMDBClient  # noqa: PLC0415
    from marquee.core.sync_service import SyncService  # noqa: PLC0415

    if context.cancellation.cancel_called:
        raise asyncio.CancelledError

    radarr = (
        RadarrClient(settings.RADARR_URL, settings.RADARR_API_KEY)
        if settings.radarr_configured
        else None
    )
    sonarr = (
        SonarrClient(settings.SONARR_URL, settings.SONARR_API_KEY)
        if settings.sonarr_configured
        else None
    )
    tmdb = (
        TMDBClient(read_access_token=settings.TMDB_READ_ACCESS_TOKEN)
        if settings.tmdb_configured
        else None
    )
    sources = {
        "radarr": "configured" if radarr is not None else "skipped",
        "sonarr": "configured" if sonarr is not None else "skipped",
        "tmdb": "configured" if tmdb is not None else "skipped",
    }
    if radarr is None and sonarr is None:
        # No library source to reconcile against; honest no-change rather than an empty success.
        return {
            "outcome": "no_change",
            "summary": {"reason": "no_source_configured", "sources": sources},
        }

    cancel = _ContextCancellation(context.cancellation)
    ordinal = 0

    async def progress(stage: str, _message: str) -> None:
        nonlocal ordinal
        if context.cancellation.cancel_called:
            raise asyncio.CancelledError
        ordinal += 1
        await _emit_stage(context, stage_key=stage, ordinal=ordinal)

    await _emit_stage(context, stage_key="connecting", ordinal=(ordinal := ordinal + 1))
    for client in (radarr, sonarr, tmdb):
        if client is not None:
            await client.connect()
    try:
        async with context.session_factory() as db:
            report = await SyncService(db, radarr=radarr, sonarr=sonarr, tmdb=tmdb).sync_all(
                progress=progress, cancel_event=cancel
            )
    except JobCancelledError as exc:
        # SyncService signals cooperative cancellation with a domain exception; the kernel
        # treats CancelledError (not a generic failure) as cancellation.
        raise asyncio.CancelledError from exc
    finally:
        for client in (radarr, sonarr, tmdb):
            if client is not None:
                await client.disconnect()

    await _emit_stage(context, stage_key="finalizing", ordinal=(ordinal := ordinal + 1))

    errors = (
        report.movies.errors
        + report.series.errors
        + report.seasons.errors
        + report.episodes.errors
    )
    changed = (
        report.movies.total
        + report.series.total
        + report.seasons.total
        + report.episodes.total
    )
    warnings: list[str] = []
    if errors:
        warnings.append(f"{errors} subject(s) failed to reconcile")
    for name, client in (("radarr", radarr), ("sonarr", sonarr)):
        if client is None:
            warnings.append(f"{name} was not configured; its subjects were not reconciled")
    summary: dict[str, Any] = {
        "sources": sources,
        "duration_seconds": report.duration_seconds,
        "movies": _counts(report.movies),
        "series": _counts(report.series),
        "seasons": _counts(report.seasons),
        "episodes": _counts(report.episodes),
        "warnings": warnings,
    }
    outcome = "succeeded" if (changed or errors) else "no_change"
    return {"outcome": outcome, "summary": summary}


register_execution_handler("library_sync", execute_library_sync)
