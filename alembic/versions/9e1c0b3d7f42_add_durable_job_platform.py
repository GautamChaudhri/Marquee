"""add durable PostgreSQL job platform

Revision ID: 9e1c0b3d7f42
Revises: 806830740d24
Create Date: 2026-06-19
"""

from alembic import op
import sqlalchemy as sa


revision = "9e1c0b3d7f42"
down_revision = "806830740d24"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "jobs",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("type", sa.String(80), nullable=False),
        sa.Column("payload_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="50"),
        sa.Column("parent_id", sa.String(32), sa.ForeignKey("jobs.id", ondelete="SET NULL")),
        sa.Column("root_id", sa.String(32)),
        sa.Column("correlation_id", sa.String(64)),
        sa.Column("subject_type", sa.String(40)),
        sa.Column("subject_id", sa.String(64)),
        sa.Column("idempotency_key", sa.String(200), unique=True),
        sa.Column("current_stage", sa.String(80)),
        sa.Column("checkpoint", sa.JSON()),
        sa.Column("progress", sa.JSON()),
        sa.Column("result", sa.JSON()),
        sa.Column("error", sa.JSON()),
        sa.Column("resource_request", sa.JSON(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cancel_requested", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("pause_requested", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("claimed_at", sa.DateTime(timezone=True)),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_jobs_type", "jobs", ["type"])
    op.create_index("ix_jobs_status", "jobs", ["status"])
    op.create_index("ix_jobs_claim", "jobs", ["status", "scheduled_at", "priority", "created_at"])
    op.create_index("ix_jobs_parent_status", "jobs", ["parent_id", "status"])
    for name in ("root_id", "correlation_id", "subject_type", "subject_id"):
        op.create_index(f"ix_jobs_{name}", "jobs", [name])

    op.create_table(
        "job_attempts",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("job_id", sa.String(32), sa.ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("worker_id", sa.String(100), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("process_id", sa.Integer()),
        sa.Column("process_group_id", sa.Integer()),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("exit_code", sa.Integer()),
        sa.Column("error", sa.JSON()),
        sa.Column("metrics", sa.JSON()),
        sa.UniqueConstraint("job_id", "number", name="uq_job_attempt_number"),
    )
    op.create_index("ix_job_attempts_job_id", "job_attempts", ["job_id"])
    op.create_index("ix_job_attempts_worker_id", "job_attempts", ["worker_id"])
    op.create_table(
        "job_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("job_id", sa.String(32), sa.ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("attempt_id", sa.Integer(), sa.ForeignKey("job_attempts.id", ondelete="SET NULL")),
        sa.Column("stage", sa.String(80)),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("message", sa.Text()),
        sa.Column("detail", sa.JSON()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
    )
    op.create_index("ix_job_events_job_id", "job_events", ["job_id"])
    op.create_index("ix_job_events_job_id_id", "job_events", ["job_id", "id"])
    op.create_table("job_resources", sa.Column("key", sa.String(160), primary_key=True), sa.Column("capacity", sa.Integer(), nullable=False), sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()), sa.Column("updated_at", sa.DateTime(timezone=True)))
    op.create_table(
        "job_resource_reservations",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("job_id", sa.String(32), sa.ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("attempt_id", sa.Integer(), sa.ForeignKey("job_attempts.id", ondelete="SET NULL")),
        sa.Column("resource_key", sa.String(160), sa.ForeignKey("job_resources.key", ondelete="CASCADE"), nullable=False),
        sa.Column("units", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("stage", sa.String(80)),
        sa.Column("acquired_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("released_at", sa.DateTime(timezone=True)),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_job_resource_active", "job_resource_reservations", ["resource_key", "released_at"])
    op.create_index("ix_job_resource_reservations_job_id", "job_resource_reservations", ["job_id"])
    op.create_index("ix_job_resource_reservations_lease_expires_at", "job_resource_reservations", ["lease_expires_at"])
    op.create_table("job_schedules", sa.Column("id", sa.String(64), primary_key=True), sa.Column("job_type", sa.String(80), nullable=False), sa.Column("payload", sa.JSON(), nullable=False), sa.Column("interval_seconds", sa.Integer(), nullable=False), sa.Column("priority", sa.Integer(), nullable=False, server_default="10"), sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()), sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=False), sa.Column("last_job_id", sa.String(32)), sa.Column("last_run_at", sa.DateTime(timezone=True)))
    op.create_index("ix_job_schedules_next_run_at", "job_schedules", ["next_run_at"])
    op.create_table("job_workers", sa.Column("id", sa.String(100), primary_key=True), sa.Column("capabilities", sa.JSON(), nullable=False), sa.Column("status", sa.String(24), nullable=False), sa.Column("started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")), sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")))


def downgrade() -> None:
    for table in ("job_workers", "job_schedules", "job_resource_reservations", "job_resources", "job_events", "job_attempts", "jobs"):
        op.drop_table(table)
