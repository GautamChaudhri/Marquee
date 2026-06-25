"""add dovi state

Revision ID: c3a7f1d9b8e2
Revises: 89848f52ed76
Create Date: 2026-06-25 12:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c3a7f1d9b8e2"
down_revision: str | None = "89848f52ed76"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "dovi_state",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("movie_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), server_default=sa.text("'unknown'"), nullable=False),
        sa.Column("dovi_profile", sa.Integer(), nullable=True),
        sa.Column("dovi_level", sa.Integer(), nullable=True),
        sa.Column("el_present", sa.Boolean(), nullable=True),
        sa.Column("el_type", sa.String(length=4), nullable=True),
        sa.Column("bl_signal_compatibility_id", sa.Integer(), nullable=True),
        sa.Column("source_codec", sa.String(length=16), nullable=True),
        sa.Column("rpu_summary_json", sa.Text(), nullable=True),
        sa.Column("error_reason", sa.Text(), nullable=True),
        sa.Column("last_analyzed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=True,
        ),
        sa.ForeignKeyConstraint(["movie_id"], ["movies.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_dovi_state_movie_id"),
        "dovi_state",
        ["movie_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_dovi_state_movie_id"), table_name="dovi_state")
    op.drop_table("dovi_state")
