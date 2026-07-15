"""Self-heal scan — verify deployed posters still exist, restore if missing.

Catches everything webhooks can't see: deletions while Marquee was down,
Plex/Jellyfin agent overwrites, manual cleanup. Walks movies whose
``poster_path`` is set, stats the file, and on a miss calls
``PosterService.restore``. Exposed on demand (``POST /api/system/heal``) and
run periodically from the app lifespan.
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from marquee.models import Job, JobBatch

logger = logging.getLogger(__name__)


async def latest_heal_summary(db: AsyncSession) -> dict | None:
    """Return the latest terminal canonical heal parent projection."""
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
            projection.failed_total
            + projection.dead_letter_total
            + projection.unsafe_total
        ),
        "unchanged": unchanged,
        "unsupported": unsupported,
    }
