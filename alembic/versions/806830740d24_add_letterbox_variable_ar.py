"""add letterbox variable aspect-ratio flag + note

Revision ID: 806830740d24
Revises: 7c0d9a2e6b1f
Create Date: 2026-06-18 00:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "806830740d24"
down_revision: str | None = "7c0d9a2e6b1f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "letterbox_state",
        sa.Column("variable_ar", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "letterbox_state",
        sa.Column("variable_ar_note", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("letterbox_state", "variable_ar_note")
    op.drop_column("letterbox_state", "variable_ar")
