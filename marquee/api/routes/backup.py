"""Internal backup management endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from marquee.api.routes.jobs import job_summary
from marquee.core.backup import backup_service
from marquee.core.jobs import job_manager
from marquee.database import get_db

router = APIRouter(prefix="/api/system", tags=["system"])


@router.post("/backup")
async def create_backup(db: Annotated[AsyncSession, Depends(get_db)]):
    """Create a local rollback backup of managed Marquee state."""
    job = await job_manager.create_and_run(
        db,
        job_type="backup_create",
        priority=10,
        subject_type="backup",
        subject_id="database",
        worker_id="inline-api",
    )
    return {**job_summary(job), **(job.result or {})}


@router.get("/backups")
async def list_backups():
    """List available local rollback backups."""
    return [backup.to_dict() for backup in await backup_service.list_backups()]

