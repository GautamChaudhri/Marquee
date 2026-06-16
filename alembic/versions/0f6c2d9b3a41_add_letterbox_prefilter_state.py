"""add letterbox prefilter state

Revision ID: 0f6c2d9b3a41
Revises: fd3dee2ea0da
Create Date: 2026-06-15 16:45:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0f6c2d9b3a41"
down_revision: str | None = "fd3dee2ea0da"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "letterbox_state",
        sa.Column("prefilter_bucket", sa.String(length=24), nullable=True),
    )
    op.add_column(
        "letterbox_state",
        sa.Column("prefilter_reason", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "letterbox_state",
        sa.Column("prefilter_aspect_ratio", sa.Float(), nullable=True),
    )
    op.add_column(
        "letterbox_state",
        sa.Column("last_prefiltered_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("letterbox_state", "last_prefiltered_at")
    op.drop_column("letterbox_state", "prefilter_aspect_ratio")
    op.drop_column("letterbox_state", "prefilter_reason")
    op.drop_column("letterbox_state", "prefilter_bucket")
