"""Build a canonical fixed batch of read-only ``subtitle_scan`` children.

Shared by the manual scan routes and the deep-scan schedule.  Selection uses the existing
stale/missing candidate query; each child is a read-only ``subtitle_scan`` on one media file.
An empty candidate set yields a sealed parent that terminalizes ``no_change``.
"""

from __future__ import annotations

from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from marquee.core.jobs.batches import BatchScope, FixedBatchResult, create_fixed_batch
from marquee.core.jobs.builtin_handlers import _stale_or_missing_subtitle_scan_candidates
from marquee.core.jobs.contracts import TriggerKind
from marquee.core.jobs.submission import Initiator, SubjectLocator, SubmissionIntent


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
