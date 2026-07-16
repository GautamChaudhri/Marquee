"""Build a canonical fixed batch of read-only ``subtitle_scan`` children.

Shared by the manual scan routes and the deep-scan schedule.  Selection uses the existing
stale/missing candidate query; each child is a read-only ``subtitle_scan`` on one media file.
An empty candidate set yields a sealed parent that terminalizes ``no_change``.
"""

from __future__ import annotations

import logging
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from marquee.core.jobs.batches import BatchScope, FixedBatchResult, create_fixed_batch
from marquee.core.jobs.contracts import TriggerKind
from marquee.core.jobs.submission import Initiator, SubjectLocator, SubmissionIntent
from marquee.core.media_files import MediaFileUnavailableError, resolve_media_file
from marquee.core.tv_queries import season_downloaded, series_visible
from marquee.models import (
    Episode,
    EpisodeMediaFile,
    MediaFile,
    Season,
    Series,
    SubtitleInventory,
)

logger = logging.getLogger(__name__)


def _subtitle_scan_stmt(
    scope: str,
    *,
    series_id: int | None = None,
    season_number: int | None = None,
):
    if scope == "movies":
        return select(MediaFile).where(
            MediaFile.is_active.is_(True), MediaFile.movie_id.is_not(None)
        )
    if scope == "tv":
        return (
            select(MediaFile)
            .join(EpisodeMediaFile, EpisodeMediaFile.media_file_id == MediaFile.id)
            .join(Episode, Episode.id == EpisodeMediaFile.episode_id)
            .join(Series, Series.id == Episode.series_id)
            .join(
                Season,
                (Season.series_id == Episode.series_id)
                & (Season.season_number == Episode.season_number),
            )
            .where(MediaFile.is_active.is_(True), series_visible(), season_downloaded())
        )
    if scope == "series":
        if series_id is None:
            raise RuntimeError("series_id is required when scope='series'")
        statement = (
            select(MediaFile)
            .join(EpisodeMediaFile, EpisodeMediaFile.media_file_id == MediaFile.id)
            .join(Episode, Episode.id == EpisodeMediaFile.episode_id)
            .join(
                Season,
                (Season.series_id == Episode.series_id)
                & (Season.season_number == Episode.season_number),
            )
            .where(
                MediaFile.is_active.is_(True),
                Episode.series_id == series_id,
                season_downloaded(),
            )
        )
        return (
            statement.where(Episode.season_number == season_number)
            if season_number is not None
            else statement
        )
    if scope == "all":
        return select(MediaFile).where(MediaFile.is_active.is_(True))
    raise RuntimeError(f"unsupported subtitle scan scope: {scope}")


async def _stale_or_missing_subtitle_scan_candidates(
    db: AsyncSession,
    *,
    scope: str,
    force: bool,
    series_id: int | None = None,
    season_number: int | None = None,
    limit: int | None = None,
) -> list[MediaFile]:
    statement = _subtitle_scan_stmt(
        scope, series_id=series_id, season_number=season_number
    ).outerjoin(SubtitleInventory, SubtitleInventory.media_file_id == MediaFile.id)
    statement = statement.order_by(
        SubtitleInventory.scanned_at.asc().nullsfirst(), MediaFile.id.asc()
    )
    media_files = (await db.execute(statement)).scalars().unique().all()
    candidates: list[MediaFile] = []
    for media_file in media_files:
        inventory = await db.scalar(
            select(SubtitleInventory).where(
                SubtitleInventory.media_file_id == media_file.id
            )
        )
        if force or inventory is None:
            candidates.append(media_file)
        else:
            try:
                resolved = await resolve_media_file(db, media_file.id)
            except MediaFileUnavailableError:
                logger.warning(
                    "subtitle scan candidate media_file_id=%s is unavailable; skipping",
                    media_file.id,
                )
                continue
            if inventory.file_signature != resolved.signature:
                candidates.append(media_file)
        if limit is not None and len(candidates) >= limit:
            break
    return candidates


async def create_subtitle_scan_batch(
    session: AsyncSession,
    *,
    parent_job_type: str,
    scope: str,
    force: bool,
    initiator: Initiator | None,
    series_id: int | None = None,
    season_number: int | None = None,
    limit: int | None = None,
    trigger: TriggerKind = TriggerKind.BATCH,
    idempotency_key: str | None = None,
) -> FixedBatchResult:
    """Select candidate media files and create a sealed fixed batch of subtitle scans."""
    candidates = await _stale_or_missing_subtitle_scan_candidates(
        session,
        scope=scope,
        force=force,
        series_id=series_id,
        season_number=season_number,
        limit=limit,
    )
    nonce = uuid4().hex
    children = [
        SubmissionIntent(
            job_type="subtitle_scan",
            request={"force": force},
            subject=SubjectLocator(kind="media_file", reference=str(media_file.id)),
            # Fixed-batch children are always BATCH-triggered; only the parent carries the
            # originating trigger (manual BATCH or scheduled SCHEDULE).
            trigger=TriggerKind.BATCH,
            initiator=initiator,
            idempotency_key=f"subtitle_scan:batch-{nonce}-{media_file.id}",
        )
        for media_file in candidates
    ]
    request: dict[str, object] = {"scope": scope, "force": force}
    if series_id is not None:
        request["series_id"] = series_id
    if season_number is not None:
        request["season_number"] = season_number
    return await create_fixed_batch(
        session,
        parent_job_type=parent_job_type,
        parent_request=request,
        scope=BatchScope(reference=nonce, display_name=f"Subtitle scan · {scope}"),
        trigger=trigger,
        initiator=initiator,
        idempotency_key=idempotency_key or f"{parent_job_type}:manual-{nonce}",
        children=children,
    )
