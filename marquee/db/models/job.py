"""The job queue's tables: jobs, their append-only events, and when each schedule is due."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from marquee.db.base import Base, TimestampMixin

JOB_STATES = ("queued", "running", "succeeded", "failed", "cancelled")
ACTIVE_STATES = ("queued", "running")
FINAL_STATES = ("succeeded", "failed", "cancelled")
JOB_CLASSES = ("gpu", "network", "media_read", "media_write", "batch")
EVENT_TYPES = (
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
)


def _one_of(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class Job(TimestampMixin, Base):
    """One unit of work. A ``batch`` row is a parent the worker never runs itself."""

    __tablename__ = "jobs"
    __table_args__ = (
        CheckConstraint(_one_of("job_class", JOB_CLASSES), name="job_class"),
        CheckConstraint(_one_of("state", JOB_STATES), name="state"),
        # One active job per key; a finished job no longer holds its key.
        Index(
            "ix_jobs_dedupe_key_active",
            "dedupe_key",
            unique=True,
            postgresql_where=text(_one_of("state", ACTIVE_STATES)),
        ),
        # The claim query: the best queued job first.
        Index(
            "ix_jobs_claim",
            text("priority DESC"),
            "id",
            postgresql_where=text("state = 'queued'"),
        ),
        Index("ix_jobs_parent_id", "parent_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    job_class: Mapped[str] = mapped_column(Text, nullable=False)
    state: Mapped[str] = mapped_column(Text, nullable=False)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    # None is SQL NULL in these columns, not the JSON value null.
    result: Mapped[Any | None] = mapped_column(JSONB(none_as_null=True), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    progress: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True), nullable=True)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    run_after: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    dedupe_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    parent_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("jobs.id", ondelete="CASCADE"), nullable=True
    )
    cancel_requested: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    def __repr__(self) -> str:
        return f"<Job id={self.id} kind={self.kind!r} state={self.state!r}>"


class JobEvent(Base):
    """One thing that happened to a job. Append-only; ``id`` is also the feed's cursor."""

    __tablename__ = "job_events"
    __table_args__ = (CheckConstraint(_one_of("type", EVENT_TYPES), name="type"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    job_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    type: Mapped[str] = mapped_column(Text, nullable=False)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    data: Mapped[Any | None] = mapped_column(JSONB(none_as_null=True), nullable=True)


class JobSchedule(Base):
    """When a schedule declared in code is next due. The schedules themselves live in code."""

    __tablename__ = "job_schedules"

    name: Mapped[str] = mapped_column(Text, primary_key=True)
    next_due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # Not a foreign key: the schedule outlives any one job.
    last_job_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
