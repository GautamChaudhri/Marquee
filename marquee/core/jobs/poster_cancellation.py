"""Scoped cleanup for cancelled read-only grouped poster analysis."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import delete, select

from marquee.core.jobs.artifact_service import expire_attempt_artifacts
from marquee.core.jobs.event_service import job_event_writer
from marquee.core.jobs.work_items import terminalize_work_items
from marquee.database import _get_session_factory
from marquee.models import Job, PipelineRun

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class PosterCancellationCleanup:
    complete: bool
    projections_removed: int
    artifacts_expired: int
    warning: str | None = None


async def record_cleanup_warning(
    *,
    job_id: str,
    attempt_id: int,
    warning: str,
) -> None:
    try:
        factory = _get_session_factory()
        async with factory() as session, session.begin():
            job = await session.scalar(select(Job).where(Job.id == job_id).with_for_update())
            if job is None:
                return
            job.attention = {
                "level": "warning",
                "reason": "failed",
                "message": warning[:1_000],
                "remediation": "The remaining temporary data was quarantined for safe cleanup.",
            }
            await job_event_writer.append(
                session,
                job_id=job_id,
                attempt_id=attempt_id,
                event_key="attention.updated",
                state=job.outcome or job.phase,
                message="Cancelled poster cleanup needs attention",
                detail={"cleanup_complete": False},
                canonical_version=job.fence_token,
            )
    except Exception:  # noqa: BLE001 - warning persistence is best effort
        logger.exception("cancelled poster cleanup warning could not be persisted")


async def cleanup_cancelled_poster_attempt(
    *,
    data_dir: str | Path,
    job_id: str,
    attempt_id: int,
    fence_token: int,
) -> PosterCancellationCleanup:
    """Remove only unpublished projections and evidence owned by one cancelled fence."""
    factory = _get_session_factory()
    try:
        canonical = False
        projections_removed = 0
        async with factory() as session, session.begin():
            job = await session.scalar(select(Job).where(Job.id == job_id).with_for_update())
            canonical = bool(
                job is not None
                and job.type == "poster_pipeline_group"
                and job.outcome == "cancelled"
            )
            if canonical:
                deletion = await session.execute(
                    delete(PipelineRun).where(
                        PipelineRun.job_id == job_id,
                        PipelineRun.attempt_id == attempt_id,
                        PipelineRun.fence_token == fence_token,
                    )
                )
                projections_removed = deletion.rowcount or 0
        if not canonical:
            warning = "Cancelled poster cleanup could not prove canonical ownership."
            await record_cleanup_warning(
                job_id=job_id,
                attempt_id=attempt_id,
                warning=warning,
            )
            return PosterCancellationCleanup(False, 0, 0, warning)
    except Exception:  # noqa: BLE001 - quarantine on any uncertain deletion
        logger.exception("cancelled poster projections could not be removed")
        warning = "Cancelled poster projections could not be proven deleted."
        await record_cleanup_warning(job_id=job_id, attempt_id=attempt_id, warning=warning)
        return PosterCancellationCleanup(False, 0, 0, warning)

    try:
        artifacts = await expire_attempt_artifacts(
            data_dir=data_dir,
            job_id=job_id,
            attempt_id=attempt_id,
            family="poster_pipeline",
        )
    except Exception:  # noqa: BLE001 - quarantine on any uncertain physical cleanup
        logger.exception("cancelled poster artifacts could not be expired")
        artifacts = {"expired": 0, "failed": 1}
    items_closed = await terminalize_work_items(
        job_id=job_id,
        attempt_id=attempt_id,
        fence_token=fence_token,
        status="cancelled",
        message="Poster analysis was cancelled; temporary results were removed.",
    )
    if artifacts["failed"] or not items_closed:
        warning = (
            "Cancellation completed, but temporary poster data could not be fully deleted; "
            "the remainder was quarantined."
        )
        await record_cleanup_warning(job_id=job_id, attempt_id=attempt_id, warning=warning)
        return PosterCancellationCleanup(
            False,
            projections_removed,
            artifacts["expired"],
            warning,
        )
    return PosterCancellationCleanup(
        True,
        projections_removed,
        artifacts["expired"],
    )
