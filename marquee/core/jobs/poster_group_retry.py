"""Domain-coordinated expansion of grouped poster retries into single leaves."""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from marquee.core.jobs.contracts import TriggerKind
from marquee.core.jobs.documents import (
    PosterPipelineGroupRequestV1,
    PosterPipelineRequestV1,
)
from marquee.core.jobs.documents import (
    poster_pipeline_subject_key as poster_subject_key,
)
from marquee.core.jobs.submission import (
    Initiator,
    SubjectLocator,
    SubmissionIntent,
    SubmissionInvariantError,
    SubmissionValidationError,
)
from marquee.models import Job, PipelineRun


def poster_subject_locator(request: PosterPipelineRequestV1) -> SubjectLocator:
    """Build the single-leaf live subject locator for a group member."""
    key = poster_subject_key(request)
    kind, reference = key.split(":", 1)
    return SubjectLocator(kind=kind, reference=reference)


async def retryable_group_members(
    session: AsyncSession,
    *,
    group_job: Job,
) -> tuple[PosterPipelineRequestV1, ...]:
    """Validate group projection completeness and select retryable members."""
    try:
        request = PosterPipelineGroupRequestV1.model_validate(group_job.request)
    except (TypeError, ValueError) as exc:
        raise SubmissionValidationError("poster group retry request is unavailable") from exc

    members_by_key = {poster_subject_key(member): member for member in request.members}
    projections = tuple(
        (
            await session.scalars(
                select(PipelineRun)
                .where(PipelineRun.job_id == group_job.id)
                .order_by(PipelineRun.subject_key)
            )
        ).all()
    )
    if group_job.outcome == "partially_succeeded":
        projected_keys = {row.subject_key for row in projections}
        if len(projections) != len(request.members) or projected_keys != set(members_by_key):
            raise SubmissionInvariantError(
                "poster group retry requires its complete atomic member projection"
            )
        failed_keys = {row.subject_key for row in projections if row.status == "failed"}
        if not failed_keys:
            raise SubmissionValidationError("poster group has no failed members to retry")
        return tuple(member for key, member in members_by_key.items() if key in failed_keys)

    if group_job.outcome not in {"failed", "cancelled"}:
        raise SubmissionValidationError("poster group outcome is not retryable")
    if projections:
        raise SubmissionInvariantError(
            "systemic poster group retry requires zero member projections"
        )
    return tuple(request.members)


def group_member_intents(
    members: Sequence[PosterPipelineRequestV1],
    *,
    source_job: Job,
    initiator: Initiator | None,
    key_prefix: str,
) -> tuple[SubmissionIntent, ...]:
    """Create retry intents while preserving systemic all-at-once semantics."""
    original_request = getattr(source_job, "request", None)
    original_outcome = getattr(source_job, "outcome", None)
    original_id = str(getattr(source_job, "id", "group"))
    if isinstance(original_request, dict) and original_outcome in {"failed", "cancelled"}:
        try:
            group = PosterPipelineGroupRequestV1.model_validate(original_request)
        except (TypeError, ValueError):
            group = None
        if (
            group is not None
            and group.batch_mode == "all_at_once"
            and tuple(members) == group.members
        ):
            return (
                SubmissionIntent(
                    job_type="poster_pipeline_group",
                    request=group.model_dump(mode="json", exclude_none=True),
                    subject=SubjectLocator(
                        kind="poster_subject_group",
                        reference=f"{group.library}-retry-{original_id[:32]}",
                    ),
                    trigger=TriggerKind.BATCH,
                    initiator=initiator,
                    idempotency_key=f"poster_pipeline_group:{key_prefix}-all",
                    priority=source_job.priority,
                ),
            )

    return tuple(
        SubmissionIntent(
            job_type="poster_pipeline",
            request=member.model_dump(mode="json", exclude_none=True),
            subject=poster_subject_locator(member),
            trigger=TriggerKind.BATCH,
            initiator=initiator,
            idempotency_key=f"poster_pipeline:{key_prefix}-{index}",
            priority=source_job.priority,
        )
        for index, member in enumerate(members)
    )
