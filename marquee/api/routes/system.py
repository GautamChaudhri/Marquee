"""System routes — poster cache stats, webhook/heal state, manual heal."""

from __future__ import annotations

import logging
import os

from fastapi import APIRouter

from marquee.api.routes.webhooks import webhook_state
from marquee.config import settings
from marquee.core.heal import heal_scan, heal_state

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
async def system_status():
    return {
        "cache": _cache_stats(),
        "heal": heal_state,
        "webhook": webhook_state,
    }


@router.post("/heal")
async def trigger_heal():
    """Run the self-heal poster existence scan on demand."""
    return await heal_scan()
