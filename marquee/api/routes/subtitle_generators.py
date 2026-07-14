"""Subtitle generation provider routes and embedded Subgen management."""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from marquee.core.configuration import (
    ConfigurationError,
    ConfigurationVersionConflictError,
    update_configuration,
)
from marquee.core.configuration_cache import configuration_provider
from marquee.core.media_files import ensure_media_file_for_movie
from marquee.core.media_jobs import media_job_manager
from marquee.core.subtitles import generation
from marquee.core.subtitles.config import subtitle_settings
from marquee.core.subtitles.embedded_subgen import hardware_snapshot
from marquee.core.subtitles.whisper_catalog import catalog_dicts, per_model_verdicts, recommend
from marquee.database import get_db
from marquee.models import Movie

logger = logging.getLogger(__name__)

router = APIRouter(tags=["subtitle-generators"])


@router.get("/api/subtitle-generators")
async def list_generators(request: Request):
    generators = await generation.list_generators()
    supervisor = getattr(request.app.state, "worker_supervisor", None)
    subgen_state = supervisor.subgen_status() if supervisor is not None else {"state": "disabled"}
    return {
        "generators": [
            {
                **item,
                "child_state": subgen_state["state"] if item["provider"] == "subgen" else None,
                "queue_depth": {
                    "processing": subgen_state.get("queue_processing", 0),
                    "queued": subgen_state.get("queue_queued", 0),
                },
                "last_activity_line": subgen_state.get("last_activity_line"),
            }
            for item in generators
        ]
    }


class GenerateRequest(BaseModel):
    generator_id: str | None = None
    language_hint: str | None = None
    output: str = "external"
    task: Literal["transcribe", "translate"] = "transcribe"
    stream_index: int | None = None


class SubgenSettingsRequest(BaseModel):
    expected_version: int
    deployment: Literal["disabled", "external", "embedded"] | None = None
    url: str | None = None
    profile_name: str | None = None
    model_label: str | None = None
    mode: Literal["transcribe", "translate"] | None = None
    local_path_prefix: str | None = None
    remote_path_prefix: str | None = None
    callback_token: str | None = None
    whisper_model: str | None = None
    embedded_port: int | None = Field(default=None, ge=1, le=65535)
    transcribe_device: Literal["auto", "cpu", "cuda"] | None = None
    gpu_index: int | None = Field(default=None, ge=0)
    compute_type: str | None = None
    concurrent_transcriptions: int | None = Field(default=None, ge=1, le=32)
    whisper_threads: int | None = Field(default=None, ge=0, le=128)
    model_path: str | None = None
    naming_type: Literal["ISO_639_1", "ISO_639_2_T", "ISO_639_2_B", "NAME", "NATIVE"] | None = None
    name_includes_subgen: bool | None = None
    name_includes_model: bool | None = None





@router.get("/api/subtitle-generators/subgen/hardware")
async def subgen_hardware():
    hw = hardware_snapshot()
    english_only = subtitle_settings.effective_preferred_subtitle_languages == ["en"]
    return {
        "hardware": {
            "gpus": [
                {
                    "index": gpu.index,
                    "name": gpu.name,
                    "vram_total": gpu.vram_total,
                    "vram_free": gpu.vram_free,
                }
                for gpu in hw.gpus
            ],
            "cpu_count": hw.cpu_count,
            "ram_total": hw.ram_total,
        },
        "models": per_model_verdicts(hw, subtitle_settings.SUBGEN_MODE, english_only_preferred=english_only),
        "catalog": catalog_dicts(),
        "recommendation": recommend(hw, subtitle_settings.SUBGEN_MODE, english_only_preferred=english_only),
    }


@router.put("/api/subtitle-generators/subgen/settings")
async def update_subgen_settings(
    body: SubgenSettingsRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    update = {
        f"SUBGEN_{key.upper()}": value
        for key, value in body.model_dump(
            exclude={"expected_version"}, exclude_unset=True
        ).items()
    }
    try:
        state, changed = await update_configuration(
            db,
            expected_version=body.expected_version,
            updates=update,
            actor={"kind": "api", "id": "subtitle-generators"},
            trigger="subgen_settings_api",
        )
    except ConfigurationVersionConflictError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail={
                "code": "configuration_version_conflict",
                "current_version": exc.current.version,
                "etag": exc.current.etag,
            },
        ) from exc
    except ConfigurationError as exc:
        await db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await db.commit()
    await configuration_provider.refresh_from_session(db)
    return {
        "configuration_version": state.version,
        "etag": state.etag,
        "changed": changed,
        "applied": sorted(update) if changed else [],
        "settings": configuration_provider.effective("subtitle"),
    }


@router.post("/api/subtitle-generators/subgen/restart")
async def restart_subgen(request: Request):
    if subtitle_settings.subgen_deployment != "embedded":
        raise HTTPException(status_code=422, detail="Embedded Subgen is not enabled.")
    supervisor = getattr(request.app.state, "worker_supervisor", None)
    if supervisor is None:
        raise HTTPException(status_code=503, detail="Worker supervisor is not available.")
    return await supervisor.restart_subgen()


@router.get("/api/subtitle-generators/subgen/logs")
async def subgen_logs(request: Request, tail: int = 200):
    supervisor = getattr(request.app.state, "worker_supervisor", None)
    if supervisor is None:
        return {"lines": []}
    return {"lines": supervisor.subgen_logs(tail=tail)}


@router.post("/api/subtitle-generators/subgen/test")
async def subgen_test():
    generator = generation.get_generator(None)
    if generator is None or not subtitle_settings.generation_enabled:
        raise HTTPException(status_code=503, detail="Generation disabled.")
    probe = Path(__file__).resolve().parents[2] / "assets" / "subgen_probe.mp3"
    started = time.perf_counter()
    result = await generator.detect_language(str(probe))
    elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
    return {
        "ok": True,
        "detected_language": result.get("detected_language") or result.get("language"),
        "latency_ms": elapsed_ms,
        "raw": result,
    }


@router.post("/api/media-files/{media_file_id}/subtitle-generations", status_code=202)
async def generate_for_media_file(
    media_file_id: int,
    body: GenerateRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    if not subtitle_settings.generation_enabled:
        raise HTTPException(status_code=503, detail="Generation disabled.")
    generation.validate_generation_request(body.task, generation.current_subgen_model())
    job = await media_job_manager.create_job(
        db,
        operation="subtitle_generate",
        media_file_id=media_file_id,
        trigger="manual",
        request=body.model_dump(),
        status="queued",
    )
    return {"job_id": job.job_id, "events_url": f"/api/media-jobs/{job.job_id}/events"}


@router.post("/api/movies/{movie_id}/subtitle-generations", status_code=202)
async def generate_for_movie(
    movie_id: int,
    body: GenerateRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    if not subtitle_settings.generation_enabled:
        raise HTTPException(status_code=503, detail="Generation disabled.")
    generation.validate_generation_request(body.task, generation.current_subgen_model())
    movie = (await db.execute(select(Movie).where(Movie.id == movie_id))).scalar_one_or_none()
    if movie is None:
        raise HTTPException(status_code=404, detail=f"Movie id={movie_id} not found")
    media_file = await ensure_media_file_for_movie(db, movie)
    if media_file is None:
        raise HTTPException(status_code=422, detail="Movie has no media file")
    job = await media_job_manager.create_job(
        db,
        operation="subtitle_generate",
        media_file_id=media_file.id,
        trigger="manual",
        request=body.model_dump(),
        status="queued",
    )
    return {"job_id": job.job_id, "events_url": f"/api/media-jobs/{job.job_id}/events"}
