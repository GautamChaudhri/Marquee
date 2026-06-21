"""Runtime pipeline knob inspection + mutation (design 09 §14).

``GET`` exposes the live ``pipeline_settings`` (current values, code defaults,
persisted overrides, and the knobs that need a restart). ``PUT`` validates a
proposed change by constructing a throwaway ``PipelineSettings`` (reusing the
existing cross-field validators), applies it to the live singleton (gates and
scorers read attributes at call time), persists it to
``data/pipeline_overrides.json``, and resets the run extractor so the next run
re-preflights with the new values.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from marquee.core.pipeline_config import (
    PipelineSettings,
    load_overrides,
    pipeline_settings,
    save_overrides,
)
from marquee.core.pipeline_config_meta import KNOB_GROUPS, KNOB_META

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/config", tags=["config"])

# Knobs that only bind at process start (model identity, provider, paths).
# Changing these needs a restart + a matching artifact, so the hot PUT path
# rejects them.
RESTART_REQUIRED: frozenset[str] = frozenset(
    {
        "AI_MODEL",
        "EXECUTION_PROVIDER",
        "CLIP_MODEL_PATH",
        "AESTHETIC_MODEL_PATH",
        "FACE_MODEL_PATH",
        "TASTE_PROFILE_PATH",
        "DINO_MODEL_PATH",
        "PERSON_MODEL_PATH",
        "ZEROSHOT_AXES_PATH",
        "LEARNED_HEAD_PATH",
        "EMBEDDING_CACHE_DIR",
        "FEEDBACK_LABELS_PATH",
        "NEGATIVE_DATA_DIR",
        "TRAINING_DATA_DIR",
    }
)


class ConfigUpdate(BaseModel):
    values: dict[str, Any]


def _serialize(value: Any) -> Any:
    return str(value) if isinstance(value, Path) else value


@router.get("/pipeline")
async def get_pipeline_config():
    fields = PipelineSettings.model_fields
    current = pipeline_settings.model_dump()
    # Merge per-field descriptions from the pydantic model.
    meta = {}
    for name, info in KNOB_META.items():
        entry = dict(info)
        field = fields.get(name)
        if field and field.description:
            entry["help"] = field.description
        meta[name] = entry
    return {
        "values": {name: _serialize(current[name]) for name in fields},
        "defaults": {
            name: _serialize(field.default) for name, field in fields.items()
        },
        "overrides": load_overrides(),
        "restart_required": sorted(RESTART_REQUIRED),
        "groups": KNOB_GROUPS,
        "meta": meta,
    }


@router.put("/pipeline")
async def put_pipeline_config(update: ConfigUpdate):
    new_values = update.values
    if not new_values:
        raise HTTPException(status_code=400, detail="No values provided")

    known = set(PipelineSettings.model_fields)
    unknown = [key for key in new_values if key not in known]
    if unknown:
        raise HTTPException(status_code=400, detail=f"Unknown config keys: {unknown}")

    blocked = [key for key in new_values if key in RESTART_REQUIRED]
    if blocked:
        raise HTTPException(
            status_code=400,
            detail=(
                f"These knobs need a restart (set them via .env, not the API): {blocked}"
            ),
        )

    # Validate by constructing a fully-specified throwaway instance — this runs
    # every cross-field validator (weight sums, ranges, enum choices).
    merged = {**pipeline_settings.model_dump(), **new_values}
    try:
        PipelineSettings(**merged)
    except Exception as exc:  # noqa: BLE001 — surface the validation error
        logger.exception("CONFIG | invalid update %s", sorted(new_values))
        raise HTTPException(status_code=400, detail="Invalid configuration") from exc

    # Apply to the live singleton (gates/scorers read attributes at call time).
    for key, value in new_values.items():
        setattr(pipeline_settings, key, value)

    # Persist (merge into existing overrides).
    overrides = {**load_overrides(), **new_values}
    save_overrides(overrides)

    # Next run re-preflights with the new knobs.
    from marquee.pipeline.run_manager import run_manager  # noqa: PLC0415

    run_manager.reset_extractor()

    logger.info("CONFIG | updated %s", sorted(new_values))
    return {"applied": sorted(new_values), "overrides": overrides}
