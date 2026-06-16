"""System routes — cache stats, tool capabilities, queue + generator health."""

from __future__ import annotations

import logging
import os
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from marquee.api.routes.webhooks import webhook_state
from marquee.config import settings
from marquee.core.heal import heal_scan, heal_state
from marquee.core.letterbox_heal import letterbox_heal_state
from marquee.database import get_db
from marquee.media import binaries
from marquee.models import MediaJob

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/system", tags=["system"])


def _cache_stats() -> dict:
    base = settings.poster_cache_path / "movies"
    count = total_bytes = 0
    if base.is_dir():
        for entry in os.scandir(base):
            if entry.is_file() and entry.name.endswith(".jpg"):
                count += 1
                total_bytes += entry.stat().st_size
    return {"posters": count, "bytes": total_bytes}


@router.get("/status")
async def system_status(db: Annotated[AsyncSession, Depends(get_db)]):
    queue_rows = (
        await db.execute(select(MediaJob.status, func.count()).group_by(MediaJob.status))
    ).all()
    return {
        "cache": _cache_stats(),
        "heal": heal_state,
        "letterbox_heal": letterbox_heal_state,
        "webhook": webhook_state,
        "tools": binaries.availability(),
        "media_jobs": dict(queue_rows),
    }


@router.get("/status/generators")
async def generator_health():
    """Subtitle-generation provider health + capabilities."""
    from marquee.core.subtitles.generation import list_generators  # noqa: PLC0415

    return {"generators": await list_generators()}


@router.post("/heal")
async def trigger_heal():
    """Run the self-heal poster existence scan on demand."""
    return await heal_scan()
