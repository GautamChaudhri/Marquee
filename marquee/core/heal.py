"""Self-heal scan — verify deployed posters still exist, restore if missing.

Catches everything webhooks can't see: deletions while Marquee was down,
Plex/Jellyfin agent overwrites, manual cleanup. Walks movies whose
``poster_path`` is set, stats the file, and on a miss calls
``PosterService.restore``. Exposed on demand (``POST /api/system/heal``) and
run periodically from the app lifespan.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from marquee.config import settings
from marquee.core.poster_service import poster_service
from marquee.database import _get_session_factory
from marquee.models import Job, Movie

logger = logging.getLogger(__name__)


async def latest_heal_summary(db: AsyncSession) -> dict | None:
    """Last completed heal scan, from the durable job row.

    The scan runs as a ``poster_heal`` job in the scheduler child process, so
    an in-memory dict would never be visible here — the Job table is the only
    truthful cross-process record.
    """
    job = await db.scalar(
        select(Job)
        .where(Job.type == "poster_heal", Job.status == "succeeded")
        .order_by(Job.finished_at.desc())
        .limit(1)
    )
    if job is None:
        return None
    return {
        "last_run": job.finished_at.isoformat() if job.finished_at else None,
        "checked": (job.result or {}).get("checked", 0),
        "restored": (job.result or {}).get("restored", 0),
        "failed": (job.result or {}).get("failed", 0),
    }


async def heal_scan() -> dict:
    """Stat every deployed poster; restore the missing ones from cache/URL."""
    factory = _get_session_factory()
    checked = restored = failed = 0
    # Freshly deployed posters get a grace window: the deploy may still be
    # mid-flight in the API process, and restoring over it would clobber it.
    grace_cutoff = datetime.now(UTC) - timedelta(
        minutes=settings.HEAL_RECENT_DEPLOY_GRACE_MINUTES
    )
    async with factory() as db:
        movies = (
            (
                await db.execute(
                    select(Movie).where(
                        Movie.poster_path.is_not(None),
                        or_(
                            Movie.poster_deployed_at.is_(None),
                            Movie.poster_deployed_at < grace_cutoff,
                        ),
                    )
                )
            )
            .scalars()
            .all()
        )
        for movie in movies:
            checked += 1
            if movie.poster_path and await asyncio.to_thread(Path(movie.poster_path).is_file):
                continue
            logger.info("HEAL | poster missing for %s — restoring", movie.title)
            result = await poster_service.restore(db, movie, source="heal")
            if result.restored:
                restored += 1
            else:
                failed += 1

    logger.info(
        "HEAL | scan complete | checked=%d restored=%d failed=%d",
        checked,
        restored,
        failed,
    )
    return {"checked": checked, "restored": restored, "failed": failed}
