"""add system metrics samples

Revision ID: 6d7644acfcd3
Revises: 2b8f4e9c1d3a
Create Date: 2026-06-25 17:35:41.748179

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "6d7644acfcd3"
down_revision: str | None = "2b8f4e9c1d3a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "system_metrics_samples",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("cpu", sa.JSON(), nullable=False),
        sa.Column("gpu", sa.JSON()),
        sa.Column("ram", sa.JSON(), nullable=False),
        sa.Column("disk", sa.JSON(), nullable=False),
        sa.Column("net", sa.JSON(), nullable=False),
        sa.Column("active_jobs", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )
    op.create_index(
        "ix_system_metrics_samples_created_at", "system_metrics_samples", ["created_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_system_metrics_samples_created_at", table_name="system_metrics_samples")
    op.drop_table("system_metrics_samples")
