"""add durable grouped-poster work-item projections

Revision ID: 0020_job_work_items
Revises: 0019_pipeline_subject_key
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0020_job_work_items"
down_revision: str | None = "0019_pipeline_subject_key"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "jobs",
        sa.Column("work_item_sequence", sa.BigInteger(), server_default="0", nullable=False),
    )
    op.add_column("jobs", sa.Column("work_item_summary", sa.JSON(), nullable=True))
    op.add_column(
        "jobs",
        sa.Column("work_item_updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_check_constraint(
        "ck_jobs_work_item_sequence",
        "jobs",
        "work_item_sequence >= 0",
    )

    op.create_table(
        "job_work_items",
        sa.Column("job_id", sa.String(length=32), nullable=False),
        sa.Column("subject_key", sa.String(length=200), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("subject_kind", sa.String(length=40), nullable=False),
        sa.Column("subject_reference", sa.String(length=64), nullable=True),
        sa.Column("subject_snapshot", sa.JSON(), nullable=False),
        sa.Column("attempt_id", sa.BigInteger(), nullable=True),
        sa.Column("fence_token", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(length=24), server_default="pending", nullable=False),
        sa.Column("stage_key", sa.String(length=80), nullable=True),
        sa.Column("stage_number", sa.Integer(), nullable=True),
        sa.Column("stage_total", sa.Integer(), server_default="9", nullable=False),
        sa.Column("completed", sa.Integer(), nullable=True),
        sa.Column("total", sa.Integer(), nullable=True),
        sa.Column("unit", sa.String(length=32), nullable=True),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("update_sequence", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "ordinal >= 0 AND ordinal < 500",
            name="ck_job_work_items_ordinal",
        ),
        sa.CheckConstraint("fence_token >= 0", name="ck_job_work_items_fence_token"),
        sa.CheckConstraint("update_sequence >= 0", name="ck_job_work_items_update_sequence"),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'succeeded', 'no_change', "
            "'review_required', 'failed', 'cancelled')",
            name="ck_job_work_items_status",
        ),
        sa.CheckConstraint(
            "stage_number IS NULL OR (stage_number >= 1 AND stage_number <= stage_total)",
            name="ck_job_work_items_stage_number",
        ),
        sa.CheckConstraint(
            "stage_total >= 1 AND stage_total <= 9",
            name="ck_job_work_items_stage_total",
        ),
        sa.CheckConstraint(
            "(completed IS NULL AND total IS NULL) OR "
            "(completed >= 0 AND total > 0 AND completed <= total)",
            name="ck_job_work_items_progress",
        ),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["attempt_id"], ["job_attempts.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("job_id", "subject_key"),
        sa.UniqueConstraint("job_id", "ordinal", name="uq_job_work_items_job_ordinal"),
    )


def downgrade() -> None:
    op.drop_table("job_work_items")
    op.drop_constraint("ck_jobs_work_item_sequence", "jobs", type_="check")
    op.drop_column("jobs", "work_item_updated_at")
    op.drop_column("jobs", "work_item_summary")
    op.drop_column("jobs", "work_item_sequence")
