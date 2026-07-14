"""Generation orchestration (design §24) — submit, reconcile, validate, embed."""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import time
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from marquee.core.jobs import cancel_registry
from marquee.core.jobs.cancel_registry import JobCancelledError
from marquee.core.media_files import resolve_media_file
from marquee.core.subtitles import service
from marquee.core.subtitles.config import subtitle_settings
from marquee.core.subtitles.embedded_subgen import resolved_model_and_device
from marquee.core.subtitles.generators import SubgenPathGenerator
from marquee.core.subtitles.generators.base import GenerationRequest
from marquee.core.subtitles.generators.subgen import (
    build_asr_sidecar_path,
    parse_status_version,
    temp_audio_wav_path,
)
from marquee.core.subtitles.languages import same_language
from marquee.models import MediaFile

logger = logging.getLogger(__name__)

_GENERATORS = {SubgenPathGenerator.id: SubgenPathGenerator()}
_WEBHOOK_COMPLETIONS: dict[str, dict] = {}


def get_generator(generator_id: str | None):
    if generator_id is None:
        return next(iter(_GENERATORS.values()), None)
    return _GENERATORS.get(generator_id)


def current_subgen_model() -> str:
    if subtitle_settings.subgen_deployment == "embedded":
        return resolved_model_and_device()["model"]
    return subtitle_settings.SUBGEN_WHISPER_MODEL or subtitle_settings.SUBGEN_MODEL_LABEL


def validate_generation_request(task: str, model: str | None) -> None:
    if task != "translate":
        return
    blocked = {"large-v3-turbo", "distil-large-v3"}
    if model in blocked:
        raise HTTPException(
            status_code=422,
            detail=f"{model} cannot translate; choose a translating Whisper model first.",
        )


async def list_generators() -> list[dict]:
    """Health + real capabilities for ``GET /api/subtitle-generators``."""
    out = []
    for gen in _GENERATORS.values():
        caps = gen.capabilities()
        health = await gen.health()
        parsed = (
            json.loads(health.detail)
            if health.detail and health.detail.startswith("{")
            else parse_status_version(health.version)
        )
        resolved = resolved_model_and_device()
        device = resolved["device"] if subtitle_settings.subgen_deployment == "embedded" else "external"
        out.append(
            {
                "id": caps.id,
                "name": caps.name,
                "provider": caps.provider,
                "mode": caps.mode,
                "model_label": current_subgen_model(),
                "healthy": health.healthy,
                "version": health.version,
                "detail": health.detail,
                "supports_language_hint": caps.supports_language_hint,
                "supports_per_request_model": caps.supports_per_request_model,
                "supports_percent_progress": caps.supports_percent_progress,
                "transport": caps.transport,
                "type": caps.provider,
                "url": subtitle_settings.subgen_url or "",
                "online": health.healthy,
                "model": current_subgen_model(),
                "device": device,
                "deployment": subtitle_settings.subgen_deployment,
                "versions": parsed,
                "capabilities": {
                    "language_hint": caps.supports_language_hint,
                    "translate": True,
                    "concurrent": subtitle_settings.SUBGEN_CONCURRENT_TRANSCRIPTIONS,
                    "advanced_asr": True,
                },
            }
        )
    return out


def register_completion(source_path: str, subtitle_path: str, payload: dict) -> None:
    _WEBHOOK_COMPLETIONS[str(Path(source_path))] = {
        "subtitle_path": str(Path(subtitle_path)),
        "payload": payload,
        "received_at": time.time(),
    }


def take_completion(source_path: str) -> dict | None:
    return _WEBHOOK_COMPLETIONS.pop(str(Path(source_path)), None)


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
    for ev in events:
        if ev.start < 0 or ev.end < ev.start:
            return {"valid": False, "reason": "non-monotonic or negative timing"}
    return {
        "valid": True,
        "encoding": encoding,
        "cue_count": len(events),
        "first_ms": events[0].start,
        "last_ms": events[-1].end,
    }


async def _existing_requested_language(db: AsyncSession, media_file_id: int, language_hint: str | None) -> str | None:
    if not language_hint:
        return None
    inv = await service.get_inventory_dict(db, media_file_id, force=False)
    for track in inv.get("tracks", []):
        if not same_language(track.get("language_tag"), language_hint):
            continue
        if track.get("is_commentary"):
            continue
        return "already covered - Subgen would likely skip this"
    return None


async def _extract_audio_for_asr(source_path: str, stream_index: int | None) -> Path:
    ffmpeg_bin = shutil.which("ffmpeg") or "ffmpeg"
    output = temp_audio_wav_path(source_path)
    args = [
        ffmpeg_bin,
        "-y",
        "-i",
        source_path,
    ]
    if stream_index is not None:
        args.extend(["-map", f"0:{stream_index}"])
    else:
        args.extend(["-map", "0:a:0"])
    args.extend(["-ac", "1", "-ar", "16000", str(output)])
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError((stderr or b"ffmpeg audio extraction failed").decode(errors="ignore")[:500])
    return output


