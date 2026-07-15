"""Sealed policy-batch construction (JMC5B B05/B18).

The parent evaluates the policy **once**, freezes the resulting per-file plan into
each child's immutable request, and seals the child set in one transaction.  A
policy edited after this point cannot be adopted by an already-sealed batch,
because no child ever re-reads live policy.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from marquee.core.jobs.audio_subtitle_documents import SubtitleGenerateRequestV1
from marquee.core.jobs.batches import BatchScope, FixedBatchResult, create_fixed_batch
from marquee.core.jobs.contracts import TriggerKind
from marquee.core.jobs.submission import Initiator, SubjectLocator, SubmissionIntent
from marquee.core.jobs.track_selectors import TrackSelectorV1

#: Bound on one sealed policy batch; discovery never grows unbounded.
MAX_POLICY_CHILDREN = 500


@dataclass(frozen=True, slots=True)
class FrozenFilePlan:
    """One file's evaluated policy decision, frozen before any child exists."""

    media_file_id: int
    remove_selectors: tuple[TrackSelectorV1, ...]


def policy_child_key(parent_key: str, media_file_id: int) -> str:
    """Deterministic, path-free child idempotency key."""
    return f"subtitle_policy:{parent_key}:{media_file_id}"


async def create_subtitle_policy_batch(
    session: AsyncSession,
    *,
    policy_id: int,
    policy_revision: int,
    plans: Sequence[FrozenFilePlan],
    idempotency_key: str,
    initiator: Initiator,
    scope: str = "all",
    priority: int = 35,
) -> FixedBatchResult:
    """Seal one immutable child plan per file (B18)."""
    if len(plans) > MAX_POLICY_CHILDREN:
        raise ValueError("a sealed policy batch exceeds its bounded child cap")
    seen = {plan.media_file_id for plan in plans}
    if len(seen) != len(plans):
        raise ValueError("a media file may only appear once in a sealed policy batch")

    children = [
        SubmissionIntent(
            job_type="subtitle_policy",
            request={
                "media_file_id": plan.media_file_id,
                "policy_id": policy_id,
                "policy_revision": policy_revision,
                # The evaluated decision is frozen here, not re-read by the child.
                "remove_selectors": [
                    selector.model_dump(mode="json") for selector in plan.remove_selectors
                ],
            },
            subject=SubjectLocator(kind="media_file", reference=str(plan.media_file_id)),
            trigger=TriggerKind.BATCH,
            initiator=initiator,
            idempotency_key=policy_child_key(idempotency_key, plan.media_file_id),
            priority=priority,
        )
        for plan in plans
    ]
    return await create_fixed_batch(
        session,
        parent_job_type="subtitle_policy_batch",
        parent_request={
            "policy_id": policy_id,
            "policy_revision": policy_revision,
            "scope": scope,
            "selection_count": len(children),
        },
        scope=BatchScope(
            reference=hashlib.sha256(idempotency_key.encode()).hexdigest()[:32],
            display_name="Apply subtitle policy",
            summary=f"{len(children)} files, policy {policy_id} revision {policy_revision}",
        ),
        # Parent-only definitions accept PARENT/BATCH triggers, never MANUAL.
        trigger=TriggerKind.BATCH,
        initiator=initiator,
        idempotency_key=idempotency_key,
        children=children,
        priority=priority,
    )


async def create_subtitle_generate_batch(
    session: AsyncSession,
    *,
    requests: Sequence[SubtitleGenerateRequestV1],
    idempotency_key: str,
    initiator: Initiator,
    series_id: int,
    season_number: int | None,
    parent_job_type: str = "subtitle_generate_batch",
    priority: int = 35,
) -> FixedBatchResult:
    """Seal generation requests before creating any transport-backed child."""
    if parent_job_type != "subtitle_generate_batch":
        raise ValueError("generation batches use the canonical parent definition")
    if len(requests) > MAX_POLICY_CHILDREN:
        raise ValueError("a sealed generation batch exceeds its bounded child cap")
    media_file_ids = [request.media_file_id for request in requests]
    if len(set(media_file_ids)) != len(media_file_ids):
        raise ValueError("a media file may only appear once in a sealed generation batch")
    children = [
        SubmissionIntent(
            job_type="subtitle_generate",
            request=request.model_dump(mode="json"),
            subject=SubjectLocator(kind="media_file", reference=str(request.media_file_id)),
            trigger=TriggerKind.BATCH,
            initiator=initiator,
            idempotency_key=policy_child_key(idempotency_key, request.media_file_id),
            priority=priority,
        )
        for request in requests
    ]
    return await create_fixed_batch(
        session,
        parent_job_type=parent_job_type,
        parent_request={
            "scope": "season" if season_number is not None else "series",
            "series_id": series_id,
            "season_number": season_number,
        },
        scope=BatchScope(
            reference=hashlib.sha256(idempotency_key.encode()).hexdigest()[:32],
            display_name="Generate TV subtitles",
            summary=f"{len(children)} episode files",
        ),
        trigger=TriggerKind.BATCH,
        initiator=initiator,
        idempotency_key=idempotency_key,
        children=children,
        priority=priority,
    )
