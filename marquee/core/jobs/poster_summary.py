"""Canonical poster-parent projections used by operational APIs."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from marquee.models import Job, JobBatch


async def latest_poster_heal_summary(db: AsyncSession) -> dict[str, object] | None:
    row = (
        await db.execute(
            select(Job, JobBatch)
            .join(JobBatch, JobBatch.parent_job_id == Job.id)
            .where(Job.type == "poster_heal", Job.phase == "terminal")
            .order_by(Job.terminal_at.desc())
            .limit(1)
        )
    ).first()
    if row is None:
        return None
    job, projection = row
    request = job.request if isinstance(job.request, dict) else {}
    selected = int(request.get("selection_count", projection.created_total))
    unchanged = int(request.get("unchanged_count", 0))
    unsupported = int(request.get("unsupported_count", 0))
    return {
        "last_run": job.terminal_at.isoformat() if job.terminal_at else None,
        "checked": selected + unchanged + unsupported,
        "restored": projection.succeeded_total,
        "failed": (
            projection.failed_total + projection.dead_letter_total + projection.unsafe_total
        ),
        "unchanged": unchanged,
        "unsupported": unsupported,
    }
