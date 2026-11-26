"""Job responses: jobs, their events, the event feed, and the worker's state."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

JobState = Literal["queued", "running", "succeeded", "failed", "cancelled"]


class JobProgress(BaseModel):
    current: int | None
    total: int | None
    message: str | None


class JobSubject(BaseModel):
    """The movie a job is about."""

    model_config = ConfigDict(from_attributes=True)

    movie_id: int
    title: str
    year: int | None


class JobItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    kind: str
    job_class: Literal["gpu", "network", "media_read", "media_write", "maintenance", "batch"]
    state: JobState
    priority: int
    payload: dict[str, Any]
    result: Any | None
    error: str | None = Field(description="One line, with every secret blanked out")
    progress: JobProgress | None
    attempt: int = Field(description="How many times the job has started")
    max_attempts: int
    run_after: datetime = Field(description="A queued job does not start before this")
    dedupe_key: str | None
    parent_id: int | None = Field(description="The batch this job belongs to")
    cancel_requested: bool
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    subject: JobSubject | None = Field(
        default=None, description="The movie the payload names (a run, deploy, or restore)"
    )


class JobList(BaseModel):
    total: int
    page: int
    page_size: int
    items: list[JobItem]


class JobEventItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int = Field(description="Also the feed's cursor")
    job_id: int
    at: datetime
    type: Literal[
        "queued",
        "started",
        "progress",
        "retry_scheduled",
        "succeeded",
        "failed",
        "cancel_requested",
        "cancelled",
        "requeued",
        "retried",
    ]
    message: str | None
    data: Any | None


class JobEventList(BaseModel):
    items: list[JobEventItem]


class JobEventFeed(BaseModel):
    items: list[JobEventItem]
    next_after: int = Field(description="Pass as `after` to get the events that follow")


class WorkerInfo(BaseModel):
    running: bool = Field(description="Whether a worker holds the single-worker lock")
    since: datetime | None = Field(description="When the running worker connected")
    queued: int
    running_jobs: int
    failed: int
