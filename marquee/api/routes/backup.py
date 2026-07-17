"""Internal backup management endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from marquee.api.job_submission import JobSubmissionResponse, submission_response
from marquee.core.backup import backup_service
from marquee.core.jobs.contracts import TriggerKind
from marquee.core.jobs.submission import (
    IdempotencyConflictError,
    Initiator,
    SubjectLocator,
    SubmissionError,
    submit_job,
)
from marquee.database import get_db

router = APIRouter(prefix="/api/system", tags=["system"])


@router.post("/backup", status_code=202)
async def create_backup(
    db: Annotated[AsyncSession, Depends(get_db)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
) -> JobSubmissionResponse:
    """Create a local rollback backup of managed Marquee state."""
    if db.in_transaction():
        await db.commit()
    try:
        async with db.begin():
            result = await submit_job(
                db,
                job_type="backup_create",
                request={"reason": "manual"},
                subject=SubjectLocator(kind="maintenance_scope", reference="backup-create"),
                trigger=TriggerKind.MANUAL,
                initiator=Initiator(kind="system", identifier="backup-api"),
                idempotency_key=idempotency_key,
                priority=10,
            )
    except IdempotencyConflictError as exc:
        raise HTTPException(status_code=409, detail=exc.api_detail) from exc
    except SubmissionError as exc:
        raise HTTPException(status_code=422, detail=exc.code) from exc
    return submission_response(result)


@router.get("/backups")
async def list_backups():
    """List available local rollback backups."""
    return [backup.to_dict() for backup in await backup_service.list_backups()]
