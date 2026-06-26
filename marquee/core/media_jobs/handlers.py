"""Media-job operation handlers (design §22.1 operations).

Each returns a JSON-able result dict or raises. Scan is non-destructive;
remove/embed/metadata go through the mutation transaction; generate goes through
the external generator; policy/restore compose the above. Execution paths that
need real binaries are validated structurally here and exercised on the box.
"""

from __future__ import annotations

import json
import logging

from sqlalchemy.ext.asyncio import AsyncSession

from marquee.core.jobs.child_tracking import clear_child_pid, record_child_pid
from marquee.core.media_files import resolve_media_file
from marquee.core.subtitles import mutation, service
from marquee.models import MediaJob

logger = logging.getLogger(__name__)


async def dispatch(db: AsyncSession, job: MediaJob, emit) -> dict:
    handler = _HANDLERS.get(job.operation)
    if handler is None:
        raise ValueError(f"no handler for operation {job.operation!r}")
    return await handler(db, job, emit)


async def _scan(db: AsyncSession, job: MediaJob, emit) -> dict:
    resolved = await resolve_media_file(db, job.media_file_id)
    inventory = await service.scan_inventory(db, resolved)
    await emit(db, job.job_id, "scan", "complete")
    return {
        "inventory_id": inventory.id,
        "tracks": len(await service._tracks_for(db, inventory.id)),
    }


async def _mutate(db: AsyncSession, job: MediaJob, emit) -> dict:
    return await mutation.execute_job(db, job, emit)


async def _extract(db: AsyncSession, job: MediaJob, emit) -> dict:
    import asyncio  # noqa: PLC0415
    from pathlib import Path  # noqa: PLC0415

    from marquee.core.subtitles import capabilities, probe
    from marquee.core.subtitles.adapters import adapter_for
    from marquee.media import binaries

    request = json.loads(job.request_json) if job.request_json else {}
    resolved = await resolve_media_file(db, job.media_file_id)
    track = await service.get_track(db, job.media_file_id, request["track_id"])
    if track is None or track.source != "embedded":
        raise ValueError("extract requires an embedded track")
    source_probe = await asyncio.to_thread(probe.probe_container, resolved.path)
    family = capabilities.container_family(source_probe.container if source_probe else None)
    adapter = adapter_for(family)
    ext = "srt" if track.kind == "text" else "sup"
    out = resolved.path.with_suffix(f".{track.language_tag}.extracted.{ext}")
    binary, args = adapter.build_extract(
        resolved.path,
        Path(out),
        stream_index=track.stream_index or 0,
        tool_track_id=track.tool_track_id,
    )
    await emit(db, job.job_id, "extract", "start")
    proc = await asyncio.create_subprocess_exec(
        binaries.resolve(binary) or binary,
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    await record_child_pid(proc.pid)
    try:
        _, stderr = await proc.communicate()
    finally:
        await clear_child_pid(proc.pid)
    if proc.returncode != 0:
        raise RuntimeError((stderr or b"").decode(errors="replace")[:300])
    # Re-scan so the new sidecar appears as an external track.
    await service.scan_inventory(db, await resolve_media_file(db, job.media_file_id))
    return {"extracted_to": str(out)}


async def _generate(db: AsyncSession, job: MediaJob, emit) -> dict:
    from marquee.core.subtitles.generation import run_generation_job  # noqa: PLC0415

    return await run_generation_job(db, job, emit)


async def _policy(db: AsyncSession, job: MediaJob, emit) -> dict:
    """Evaluate a stored policy snapshot against the file and remove if allowed."""
    request = json.loads(job.request_json) if job.request_json else {}
    removals = request.get("removals", [])
    if not removals:
        await emit(db, job.job_id, "policy", "complete", message="no removals")
        return {"removed": 0}
    # Reuse the mutation transaction with the precomputed removal set.
    job.operation = "subtitle_remove"
    job.request_json = json.dumps({**request, "track_ids": removals})
    return await mutation.execute_job(db, job, emit)


async def _restore(db: AsyncSession, job: MediaJob, emit) -> dict:
    """Re-embed managed subtitle assets after a media-file replacement."""
    from marquee.core.subtitles.restore import restore_managed_assets  # noqa: PLC0415

    return await restore_managed_assets(db, job, emit)


async def _letterbox_reencode(db: AsyncSession, job: MediaJob, emit) -> dict:
    from marquee.core.letterbox_reencode import execute_job  # noqa: PLC0415

    return await execute_job(db, job, emit)


_HANDLERS = {
    "subtitle_scan": _scan,
    "track_remove": _mutate,
    "subtitle_remove": _mutate,
    "subtitle_embed": _mutate,
    "subtitle_metadata": _mutate,
    "audio_reorder": _mutate,
    "subtitle_extract": _extract,
    "subtitle_generate": _generate,
    "subtitle_policy": _policy,
    "subtitle_restore": _restore,
    "letterbox_reencode": _letterbox_reencode,
}
