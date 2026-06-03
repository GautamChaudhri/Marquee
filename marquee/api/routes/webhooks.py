"""Webhook routes — receive events from Radarr and Sonarr."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])


@router.post("/radarr")
async def radarr_webhook():
    """Receive Radarr webhook events (upgrade, rename, add).
    Phase 4 — not yet implemented."""
    raise HTTPException(status_code=501, detail="Not implemented — Phase 4")


@router.post("/sonarr")
async def sonarr_webhook():
    """Receive Sonarr webhook events (upgrade, rename, add).
    Phase 4 — not yet implemented."""
    raise HTTPException(status_code=501, detail="Not implemented — Phase 4")
