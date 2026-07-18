"""Subtitle-provider discovery and bounded reconciliation helpers.

Execution belongs to the canonical JMC5B handler.  This module deliberately
contains no job lifecycle, subprocess launch, cancellation registry, or media
publication authority.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from fastapi import HTTPException

from marquee.core.subtitles.config import subtitle_settings
from marquee.core.subtitles.embedded_subgen import resolved_model_and_device
from marquee.core.subtitles.generators import SubgenPathGenerator
from marquee.core.subtitles.generators.subgen import (
    parse_status_version,
)

logger = logging.getLogger(__name__)

_GENERATORS = {SubgenPathGenerator.id: SubgenPathGenerator()}
_WEBHOOK_COMPLETIONS: dict[str, dict] = {}


def get_generator(generator_id: str | None, *, configuration=None):
    if configuration is not None:
        generator = SubgenPathGenerator(configuration=configuration)
        return generator if generator_id in {None, generator.id} else None
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
