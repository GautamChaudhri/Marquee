"""Internal backup management endpoints."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from marquee.core.backup import backup_service

router = APIRouter(prefix="/api/system", tags=["system"])


@router.post("/backup")
async def create_backup():
    """Create a local rollback backup of managed Marquee state."""
    return (await backup_service.create_backup()).to_dict()


@router.get("/backups")
async def list_backups():
    """List available local rollback backups."""
    return [backup.to_dict() for backup in await backup_service.list_backups()]


@router.post("/restore", status_code=status.HTTP_202_ACCEPTED)
async def restore_backup(backup_id: str):
    """Restore a backup and signal that a process restart is required."""
    try:
        result = await backup_service.restore_backup(backup_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return result.to_dict()


@router.delete("/backups/{backup_id}")
async def delete_backup(backup_id: str):
    """Delete a single local rollback backup directory."""
    deleted = await backup_service.delete_backup(backup_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Backup not found: {backup_id}")
    return {"backup_id": backup_id, "deleted": True}
