"""Version-aware pipeline configuration inspection and mutation."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from marquee.core.configuration import (
    CONFIGURATION_CATALOG,
    PIPELINE_RESTART_KEYS,
    ConfigurationError,
    ConfigurationVersionConflictError,
    update_configuration,
)
from marquee.core.configuration_cache import configuration_provider
from marquee.core.pipeline_config import PipelineSettings
from marquee.core.pipeline_config_meta import KNOB_GROUPS, KNOB_META
from marquee.database import get_db

router = APIRouter(prefix="/api/config", tags=["config"])


class ConfigUpdate(BaseModel):
    expected_version: int
    values: dict[str, Any]


def _serialize(value: Any) -> Any:
    return str(value) if isinstance(value, Path) else value


def _metadata() -> dict[str, dict[str, Any]]:
    fields = PipelineSettings.model_fields
    metadata: dict[str, dict[str, Any]] = {}
    for name, catalog_entry in CONFIGURATION_CATALOG.items():
        if catalog_entry.owner != "pipeline":
            continue
        entry = dict(KNOB_META.get(name, {}))
        field = fields.get(name)
        if field and field.description:
            entry["help"] = field.description
        entry.update(
            {
                "owner": "database" if catalog_entry.database_owned else "environment",
                "apply_mode": catalog_entry.apply_mode,
                "sensitivity": catalog_entry.sensitivity,
            }
        )
        metadata[name] = entry
    return metadata


@router.get("/pipeline")
async def get_pipeline_config(db: Annotated[AsyncSession, Depends(get_db)]):
    state = configuration_provider.state
    provider_health = configuration_provider.health()
    fields = PipelineSettings.model_fields
    current = configuration_provider.effective("pipeline")
    overrides = {
        key: value
        for key, value in state.values.items()
        if CONFIGURATION_CATALOG[key].owner == "pipeline"
    }
    return {
        "configuration_version": state.version,
        "etag": state.etag,
        "values": {name: _serialize(current[name]) for name in fields},
        "defaults": {name: _serialize(field.default) for name, field in fields.items()},
        "overrides": overrides,
        "restart_required": sorted(PIPELINE_RESTART_KEYS),
        "groups": KNOB_GROUPS,
        "meta": _metadata(),
        "stale": provider_health["status"] != "valid",
        "health": provider_health,
    }


@router.put("/pipeline")
async def put_pipeline_config(
    update: ConfigUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    try:
        state, changed = await update_configuration(
            db,
            expected_version=update.expected_version,
            updates=update.values,
            actor={"kind": "api", "id": "settings"},
            trigger="pipeline_api",
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
        "applied": sorted(update.values) if changed else [],
        "overrides": {
            key: value
            for key, value in state.values.items()
            if CONFIGURATION_CATALOG[key].owner == "pipeline"
        },
    }
