"""Health probes. No key needed: a probe cannot send one."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Request, Response, status
from sqlalchemy.exc import SQLAlchemyError

from marquee.api.schemas.health import Live, Ready
from marquee.db import migrate

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/health", tags=["health"])


@router.get("/live")
async def live() -> Live:
    """The process is up."""
    return Live()


@router.get("/ready", responses={503: {"model": Ready}})
async def ready(request: Request, response: Response) -> Ready:
    """The database answers and its schema is the newest migration."""
    try:
        async with request.app.state.engine.connect() as connection:
            current = await connection.run_sync(migrate.current_revision)
    except (SQLAlchemyError, OSError) as exc:
        logger.warning("API | readiness: database unreachable (%s)", type(exc).__name__)
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return Ready(status="not ready", reason="the database does not answer")
    if current != request.app.state.head_revision:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return Ready(
            status="not ready",
            reason="the database schema is not the newest; run `marquee db upgrade`",
        )
    return Ready(status="ready")
