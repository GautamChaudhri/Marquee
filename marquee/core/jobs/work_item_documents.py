"""Public, bounded documents for grouped-poster work-item progress."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from marquee.core.jobs.documents import StrictDocument

WorkItemStatus = Literal[
    "pending",
    "running",
    "succeeded",
    "no_change",
    "review_required",
    "failed",
    "cancelled",
]


def poster_group_completion_message(*, total: int, failed: int, review: int) -> str:
    """Produce exact, grammatical operator-facing grouped-poster wording."""
    if failed >= total and total > 0:
        noun = "poster" if total == 1 else "posters"
        verb = "needs" if total == 1 else "need"
        return f"Failed: all {total} {noun} {verb} attention."
    if failed:
        verb = "needs" if failed == 1 else "need"
        message = f"Partially failed: {failed} of {total} posters {verb} attention"
        if review:
            review_verb = "is" if review == 1 else "are"
            message += f"; {review} {review_verb} ready for review"
        return f"{message}."
    noun = "poster" if review == 1 else "posters"
    verb = "is" if review == 1 else "are"
    return f"{review} {noun} {verb} ready for review."


class WorkItemStatusCounts(StrictDocument):
    pending: int = Field(default=0, ge=0)
    running: int = Field(default=0, ge=0)
    succeeded: int = Field(default=0, ge=0)
    no_change: int = Field(default=0, ge=0)
    review_required: int = Field(default=0, ge=0)
    failed: int = Field(default=0, ge=0)
    cancelled: int = Field(default=0, ge=0)


class WorkItemSummary(StrictDocument):
    version: Literal[1] = 1
    total: int = Field(ge=0, le=500)
    counts: WorkItemStatusCounts = Field(default_factory=WorkItemStatusCounts)
    sequence: int = Field(ge=0)
    updated_at: datetime | None = None
    href: str = Field(min_length=1, max_length=200)


class WorkItemProgress(StrictDocument):
    completed: int = Field(ge=0)
    total: int = Field(gt=0)
    unit: str | None = Field(default=None, max_length=32)


class WorkItemRow(StrictDocument):
    version: Literal[1] = 1
    subject_key: str = Field(min_length=1, max_length=200)
    ordinal: int = Field(ge=0, le=499)
    subject_kind: str = Field(min_length=1, max_length=40)
    subject_reference: str | None = Field(default=None, max_length=64)
    subject: dict
    status: WorkItemStatus
    stage_key: str | None = Field(default=None, max_length=80)
    stage_name: str | None = Field(default=None, max_length=100)
    stage_number: int | None = Field(default=None, ge=1, le=9)
    stage_total: int = Field(default=9, ge=1, le=9)
    progress: WorkItemProgress | None = None
    message: str | None = Field(default=None, max_length=2_000)
    sequence: int = Field(ge=0)
    updated_at: datetime


class WorkItemPage(StrictDocument):
    version: Literal[1] = 1
    job_id: str = Field(min_length=1, max_length=32)
    summary: WorkItemSummary
    items: tuple[WorkItemRow, ...]
    next_cursor: int | None = Field(default=None, ge=0, le=499)
    limit: int = Field(ge=1, le=100)
    historical_fallback: bool = False
