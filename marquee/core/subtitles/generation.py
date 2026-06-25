"""Generation orchestration (design §24) — submit, reconcile, validate, embed.

Owns durable job state; provider progress is best-effort. Submits to the
configured generator, reconciles by polling for the output file (the Subgen
completion webhook can short-circuit this but isn't required), validates the
text, and — for embedded output — re-embeds it through the normal mutation path.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from marquee.core.media_files import resolve_media_file
from marquee.core.subtitles import service
from marquee.core.subtitles.config import subtitle_settings
from marquee.core.subtitles.generators import SubgenPathGenerator
from marquee.core.subtitles.generators.base import GenerationRequest
from marquee.models import MediaJob

logger = logging.getLogger(__name__)

# Single provider for now; the list/shape is plural so more can be added.
_GENERATORS = {SubgenPathGenerator.id: SubgenPathGenerator()}


def get_generator(generator_id: str | None):
    if generator_id is None:
        return next(iter(_GENERATORS.values()), None)
    return _GENERATORS.get(generator_id)


async def list_generators() -> list[dict]:
    """Health + real capabilities for ``GET /api/subtitle-generators``."""
    out = []
    for gen in _GENERATORS.values():
        caps = gen.capabilities()
        health = await gen.health()

        # Default device is cuda in this deployment profile.
        device = "cuda"
        if health.version and "cpu" in health.version.lower():
            device = "cpu"

        out.append(
            {
                # Backend fields
                "id": caps.id,
                "name": caps.name,
                "provider": caps.provider,
                "mode": caps.mode,
                "model_label": caps.model_label,
                "healthy": health.healthy,
                "version": health.version,
                "detail": health.detail,
                "supports_language_hint": caps.supports_language_hint,
                "supports_per_request_model": caps.supports_per_request_model,
                "supports_percent_progress": caps.supports_percent_progress,
                "transport": caps.transport,
                # Frontend schema matches SubtitleGenerator interface in types.ts
                "type": caps.provider,
                "url": subtitle_settings.SUBGEN_URL or "",
                "online": health.healthy,
                "model": caps.model_label,
                "device": device,
                "capabilities": {
                    "language_hint": caps.supports_language_hint,
                    "translate": caps.mode == "translate",
                    "concurrent": 2 if device == "cuda" else 1,
                },
            }
        )
    return out


def validate_generated_srt(path: Path | str) -> dict:
    """Decode + parse + sanity-check a generated subtitle (design §24.5)."""
    try:
        import pysubs2  # noqa: PLC0415
        from charset_normalizer import from_path  # noqa: PLC0415

        best = from_path(str(path)).best()
        encoding = best.encoding if best else "utf-8"
        subs = pysubs2.load(str(path), encoding=encoding)
    except Exception as exc:  # noqa: BLE001
        return {"valid": False, "reason": f"parse failed: {exc}"}

    events = [e for e in subs.events if not e.is_comment and e.plaintext.strip()]
    if not events:
        return {"valid": False, "reason": "no non-empty cues"}
    last_end = -1
    for ev in events:
        if ev.start < 0 or ev.end < ev.start:
            return {"valid": False, "reason": "non-monotonic or negative timing"}
        last_end = max(last_end, ev.end)
    return {
        "valid": True,
        "encoding": encoding,
        "cue_count": len(events),
        "first_ms": events[0].start,
        "last_ms": events[-1].end,
    }


async def run_generation_job(db: AsyncSession, job: MediaJob, emit) -> dict:
    """Submit to the generator, await the output, validate, and optionally embed."""
    request_data = json.loads(job.request_json) if job.request_json else {}
    generator = get_generator(request_data.get("generator_id"))
    if generator is None or not subtitle_settings.generation_enabled:
        raise RuntimeError("no generation provider configured (set SUBGEN_URL)")

    resolved = await resolve_media_file(db, job.media_file_id)
    gen_request = GenerationRequest(
        media_file_id=job.media_file_id,
        local_media_path=str(resolved.path),
        language_hint=request_data.get("language_hint"),
        output=request_data.get("output", "external"),
    )

    await emit(db, job.job_id, "queued", "running", message="submitting to provider")
    submission = await generator.submit(gen_request)
    if not submission.accepted:
        raise RuntimeError(f"provider rejected submission: {submission.detail}")

    await emit(db, job.job_id, "provider-running", "running")
    deadline = asyncio.get_running_loop().time() + subtitle_settings.SUBGEN_TIMEOUT_MINUTES * 60
    produced: str | None = None
    while asyncio.get_running_loop().time() < deadline:
        state = await generator.reconcile(gen_request)
        if state.state == "produced":
            produced = state.output_path
            break
        if state.state == "failed":
            raise RuntimeError(f"provider failed: {state.message}")
        await asyncio.sleep(subtitle_settings.SUBGEN_POLL_SECONDS)
    if produced is None:
        raise TimeoutError("generation timed out waiting for output")

    await emit(db, job.job_id, "validating", "running")
    validation = validate_generated_srt(produced)
    if not validation["valid"]:
        raise RuntimeError(f"generated subtitle invalid: {validation['reason']}")

    # Rescan so the new sidecar shows up as an external (generated) track.
    await service.scan_inventory(db, await resolve_media_file(db, job.media_file_id))

    result = {"output_path": produced, "validation": validation, "embedded": False}
    if gen_request.output == "embedded":
        await emit(db, job.job_id, "embedding", "running")
        track = await _find_external_track(db, job.media_file_id, produced)
        if track is not None:
            from marquee.core.subtitles import mutation  # noqa: PLC0415

            inv_id = track.inventory_id
            job.operation = "subtitle_embed"
            job.request_json = json.dumps({"inventory_id": inv_id, "track_ids": [track.id]})
            await mutation.execute_job(db, job, emit)
            result["embedded"] = True
    await emit(db, job.job_id, "done", "complete")
    return result


async def _find_external_track(db: AsyncSession, media_file_id: int, path: str):
    inv_dict_tracks = await service.get_inventory_dict(db, media_file_id)
    target = str(Path(path))
    for track in inv_dict_tracks["tracks"]:
        # Match by basename since inventory dicts don't expose raw paths.
        if track["source"] == "external":
            full = await service.get_track(db, media_file_id, track["id"])
            if full and full.external_path and str(Path(full.external_path)) == target:
                return full
    return None
