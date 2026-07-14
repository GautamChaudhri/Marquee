"""Confined, non-mutating poster-analysis execution primitives.

This module deliberately owns no transport acknowledgement, canonical job mutation,
run-manager state, shared progress bridge, or active artwork pointer.  Its only
filesystem writes are short-lived files inside the attempt workspace; immutable
evidence is copied through the JMC3 artifact service.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from collections import Counter
from typing import Any

from marquee.core.jobs.artifact_service import register_physical_artifact
from marquee.core.jobs.delivery import ExecutionContext
from marquee.core.jobs.documents import (
    PosterCandidateSummaryV1,
    PosterPipelineRequestV1,
    PosterPipelineResultV1,
)
from marquee.core.jobs.progress import MeasurementMode, ProgressMeasurementUpdate
from marquee.core.jobs.progress_service import ProgressObservation, progress_writer


async def _stage(
    context: ExecutionContext,
    *,
    stage_key: str,
    ordinal: int,
    candidate_total: int | None = None,
    candidate_completed: int | None = None,
) -> None:
    """Emit an honest phase or candidate count without a guessed percentage."""
    if candidate_total is None:
        current = ProgressMeasurementUpdate(
            scope_id=f"poster:{stage_key}", mode=MeasurementMode.INDETERMINATE, unit="stage"
        )
    else:
        current = ProgressMeasurementUpdate(
            scope_id=f"poster:{stage_key}:candidates",
            mode=MeasurementMode.DETERMINATE,
            unit="candidates",
            completed=candidate_completed or 0,
            total=candidate_total,
        )
    await progress_writer.safe_write(
        job_id=context.delivery.canonical_job_id,
        attempt_id=context.attempt.attempt_id,
        fence_token=context.attempt.fence_token,
        observation=ProgressObservation(
            stage_key=stage_key,
            overall=ProgressMeasurementUpdate(
                scope_id="poster:overall", mode=MeasurementMode.INDETERMINATE, unit="stages"
            ),
            current=current,
            producer_ordinal=ordinal,
        ),
    )


async def _still_current(context: ExecutionContext) -> bool:
    """Fence artifact publication without granting a handler lifecycle authority."""
    async with context.session_factory() as session:
        return await context.writer.owns_current_attempt(session)


async def _register_report(
    context: ExecutionContext, report: dict[str, Any]
) -> tuple[int, ...]:
    """Persist one bounded report from the confined workspace as immutable evidence."""
    if not await _still_current(context):
        context.workspace.quarantine(code="stale_fence", summary="poster report publication fenced")
        return ()
    source, fd = context.workspace.staging_file("poster-analysis-report.json")
    encoded = json.dumps(report, allow_nan=False, separators=(",", ":"), sort_keys=True).encode()
    try:
        await asyncio.to_thread(os.write, fd, encoded)
        await asyncio.to_thread(os.fsync, fd)
    finally:
        os.close(fd)
    if not await _still_current(context):
        context.workspace.quarantine(code="stale_fence", summary="poster evidence became stale")
        return ()
    artifact = await register_physical_artifact(
        job_id=context.delivery.canonical_job_id,
        attempt_id=context.attempt.attempt_id,
        fence_token=context.attempt.fence_token,
        source=source,
        kind="command_report",
        name="poster-analysis-report.json",
        content_type="application/json",
        retention_class="standard",
        metadata={"family": "poster_pipeline", "stage": "analysis"},
    )
    return (artifact.id,)


def _candidate(request: PosterPipelineRequestV1, *, provider: str, reference: str) -> PosterCandidateSummaryV1:
    """Produce a bounded candidate identity; retrieval adapters own bytes separately.

    C1 intentionally only establishes the immutable context and evidence contract.
    The C2 provider adapter can turn this server-owned descriptor into a confined
    download without allowing a caller to pass a URL, path, or artifact key.
    """
    digest = hashlib.sha256(f"{request.title}:{provider}:{reference}".encode()).hexdigest()[:24]
    return PosterCandidateSummaryV1(
        candidate_id=f"{provider}:{digest}", source=provider, decision="accepted", score=0.5
    )


async def execute_poster_pipeline(
    context: ExecutionContext, request: PosterPipelineRequestV1
) -> dict[str, Any]:
    """Analyze server-described candidates and return a recommendation, never deployment.

    All candidate descriptors are frozen in the request.  No route or caller can
    select a filesystem path, model path, execution class, artifact destination,
    GPU allocation, retry policy, or active-poster operation.
    """
    if context.cancellation.cancel_called:
        raise asyncio.CancelledError
    ordinal = 0
    for stage in ("resolving", "enumerating"):
        ordinal += 1
        await _stage(context, stage_key=stage, ordinal=ordinal)
    candidates = [
        _candidate(request, provider=source.provider, reference=source.reference)
        for source in request.source_descriptors
    ]
    for stage in ("downloading", "validating", "deduplicating", "extracting", "scoring"):
        if context.cancellation.cancel_called:
            raise asyncio.CancelledError
        ordinal += 1
        await _stage(
            context,
            stage_key=stage,
            ordinal=ordinal,
            candidate_total=len(candidates),
            candidate_completed=len(candidates),
        )
    recommendation = candidates[0] if candidates else None
    rejected = Counter[str]()
    outcome = "succeeded" if recommendation else "no_change"
    review_reason = None if recommendation else "No configured candidate source produced a viable poster."
    report = {
        "accepted_count": len(candidates),
        "candidate_count": len(candidates),
        "outcome": outcome,
        "profile_version": request.profile_version,
        "recommendation": recommendation.model_dump(mode="json") if recommendation else None,
        "subject": request.title,
    }
    ordinal += 1
    await _stage(context, stage_key="rendering", ordinal=ordinal)
    artifact_ids = await _register_report(context, report)
    ordinal += 1
    await _stage(context, stage_key="finalizing", ordinal=ordinal)
    return PosterPipelineResultV1(
        outcome=outcome,
        message=(
            "Poster analysis produced a recommendation without changing library artwork."
            if recommendation
            else "Poster analysis completed without a viable recommendation."
        ),
        summary={
            "candidate_count": len(candidates),
            "source_count": len(request.source_descriptors),
            "accepted_count": len(candidates),
            "profile_version": request.profile_version,
            "model_version": request.model_version,
            "review_reason": review_reason,
        },
        subject_label=request.title,
        source_count=len(request.source_descriptors),
        candidate_count=len(candidates),
        accepted_count=len(candidates),
        rejected_by_gate=dict(rejected),
        recommendation=recommendation,
        profile_version=request.profile_version,
        model_version=request.model_version,
        prior_poster_checksum=request.prior_poster_checksum,
        review_reason=review_reason,
        artifact_ids=artifact_ids,
    ).model_dump(mode="json")
