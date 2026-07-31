"""Canonical Marquee job model (JMC2A).

PgQueuer is the sole claim, heartbeat, stale-redelivery, retry-timing,
schedule, and concurrency authority. These tables carry product meaning and
evidence only: canonical lifecycle, dispatch audit, immutable execution
attempts, and the semantic event stream. ``JobAttempt`` has no claim, lease,
heartbeat, or recovery authority.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from marquee.database import Base

JOB_PHASES = ("planned", "queued", "running", "stopping", "terminal")
JOB_OUTCOMES = (
    "succeeded",
    "partially_succeeded",
    "no_change",
    "failed",
    "cancelled",
    "superseded",
    "dead_letter",
    "unsafe",
)
JOB_DESIRED_STATES = ("run", "pause", "cancel")
JOB_TRIGGER_KINDS = (
    "manual",
    "schedule",
    "policy",
    "batch",
    "parent",
    "healing",
    "system",
    "webhook",  # reserved; rejected while webhooks remain disabled
)
DISPATCH_DISPOSITIONS = (
    "active",
    "succeeded",
    "failed",
    "cancelled",
    "stale",
    "superseded",
    "dead_lettered",
)
ATTEMPT_PHASES = ("admitted", "running", "stopping", "finished")
ATTEMPT_OUTCOMES = ("succeeded", "failed", "cancelled", "interrupted", "retrying")
BATCH_MODES = ("fixed", "dynamic")
JOB_WORK_ITEM_STATUSES = (
    "pending",
    "running",
    "succeeded",
    "no_change",
    "review_required",
    "failed",
    "cancelled",
)


def _in_clause(column: str, values: tuple[str, ...]) -> str:
    quoted = ", ".join(f"'{value}'" for value in values)
    return f"{column} IN ({quoted})"


class Job(Base):
    """One canonical product job. Transport tickets live in PgQueuer."""

    __tablename__ = "jobs"
    __table_args__ = (
        CheckConstraint(_in_clause("phase", JOB_PHASES), name="ck_jobs_phase"),
        CheckConstraint(
            "outcome IS NULL OR " + _in_clause("outcome", JOB_OUTCOMES),
            name="ck_jobs_outcome",
        ),
        CheckConstraint(
            _in_clause("desired_state", JOB_DESIRED_STATES),
            name="ck_jobs_desired_state",
        ),
        CheckConstraint(
            _in_clause("trigger_kind", JOB_TRIGGER_KINDS),
            name="ck_jobs_trigger_kind",
        ),
        CheckConstraint(
            "(phase = 'terminal' AND outcome IS NOT NULL AND terminal_at IS NOT NULL)"
            " OR (phase <> 'terminal' AND outcome IS NULL AND terminal_at IS NULL)",
            name="ck_jobs_terminal_consistency",
        ),
        CheckConstraint("fence_token >= 0", name="ck_jobs_fence_token"),
        CheckConstraint("dispatch_generation >= 0", name="ck_jobs_dispatch_generation"),
        CheckConstraint("progress_sequence >= 0", name="ck_jobs_progress_sequence"),
        CheckConstraint("work_item_sequence >= 0", name="ck_jobs_work_item_sequence"),
        Index("ix_jobs_phase_eligible", "phase", "eligible_at", "priority", "created_at"),
        Index("ix_jobs_subject", "subject_kind", "subject_reference"),
        Index(
            "ix_jobs_active_overlap_scope",
            "phase",
            "type",
            "subject_kind",
            "subject_reference",
        ),
        Index("ix_jobs_terminal_history", "phase", "terminal_at"),
    )

    # Identity and versioning.
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    type: Mapped[str] = mapped_column(String(80), index=True, nullable=False)
    payload_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    result_version: Mapped[int] = mapped_column(
        Integer, default=1, server_default="1", nullable=False
    )
    error_version: Mapped[int] = mapped_column(
        Integer, default=1, server_default="1", nullable=False
    )

    # Documents.
    request: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    plan: Mapped[dict | None] = mapped_column(JSON)
    result: Mapped[dict | None] = mapped_column(JSON)
    error: Mapped[dict | None] = mapped_column(JSON)

    # Lifecycle.
    phase: Mapped[str] = mapped_column(
        String(16), default="planned", server_default="planned", index=True, nullable=False
    )
    outcome: Mapped[str | None] = mapped_column(String(24), index=True)
    desired_state: Mapped[str] = mapped_column(
        String(12), default="run", server_default="run", nullable=False
    )
    fence_token: Mapped[int] = mapped_column(
        BigInteger, default=0, server_default="0", nullable=False
    )
    current_attempt_id: Mapped[int | None] = mapped_column(
        ForeignKey("job_attempts.id", ondelete="SET NULL", use_alter=True)
    )
    priority: Mapped[int] = mapped_column(Integer, default=50, nullable=False)
    eligible_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    idempotency_key: Mapped[str | None] = mapped_column(String(200), unique=True)

    # Transport linkage. Dispatch history stays in job_dispatches.
    pgq_job_id: Mapped[int | None] = mapped_column(BigInteger, unique=True)
    dispatch_generation: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )

    # Policy and configuration snapshots.
    retry_policy: Mapped[dict | None] = mapped_column(JSON)
    execution_policy_id: Mapped[str | None] = mapped_column(String(80))
    configuration_version: Mapped[int | None] = mapped_column(
        ForeignKey("configuration_revisions.version", ondelete="RESTRICT")
    )
    configuration_snapshot: Mapped[dict | None] = mapped_column(JSON)

    # Hierarchy. Stable IDs survive parent deletion through the string columns.
    parent_id: Mapped[str | None] = mapped_column(
        ForeignKey("jobs.id", ondelete="SET NULL"), index=True
    )
    root_id: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    correlation_id: Mapped[str | None] = mapped_column(String(64), index=True)
    retry_of_job_id: Mapped[str | None] = mapped_column(ForeignKey("jobs.id", ondelete="SET NULL"))

    # Provenance.
    trigger_kind: Mapped[str] = mapped_column(
        String(24), default="system", server_default="system", nullable=False
    )
    initiator: Mapped[dict | None] = mapped_column(JSON)
    feature_area: Mapped[str] = mapped_column(
        String(40), default="system", server_default="system", nullable=False
    )
    presentation_family: Mapped[str | None] = mapped_column(String(40))

    # Subject. The snapshot must stay renderable after live rows are retired.
    subject_kind: Mapped[str] = mapped_column(
        String(40), default="system", server_default="system", nullable=False
    )
    subject_reference: Mapped[str | None] = mapped_column(String(64), index=True)
    subject_snapshot: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)

    # Semantic state.
    progress: Mapped[dict | None] = mapped_column(JSON)
    progress_sequence: Mapped[int] = mapped_column(
        BigInteger, default=0, server_default="0", nullable=False
    )
    progress_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    current_stage: Mapped[str | None] = mapped_column(String(80))
    current_subject: Mapped[dict | None] = mapped_column(JSON)
    attention: Mapped[dict | None] = mapped_column(JSON)
    work_item_sequence: Mapped[int] = mapped_column(
        BigInteger, default=0, server_default="0", nullable=False
    )
    work_item_summary: Mapped[dict | None] = mapped_column(JSON)
    work_item_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Time.
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    planned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    queued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    stopping_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    terminal_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class JobWorkItem(Base):
    """Durable observation-only progress for one grouped poster library asset."""

    __tablename__ = "job_work_items"
    __table_args__ = (
        CheckConstraint(
            "ordinal >= 0 AND ordinal < 500",
            name="ck_job_work_items_ordinal",
        ),
        CheckConstraint("fence_token >= 0", name="ck_job_work_items_fence_token"),
        CheckConstraint("update_sequence >= 0", name="ck_job_work_items_update_sequence"),
        CheckConstraint(
            _in_clause("status", JOB_WORK_ITEM_STATUSES),
            name="ck_job_work_items_status",
        ),
        CheckConstraint(
            "stage_number IS NULL OR (stage_number >= 1 AND stage_number <= stage_total)",
            name="ck_job_work_items_stage_number",
        ),
        CheckConstraint(
            "stage_total >= 1 AND stage_total <= 9",
            name="ck_job_work_items_stage_total",
        ),
        CheckConstraint(
            "(completed IS NULL AND total IS NULL) OR "
            "(completed >= 0 AND total > 0 AND completed <= total)",
            name="ck_job_work_items_progress",
        ),
        UniqueConstraint("job_id", "ordinal", name="uq_job_work_items_job_ordinal"),
    )

    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), primary_key=True)
    subject_key: Mapped[str] = mapped_column(String(200), primary_key=True)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    subject_kind: Mapped[str] = mapped_column(String(40), nullable=False)
    subject_reference: Mapped[str | None] = mapped_column(String(64), nullable=True)
    subject_snapshot: Mapped[dict] = mapped_column(JSON, nullable=False)
    attempt_id: Mapped[int | None] = mapped_column(
        ForeignKey("job_attempts.id", ondelete="SET NULL"), nullable=True
    )
    fence_token: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[str] = mapped_column(
        String(24), default="pending", server_default="pending", nullable=False
    )
    stage_key: Mapped[str | None] = mapped_column(String(80), nullable=True)
    stage_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    stage_total: Mapped[int] = mapped_column(Integer, default=9, server_default="9", nullable=False)
    completed: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total: Mapped[int | None] = mapped_column(Integer, nullable=True)
    unit: Mapped[str | None] = mapped_column(String(32), nullable=True)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    update_sequence: Mapped[int] = mapped_column(
        BigInteger, default=0, server_default="0", nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class JobDispatch(Base):
    """Immutable link between a canonical generation and one PgQueuer ticket."""

    __tablename__ = "job_dispatches"
    __table_args__ = (
        UniqueConstraint("job_id", "generation", name="uq_job_dispatch_generation"),
        CheckConstraint(
            _in_clause("disposition", DISPATCH_DISPOSITIONS),
            name="ck_job_dispatches_disposition",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(
        ForeignKey("jobs.id", ondelete="CASCADE"), index=True, nullable=False
    )
    generation: Mapped[int] = mapped_column(Integer, nullable=False)
    pgq_job_id: Mapped[int | None] = mapped_column(BigInteger, unique=True)
    entrypoint: Mapped[str] = mapped_column(String(80), nullable=False)
    dedupe_key: Mapped[str] = mapped_column(String(200), unique=True, nullable=False)
    priority: Mapped[int] = mapped_column(Integer, nullable=False)
    eligible_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    disposition: Mapped[str] = mapped_column(
        String(16), default="active", server_default="active", nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    # Set only when Marquee legitimately observes the delivery being picked.
    picked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class JobBatch(Base):
    """Transport-free coordination projection for one canonical parent job."""

    __tablename__ = "job_batches"
    __table_args__ = (
        CheckConstraint(_in_clause("mode", BATCH_MODES), name="ck_job_batches_mode"),
        CheckConstraint("generation >= 1", name="ck_job_batches_generation"),
        CheckConstraint(
            "created_total >= 0 AND terminal_total >= 0 AND terminal_total <= created_total",
            name="ck_job_batches_totals",
        ),
        CheckConstraint(
            "succeeded_total >= 0 AND partially_succeeded_total >= 0 "
            "AND no_change_total >= 0 AND failed_total >= 0 "
            "AND cancelled_total >= 0 AND superseded_total >= 0 "
            "AND dead_letter_total >= 0 AND unsafe_total >= 0",
            name="ck_job_batches_outcomes_nonnegative",
        ),
        CheckConstraint(
            "terminal_total = succeeded_total + partially_succeeded_total "
            "+ no_change_total + failed_total + cancelled_total "
            "+ superseded_total + dead_letter_total + unsafe_total",
            name="ck_job_batches_terminal_outcome_sum",
        ),
        CheckConstraint(
            "(sealed AND sealed_at IS NOT NULL AND sealed_child_total IS NOT NULL) "
            "OR (NOT sealed AND sealed_at IS NULL AND sealed_child_total IS NULL)",
            name="ck_job_batches_seal_consistency",
        ),
        CheckConstraint(
            "sealed_child_total IS NULL OR sealed_child_total = created_total",
            name="ck_job_batches_sealed_total",
        ),
        CheckConstraint("mode <> 'fixed' OR sealed", name="ck_job_batches_fixed_sealed"),
        CheckConstraint("projection_sequence >= 0", name="ck_job_batches_projection_sequence"),
    )

    parent_job_id: Mapped[str] = mapped_column(
        ForeignKey("jobs.id", ondelete="CASCADE"), primary_key=True
    )
    mode: Mapped[str] = mapped_column(String(12), nullable=False)
    generation: Mapped[int] = mapped_column(
        BigInteger, default=1, server_default="1", nullable=False
    )
    sealed: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )
    sealed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sealed_child_total: Mapped[int | None] = mapped_column(Integer)
    created_total: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    terminal_total: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    succeeded_total: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    partially_succeeded_total: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    no_change_total: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    failed_total: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    cancelled_total: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    superseded_total: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    dead_letter_total: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    unsafe_total: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    projection_sequence: Mapped[int] = mapped_column(
        BigInteger, default=0, server_default="0", nullable=False
    )
    failure_summary: Mapped[dict | None] = mapped_column(JSON)
    attention_summary: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class SchemaContract(Base):
    """Verified runtime schema marker written only by the migration service."""

    __tablename__ = "schema_contracts"

    component: Mapped[str] = mapped_column(String(32), primary_key=True)
    expected_version: Mapped[str] = mapped_column(String(64), nullable=False)
    durability: Mapped[str | None] = mapped_column(String(16))
    catalog_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    verified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    verifier_build: Mapped[str] = mapped_column(String(64), nullable=False)


class JobAttempt(Base):
    """Immutable execution/admission evidence. Never a lease or claim."""

    __tablename__ = "job_attempts"
    __table_args__ = (
        UniqueConstraint("job_id", "number", name="uq_job_attempt_number"),
        UniqueConstraint("job_id", "fence_token", name="uq_job_attempt_fence"),
        CheckConstraint(_in_clause("phase", ATTEMPT_PHASES), name="ck_job_attempts_phase"),
        CheckConstraint(
            "outcome IS NULL OR " + _in_clause("outcome", ATTEMPT_OUTCOMES),
            name="ck_job_attempts_outcome",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(
        ForeignKey("jobs.id", ondelete="CASCADE"), index=True, nullable=False
    )
    number: Mapped[int] = mapped_column(Integer, nullable=False)
    fence_token: Mapped[int] = mapped_column(BigInteger, nullable=False)

    # Transport identity of the delivery that created this attempt.
    pgq_job_id: Mapped[int | None] = mapped_column(BigInteger)
    transport_attempt: Mapped[int | None] = mapped_column(Integer)

    # Logical node/build identity plus the exact durable process incarnation.
    worker_node_id: Mapped[str | None] = mapped_column(String(100), index=True)
    worker_build: Mapped[str | None] = mapped_column(String(64))
    runtime_instance_id: Mapped[str | None] = mapped_column(
        ForeignKey("runtime_instances.id", ondelete="SET NULL"), index=True
    )

    phase: Mapped[str] = mapped_column(
        String(16), default="admitted", server_default="admitted", nullable=False
    )
    outcome: Mapped[str | None] = mapped_column(String(24))
    failure_class: Mapped[str | None] = mapped_column(String(40))

    admitted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    stopping_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Process identity placeholders. Chunk 3 fills these; nothing reads them
    # for recovery, and there is deliberately no heartbeat or lease column.
    process_id: Mapped[int | None] = mapped_column(Integer)
    process_group_id: Mapped[int | None] = mapped_column(Integer)
    cgroup_path: Mapped[str | None] = mapped_column(String(200))
    host_boot_id: Mapped[str | None] = mapped_column(String(64))
    process_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    exit_code: Mapped[int | None] = mapped_column(Integer)
    exit_signal: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[dict | None] = mapped_column(JSON)
    metrics: Mapped[dict | None] = mapped_column(JSON)


class JobEvent(Base):
    """Append-only semantic event stream; ``id`` is the global durable cursor."""

    __tablename__ = "job_events"
    __table_args__ = (
        Index("ix_job_events_job_id_id", "job_id", "id"),
        Index("ix_job_events_created_at", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(
        ForeignKey("jobs.id", ondelete="CASCADE"), index=True, nullable=False
    )
    attempt_id: Mapped[int | None] = mapped_column(
        ForeignKey("job_attempts.id", ondelete="SET NULL")
    )
    event_key: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    stage: Mapped[str | None] = mapped_column(String(80))
    message: Mapped[str | None] = mapped_column(Text)
    detail: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
