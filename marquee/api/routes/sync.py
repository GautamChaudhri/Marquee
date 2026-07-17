"""Sync routes — trigger a canonical *arr → database library-sync job."""

from __future__ import annotations

import logging
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from marquee.api.job_submission import JobSubmissionResponse, submission_response
from marquee.core.jobs.contracts import TriggerKind
from marquee.core.jobs.submission import (
    IdempotencyConflictError,
    Initiator,
    SubjectLocator,
    SubmissionError,
    submit_job,
)
from marquee.database import get_db
from marquee.models import Job

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/sync", tags=["sync"])


@router.post("/all", status_code=status.HTTP_202_ACCEPTED)
async def sync_all(
    db: Annotated[AsyncSession, Depends(get_db)],
) -> JobSubmissionResponse:
    """Submit a library synchronization job and return its canonical handle (202).

    Library sync is a canonical ``network`` job (Radarr/Sonarr/TMDB + database only).  An
    in-flight sync is reused rather than starting a concurrent full-library sync.
    """
    async with db.begin():
        active = await db.scalar(
            select(Job)
            .where(Job.type == "library_sync", Job.outcome.is_(None))
            .order_by(Job.queued_at.desc())
            .limit(1)
        )
        if active is not None:
            return JobSubmissionResponse(
                job_id=active.id,
                disposition="reused",
                idempotent=True,
                phase=active.phase,
                snapshot_url=f"/api/jobs/{active.id}/snapshot",
                detail_url=f"/projection-room/jobs/{active.id}",
                activity_url=f"/projection-room?view=queue&job={active.id}",
            )
        try:
            result = await submit_job(
                db,
                job_type="library_sync",
                request={"source": "manual"},
                subject=SubjectLocator(kind="maintenance_scope", reference="library-sync"),
                trigger=TriggerKind.MANUAL,
                initiator=Initiator(kind="system", identifier="sync-api"),
                idempotency_key=f"library_sync:manual-{uuid4().hex}",
            )
        except IdempotencyConflictError as exc:
            raise HTTPException(status_code=409, detail=exc.api_detail) from exc
        except SubmissionError as exc:
            raise HTTPException(status_code=422, detail=exc.code) from exc

    logger.info("library sync job %s submitted (%s)", result.job_id, result.disposition)
    return submission_response(result)
