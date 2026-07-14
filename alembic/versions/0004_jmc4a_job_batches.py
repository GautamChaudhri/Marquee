"""Add the canonical transport-free batch projection.

Revision ID: 0004_jmc4a
Revises: 0003_jmc3b
Create Date: 2026-07-13
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0004_jmc4a"
down_revision: str | None = "0003_jmc3b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "job_batches",
        sa.Column("parent_job_id", sa.String(length=32), nullable=False),
        sa.Column("mode", sa.String(length=12), nullable=False),
        sa.Column("generation", sa.BigInteger(), server_default="1", nullable=False),
        sa.Column("sealed", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("sealed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sealed_child_total", sa.Integer(), nullable=True),
        sa.Column("created_total", sa.Integer(), server_default="0", nullable=False),
        sa.Column("terminal_total", sa.Integer(), server_default="0", nullable=False),
        sa.Column("succeeded_total", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "partially_succeeded_total", sa.Integer(), server_default="0", nullable=False
        ),
        sa.Column("no_change_total", sa.Integer(), server_default="0", nullable=False),
        sa.Column("failed_total", sa.Integer(), server_default="0", nullable=False),
        sa.Column("cancelled_total", sa.Integer(), server_default="0", nullable=False),
        sa.Column("superseded_total", sa.Integer(), server_default="0", nullable=False),
        sa.Column("dead_letter_total", sa.Integer(), server_default="0", nullable=False),
        sa.Column("unsafe_total", sa.Integer(), server_default="0", nullable=False),
        sa.Column("projection_sequence", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("failure_summary", sa.JSON(), nullable=True),
        sa.Column("attention_summary", sa.JSON(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("mode IN ('fixed', 'dynamic')", name="ck_job_batches_mode"),
        sa.CheckConstraint("generation >= 1", name="ck_job_batches_generation"),
        sa.CheckConstraint(
            "created_total >= 0 AND terminal_total >= 0 "
            "AND terminal_total <= created_total",
            name="ck_job_batches_totals",
        ),
        sa.CheckConstraint(
            "succeeded_total >= 0 AND partially_succeeded_total >= 0 "
            "AND no_change_total >= 0 AND failed_total >= 0 "
            "AND cancelled_total >= 0 AND superseded_total >= 0 "
            "AND dead_letter_total >= 0 AND unsafe_total >= 0",
            name="ck_job_batches_outcomes_nonnegative",
        ),
        sa.CheckConstraint(
            "terminal_total = succeeded_total + partially_succeeded_total "
            "+ no_change_total + failed_total + cancelled_total "
            "+ superseded_total + dead_letter_total + unsafe_total",
            name="ck_job_batches_terminal_outcome_sum",
        ),
        sa.CheckConstraint(
            "(sealed AND sealed_at IS NOT NULL AND sealed_child_total IS NOT NULL) "
            "OR (NOT sealed AND sealed_at IS NULL AND sealed_child_total IS NULL)",
            name="ck_job_batches_seal_consistency",
        ),
        sa.CheckConstraint(
            "sealed_child_total IS NULL OR sealed_child_total = created_total",
            name="ck_job_batches_sealed_total",
        ),
        sa.CheckConstraint("mode <> 'fixed' OR sealed", name="ck_job_batches_fixed_sealed"),
        sa.CheckConstraint(
            "projection_sequence >= 0", name="ck_job_batches_projection_sequence"
        ),
        sa.ForeignKeyConstraint(
            ["parent_job_id"], ["jobs.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("parent_job_id"),
    )


def downgrade() -> None:
    op.drop_table("job_batches")
