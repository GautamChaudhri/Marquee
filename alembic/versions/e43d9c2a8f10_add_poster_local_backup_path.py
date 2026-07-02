"""add poster local backup path

Revision ID: e43d9c2a8f10
Revises: c7e2a4f1b903
Create Date: 2026-07-01 00:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "e43d9c2a8f10"
down_revision = "c7e2a4f1b903"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table_name in ("movies", "series", "seasons"):
        op.add_column(
            table_name,
            sa.Column(
                "poster_local_backup_path",
                sa.Text(),
                nullable=True,
                comment="Local backup copy for restoring deployed poster bytes",
            ),
        )


def downgrade() -> None:
    for table_name in ("seasons", "series", "movies"):
        op.drop_column(table_name, "poster_local_backup_path")
