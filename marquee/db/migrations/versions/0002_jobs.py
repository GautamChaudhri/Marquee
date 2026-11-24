"""Jobs: the queue, its append-only events, and when each schedule is next due.

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-24
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | Sequence[str] | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ACTIVE = sa.text("state IN ('queued', 'running')")
_QUEUED = sa.text("state = 'queued'")


def _now(name: str) -> sa.Column:
    return sa.Column(
        name, sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
    )


def upgrade() -> None:
    op.create_table(
        "jobs",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("job_class", sa.Text(), nullable=False),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("priority", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("progress", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("attempt", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("max_attempts", sa.Integer(), server_default=sa.text("1"), nullable=False),
        _now("run_after"),
        sa.Column("dedupe_key", sa.Text(), nullable=True),
        sa.Column("parent_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "cancel_requested", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        _now("created_at"),
        _now("updated_at"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_jobs")),
        sa.ForeignKeyConstraint(
            ["parent_id"], ["jobs.id"], name=op.f("fk_jobs_parent_id_jobs"), ondelete="CASCADE"
        ),
        sa.CheckConstraint(
            "job_class IN ('gpu', 'network', 'media_read', 'media_write', 'batch')",
            name=op.f("ck_jobs_job_class"),
        ),
        sa.CheckConstraint(
            "state IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')",
            name=op.f("ck_jobs_state"),
        ),
    )
    # One active job per key; a finished job no longer holds its key.
    op.create_index(
        "ix_jobs_dedupe_key_active", "jobs", ["dedupe_key"], unique=True, postgresql_where=_ACTIVE
    )
    # The claim query: the best queued job first.
    op.create_index(
        "ix_jobs_claim",
        "jobs",
        [sa.literal_column("priority DESC"), "id"],
        unique=False,
        postgresql_where=_QUEUED,
    )
    op.create_index("ix_jobs_parent_id", "jobs", ["parent_id"], unique=False)

    op.create_table(
        "job_events",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("job_id", sa.BigInteger(), nullable=False),
        _now("at"),
        sa.Column("type", sa.Text(), nullable=False),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("data", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_job_events")),
        sa.ForeignKeyConstraint(
            ["job_id"], ["jobs.id"], name=op.f("fk_job_events_job_id_jobs"), ondelete="CASCADE"
        ),
        sa.CheckConstraint(
            "type IN ('queued', 'started', 'progress', 'retry_scheduled', 'succeeded', "
            "'failed', 'cancel_requested', 'cancelled', 'requeued', 'retried')",
            name=op.f("ck_job_events_type"),
        ),
    )
    op.create_index(op.f("ix_job_events_job_id"), "job_events", ["job_id"], unique=False)

    op.create_table(
        "job_schedules",
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("next_due_at", sa.DateTime(timezone=True), nullable=False),
        # Not a foreign key: the schedule outlives any one job.
        sa.Column("last_job_id", sa.BigInteger(), nullable=True),
        _now("updated_at"),
        sa.PrimaryKeyConstraint("name", name=op.f("pk_job_schedules")),
    )


def downgrade() -> None:
    op.drop_table("job_schedules")
    op.drop_index(op.f("ix_job_events_job_id"), table_name="job_events")
    op.drop_table("job_events")
    op.drop_index("ix_jobs_parent_id", table_name="jobs")
    op.drop_index("ix_jobs_claim", table_name="jobs", postgresql_where=_QUEUED)
    op.drop_index("ix_jobs_dedupe_key_active", table_name="jobs", postgresql_where=_ACTIVE)
    op.drop_table("jobs")
