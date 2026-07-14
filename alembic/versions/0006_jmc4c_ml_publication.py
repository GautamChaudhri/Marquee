"""Add fenced active pointers for immutable ML artifacts.

Revision ID: 0006_jmc4c
Revises: 0005_jmc4b
Create Date: 2026-07-14
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0006_jmc4c"
down_revision: str | None = "0005_jmc4b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ml_active_publications",
        sa.Column("family", sa.String(length=48), nullable=False),
        sa.Column("generation", sa.BigInteger(), nullable=False),
        sa.Column("artifact_id", sa.BigInteger(), nullable=False),
        sa.Column("version", sa.String(length=96), nullable=False),
        sa.Column("checksum", sa.String(length=64), nullable=False),
        sa.Column("job_id", sa.String(length=32), nullable=False),
        sa.Column("attempt_id", sa.BigInteger(), nullable=False),
        sa.Column("fence_token", sa.BigInteger(), nullable=False),
        sa.Column(
            "activated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "generation >= 1", name="ck_ml_active_publications_generation"
        ),
        sa.CheckConstraint(
            "fence_token >= 1", name="ck_ml_active_publications_fence"
        ),
        sa.ForeignKeyConstraint(["artifact_id"], ["job_artifacts.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["attempt_id"], ["job_attempts.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("family"),
        sa.UniqueConstraint("artifact_id"),
    )


def downgrade() -> None:
    op.drop_table("ml_active_publications")