async def _select_stream_index(
    db: AsyncSession, media_file_id: int, language_hint: str | None, explicit: int | None
) -> int | None:
    if explicit is not None:
        return explicit
    inventory = (
        await db.execute(select(MediaFile).where(MediaFile.id == media_file_id))
    ).scalar_one_or_none()
    if inventory is None:
        return None
    inv = await service.get_inventory_dict(db, media_file_id, force=False)
    for stream in inv.get("audio_streams", []):
        if language_hint and same_language(stream.get("language_tag"), language_hint):
            return stream.get("index")
    return None


async def run_generation_job(db: AsyncSession, job: Any, emit) -> dict:
    cancel_event = cancel_registry.get(job.job_id)
    produced: str | None = None
    temp_audio: Path | None = None

    def check_cancelled(*, cleanup: bool = False) -> None:
        if cancel_event is None or not cancel_event.is_set():
            return
        if cleanup and produced is not None:
            Path(produced).unlink(missing_ok=True)
        if cleanup and temp_audio is not None:
            temp_audio.unlink(missing_ok=True)
        raise JobCancelledError("subtitle generation cancelled")

    request_data = json.loads(job.request_json) if job.request_json else {}
    task = request_data.get("task") or "transcribe"
    validate_generation_request(task, current_subgen_model())
    generator = get_generator(request_data.get("generator_id"))
    if generator is None or not subtitle_settings.generation_enabled:
        raise RuntimeError("no generation provider configured")

    check_cancelled()
    resolved = await resolve_media_file(db, job.media_file_id)
    gen_request = GenerationRequest(
        media_file_id=job.media_file_id,
        local_media_path=str(resolved.path),
        language_hint=request_data.get("language_hint"),
        output=request_data.get("output", "external"),
        task=task,
        stream_index=request_data.get("stream_index"),
    )

    existing = await _existing_requested_language(db, job.media_file_id, gen_request.language_hint)
    if existing and (gen_request.stream_index is None and task == "transcribe"):
        await emit(db, job.job_id, "done", "complete", message=existing)
        return {"skipped": True, "detail": existing}

    provider = getattr(generator, "id", None) or type(generator).__name__
    lang = request_data.get("language_hint") or "auto-detected language"
    await emit(db, job.job_id, "queued", "running", message=f"Submitting to {provider} ({lang})")

    if gen_request.stream_index is not None or task != "transcribe":
        stream_index = await _select_stream_index(
            db,
            job.media_file_id,
            gen_request.language_hint,
            gen_request.stream_index,
        )
        temp_audio = await _extract_audio_for_asr(str(resolved.path), stream_index)
        produced = str(build_asr_sidecar_path(str(resolved.path), gen_request.language_hint))
        submission = await generator.submit_asr(
            gen_request,
            audio_path=str(temp_audio),
            output_path=produced,
        )
        if temp_audio is not None:
            temp_audio.unlink(missing_ok=True)
            temp_audio = None
        if not submission.accepted:
            raise RuntimeError(f"provider rejected submission: {submission.detail}")
    else:
        submission = await generator.submit(gen_request)
        if not submission.accepted:
            raise RuntimeError(f"provider rejected submission: {submission.detail}")
        await emit(
            db,
            job.job_id,
            "provider-running",
            "running",
            message=f"Generating {lang} subtitles via {provider}...",
        )
        deadline = asyncio.get_running_loop().time() + subtitle_settings.SUBGEN_TIMEOUT_MINUTES * 60
        while asyncio.get_running_loop().time() < deadline:
            check_cancelled()
            completion = take_completion(str(resolved.path))
            if completion is not None:
                produced = completion["subtitle_path"]
                break
            state = await generator.reconcile(gen_request)
            if state.state == "produced":
                produced = state.output_path
                break
            if state.state == "failed":
                raise RuntimeError(f"provider failed: {state.message}")
            await asyncio.sleep(subtitle_settings.SUBGEN_POLL_SECONDS)
        if produced is None:
            raise TimeoutError("generation timed out waiting for output")

    check_cancelled(cleanup=True)
    await emit(db, job.job_id, "validating", "running")
    validation = validate_generated_srt(produced)
    if not validation["valid"]:
        raise RuntimeError(f"generated subtitle invalid: {validation['reason']}")

    await service.scan_inventory(db, await resolve_media_file(db, job.media_file_id))
    result = {"output_path": produced, "validation": validation, "embedded": False, "task": task}
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
        if track["source"] == "external":
            full = await service.get_track(db, media_file_id, track["id"])
            if full and full.external_path and str(Path(full.external_path)) == target:
                return full
    return None
