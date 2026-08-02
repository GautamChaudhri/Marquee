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

ContainedWorkSource = Literal["work_items", "child_jobs"]


def poster_group_completion_message(*, total: int, failed: int, review: int) -> str:
    """Produce exact, grammatical operator-facing grouped-poster wording."""
    # "Failed" means an error stopped the pipeline; "needs attention" means it ran
    # clean but chose nothing, so a person has to. The two are never interchangeable.
    if failed >= total and total > 0:
        noun = "poster" if total == 1 else "posters"
        return f"Failed: all {total} {noun} errored."
    if failed:
        message = f"{failed} of {total} posters failed"
        if review:
            review_verb = "needs" if review == 1 else "need"
            message += f"; {review} {review_verb} attention"
        return f"{message}."
    noun = "poster" if review == 1 else "posters"
    verb = "needs" if review == 1 else "need"
    return f"{review} {noun} {verb} attention."


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


class ContainedWorkStatusCounts(StrictDocument):
    pending: int = Field(default=0, ge=0)
    running: int = Field(default=0, ge=0)
    retrying: int = Field(default=0, ge=0)
    succeeded: int = Field(default=0, ge=0)
    no_change: int = Field(default=0, ge=0)
    review_required: int = Field(default=0, ge=0)
    failed: int = Field(default=0, ge=0)
    cancelled: int = Field(default=0, ge=0)


class ContainedWorkSummary(StrictDocument):
    """Source-neutral disclosure metadata used by every Activity card."""

    version: Literal[1] = 1
    source: ContainedWorkSource
    label: str = Field(min_length=1, max_length=100)
    item_label_singular: str = Field(min_length=1, max_length=40)
    item_label_plural: str = Field(min_length=1, max_length=40)
    total: int = Field(ge=0, le=500)
    completed: int = Field(ge=0, le=500)
    counts: ContainedWorkStatusCounts = Field(default_factory=ContainedWorkStatusCounts)
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
    stage_number: int | None = Field(default=None, ge=1, le=100)
    stage_total: int = Field(default=9, ge=1, le=100)
    progress: WorkItemProgress | None = None
    # Work this subject brought into the run. ``progress.total`` is the current
    # stage's share of it and shrinks behind each gate; this does not.
    source_count: int | None = Field(default=None, ge=0)
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
