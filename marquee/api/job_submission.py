"""Shared bounded canonical submission response for migrated initiating routes."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from marquee.core.jobs.submission import SubmissionResult


class JobSubmissionResponse(BaseModel):
    """Bounded canonical submission handle (never a legacy or numeric transport id)."""

    job_id: str
    disposition: Literal["created", "reused"]
    phase: str
    snapshot_url: str
    detail_url: str


def submission_response(result: SubmissionResult) -> JobSubmissionResponse:
    return JobSubmissionResponse(
        job_id=result.job_id,
        disposition=result.disposition,
        phase=result.phase,
        snapshot_url=result.snapshot_link,
        detail_url=result.detail_link,
    )
