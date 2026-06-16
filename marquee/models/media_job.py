"""Durable media jobs/batches/events (design §22).

A persisted queue — NOT the in-memory ``RunManager`` — backs every media-file
mutation so work survives restarts, can be cancelled/paused, and carries an
auditable plan + result. ``MediaJobEvent`` rows are the durable backing store
for the SSE stream (history replay → live), mirroring the pipeline run events.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from marquee.database import Base


class MediaBatch(Base):
    """A group of child media jobs (e.g. a library-wide policy apply)."""

    __tablename__ = "media_batches"

    batch_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    operation: Mapped[str] = mapped_column(String(30), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="planned", server_default="'planned'"
    )
    requested_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    request_json: Mapped[str | None] = mapped_column(JSON, nullable=True)
    summary_json: Mapped[str | None] = mapped_column(JSON, nullable=True)
    paused: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")
    cancel_requested: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=True
    )


class MediaJob(Base):
    """One durable media-file operation (scan/remove/embed/extract/.../generate)."""

    __tablename__ = "media_jobs"

    job_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    batch_id: Mapped[str | None] = mapped_column(
        ForeignKey("media_batches.batch_id", ondelete="SET NULL"), index=True, nullable=True
    )
    media_file_id: Mapped[int | None] = mapped_column(
        ForeignKey("media_files.id", ondelete="CASCADE"), index=True, nullable=True
    )

    # subtitle_scan | subtitle_remove | subtitle_embed | subtitle_extract
    #   | subtitle_metadata | subtitle_policy | subtitle_generate | subtitle_restore
    operation: Mapped[str] = mapped_column(String(30), nullable=False)
    # planned | queued | running | succeeded | failed | cancelled | interrupted
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="planned", server_default="'planned'", index=True
    )
    stage: Mapped[str | None] = mapped_column(String(30), nullable=True)
    progress_done: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    progress_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # manual | batch | policy | webhook
    trigger: Mapped[str] = mapped_column(String(12), nullable=False, default="manual")

    request_json: Mapped[str | None] = mapped_column(JSON, nullable=True)
    plan_json: Mapped[str | None] = mapped_column(JSON, nullable=True)
    result_json: Mapped[str | None] = mapped_column(JSON, nullable=True)
    error_json: Mapped[str | None] = mapped_column(JSON, nullable=True)

    input_signature: Mapped[str | None] = mapped_column(String(128), nullable=True)
    plan_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(
        String(200), unique=True, nullable=True
    )
    cancel_requested: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0"
    )
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=True
    )

    def __repr__(self) -> str:
        return (
            f"<MediaJob(job_id={self.job_id!r}, op={self.operation!r}, "
            f"status={self.status!r})>"
        )


class MediaJobEvent(Base):
    """Append-only progress event for a media job (durable SSE backing)."""

    __tablename__ = "media_job_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(
        ForeignKey("media_jobs.job_id", ondelete="CASCADE"), index=True, nullable=False
    )
    stage: Mapped[str | None] = mapped_column(String(30), nullable=True)
    state: Mapped[str | None] = mapped_column(String(20), nullable=True)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    progress_json: Mapped[str | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
