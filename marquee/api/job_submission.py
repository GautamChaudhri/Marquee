"""Shared bounded canonical submission response for migrated initiating routes."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from marquee.core.jobs.submission import SubmissionResult


class ActiveJobConflict(BaseModel):
    code: Literal["active_overlap_conflict"] = "active_overlap_conflict"
    job_id: str
    snapshot_url: str
    detail_url: str


class JobSubmissionResponse(BaseModel):
    """Bounded canonical submission handle (never a legacy or numeric transport id)."""

    job_id: str
    disposition: Literal["created", "reused"]
    idempotent: bool
    phase: str
    snapshot_url: str
    detail_url: str
    activity_url: str
    active_conflict: ActiveJobConflict | None = None


class PlannedJobSubmissionResponse(JobSubmissionResponse):
    """Canonical submission handle plus the bounded mutation-confirmation fence."""

    plan_version: str
    configuration_version: int
    expires_at: str
    requires_confirmation: Literal[True] = True


def submission_response(result: SubmissionResult) -> JobSubmissionResponse:
    return JobSubmissionResponse(
        job_id=result.job_id,
        disposition=result.disposition,
        idempotent=getattr(result, "idempotent", result.disposition == "reused"),
        phase=result.phase,
        snapshot_url=result.snapshot_link,
        detail_url=result.detail_link,
        activity_url=getattr(
            result,
            "activity_link",
            f"/projection-room?view=queue&job={result.job_id}",
        ),
        active_conflict=None,
    )
