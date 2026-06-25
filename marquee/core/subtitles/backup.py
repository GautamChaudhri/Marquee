"""Tracked-backup restore/delete (design §16.7).

Backups are created by the mutation transaction; these helpers expose the
restore and explicit-delete actions. Restore copies the backup back over the
current file and re-probes; delete only ever removes the tracked backup, never
the live media.
"""

from __future__ import annotations

import contextlib
import logging
import os
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from marquee.models import MediaBackup

logger = logging.getLogger(__name__)


async def _backup_for_job(db: AsyncSession, job_id: str) -> MediaBackup | None:
    return (
        (
            await db.execute(
                select(MediaBackup)
                .where(MediaBackup.job_id == job_id)
                .order_by(MediaBackup.created_at.desc())
            )
        )
        .scalars()
        .first()
    )


async def restore_job_backup(db: AsyncSession, job_id: str) -> dict | None:
    backup = await _backup_for_job(db, job_id)
    if backup is None or backup.status not in ("available",):
        return None
    src = Path(backup.backup_path)
    dest = Path(backup.original_path)
    if not src.is_file():
        backup.status = "missing"
        await db.commit()
        return {"restored": False, "reason": "backup missing on disk"}
    from marquee.core.subtitles.config import subtitle_settings  # noqa: PLC0415
    from marquee.core.subtitles.mutation import link_or_copy  # noqa: PLC0415

    tmp = dest.with_name(f".{dest.name}.restore.tmp")
    await link_or_copy(src, tmp, bwlimit_kbps=subtitle_settings.SUBTITLE_BACKUP_COPY_BWLIMIT_KBPS)
    os.replace(tmp, dest)
    backup.status = "restored"
    await db.commit()
    logger.info("BACKUP RESTORED | job=%s | %s", job_id, dest)
    return {"restored": True, "path": str(dest)}


async def delete_job_backup(db: AsyncSession, job_id: str) -> dict | None:
    backup = await _backup_for_job(db, job_id)
    if backup is None:
        return None
    path = Path(backup.backup_path)
    with contextlib.suppress(OSError):
        path.unlink(missing_ok=True)
    backup.status = "deleted"
    await db.commit()
    return {"deleted": True}
