"""What this installation is and how it is set. Read-only; needs the API key."""

from __future__ import annotations

from fastapi import APIRouter, Request

from marquee.api import system
from marquee.api.schemas.system import SystemInfo
from marquee.api.sessions import Session

router = APIRouter(tags=["system"])


@router.get("/system")
async def get_system(session: Session, request: Request) -> SystemInfo:
    """Version, schema, the settings that shape behaviour, library counts, model files, the
    last sync, and how much the data directory holds. No secret and no environment."""
    return await system.summary(session, request.app.state.head_revision)
