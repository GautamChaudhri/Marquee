"""Durable, non-authoritative evidence attached to canonical jobs."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from marquee.database import Base


class JobLog(Base):
    """Metadata for one confined attempt-log segment; capture arrives in JMC3."""

    __tablename__ = "job_logs"
    __table_args__ = (
        UniqueConstraint("attempt_id", "segment", name="uq_job_log_attempt_segment"),
        CheckConstraint("segment >= 0", name="ck_job_logs_segment"),
        CheckConstraint("byte_count >= 0", name="ck_job_logs_byte_count"),
        CheckConstraint("line_count >= 0", name="ck_job_logs_line_count"),
        CheckConstraint("last_cursor >= 0", name="ck_job_logs_last_cursor"),
        CheckConstraint("stored_byte_count >= 0", name="ck_job_logs_stored_byte_count"),
        CheckConstraint(
            "storage_key NOT LIKE '/%' AND storage_key NOT LIKE '%..%'",
            name="ck_job_logs_confined_storage_key",
        ),
        CheckConstraint(
            "format IN ('text', 'jsonl')",
            name="ck_job_logs_format",
        ),
        CheckConstraint(
            "compression IN ('none', 'gzip', 'zstd')",
            name="ck_job_logs_compression",
        ),
        CheckConstraint(
            "seal_status IN ('open', 'recovering', 'sealed', 'failed', 'expired')",
            name="ck_job_logs_seal_status",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(
        ForeignKey("jobs.id", ondelete="CASCADE"), index=True, nullable=False
    )
    attempt_id: Mapped[int] = mapped_column(
        ForeignKey("job_attempts.id", ondelete="CASCADE"), index=True, nullable=False
    )
    segment: Mapped[int] = mapped_column(Integer, nullable=False)
    storage_key: Mapped[str] = mapped_column(String(300), nullable=False)
    format: Mapped[str] = mapped_column(String(12), nullable=False, server_default="text")
    encoding: Mapped[str] = mapped_column(String(20), nullable=False, server_default="utf-8")
    compression: Mapped[str] = mapped_column(String(12), nullable=False, server_default="none")
    byte_count: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    line_count: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    last_cursor: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    stored_byte_count: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default="0"
    )
    redacted: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    truncated: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    seal_status: Mapped[str] = mapped_column(
        String(12), nullable=False, server_default="open"
    )
    checksum: Mapped[str | None] = mapped_column(String(64))
    failure_code: Mapped[str | None] = mapped_column(String(40))
    opened_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    retention_class: Mapped[str] = mapped_column(
        String(24), nullable=False, server_default="standard"
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class JobArtifact(Base):
    """Metadata for a physical confined object or virtual evidence document."""

    __tablename__ = "job_artifacts"
    __table_args__ = (
        CheckConstraint(
            "(storage_key IS NOT NULL) <> (virtual_source IS NOT NULL)",
            name="ck_job_artifacts_one_source",
        ),
        CheckConstraint(
            "storage_key IS NULL OR (storage_key NOT LIKE '/%' AND storage_key NOT LIKE '%..%')",
            name="ck_job_artifacts_confined_storage_key",
        ),
        CheckConstraint(
            "status IN ('pending', 'available', 'failed', 'expired')",
            name="ck_job_artifacts_status",
        ),
        CheckConstraint("size_bytes IS NULL OR size_bytes >= 0", name="ck_job_artifacts_size"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(
        ForeignKey("jobs.id", ondelete="CASCADE"), index=True, nullable=False
    )
    attempt_id: Mapped[int | None] = mapped_column(
        ForeignKey("job_attempts.id", ondelete="SET NULL"), index=True, nullable=True
    )
    kind: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="available")
    storage_key: Mapped[str | None] = mapped_column(String(300), nullable=True)
    virtual_source: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    content_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    checksum: Mapped[str | None] = mapped_column(String(128), nullable=True)
    artifact_metadata: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSON, nullable=True)
    retention_class: Mapped[str] = mapped_column(
        String(24), nullable=False, server_default="standard"
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class WorkerNode(Base):
    """Observed worker identity and telemetry, never execution ownership."""

    __tablename__ = "worker_nodes"
    __table_args__ = (
        CheckConstraint(
            "readiness IN ('starting', 'ready', 'not_ready', 'stopped')",
            name="ck_worker_nodes_readiness",
        ),
    )

    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    build: Mapped[str | None] = mapped_column(String(64), nullable=True)
    boot_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    capabilities: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    readiness: Mapped[str] = mapped_column(String(16), nullable=False, server_default="starting")
    drain_requested: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )
    telemetry: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, server_default=func.now(), onupdate=func.now()
    )


class MediaOperationDetail(Base):
    """Strict 1:1 media-operation evidence with an optional live-file link."""

    __tablename__ = "media_operation_details"

    job_id: Mapped[str] = mapped_column(
        ForeignKey("jobs.id", ondelete="CASCADE"), primary_key=True
    )
    operation_kind: Mapped[str] = mapped_column(String(60), nullable=False, index=True)
    media_file_id: Mapped[int | None] = mapped_column(
        ForeignKey("media_files.id", ondelete="SET NULL"), index=True, nullable=True
    )
    media_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    target_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    input_signature: Mapped[str] = mapped_column(String(160), nullable=False)
    plan_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    confirmation: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    requested_target: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    expected_target: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    actual_target: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    validation: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    atomicity: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    backup: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    publish: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, server_default=func.now(), onupdate=func.now()
    )
