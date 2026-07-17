"""Index canonical active-overlap and exact-recovery scope.

Revision ID: 0008_jmc6e
Revises: 0007_jmc6d
Create Date: 2026-07-17
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0008_jmc6e"
down_revision: str | None = "0007_jmc6d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_jobs_active_overlap_scope",
        "jobs",
        ["phase", "type", "subject_kind", "subject_reference"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_jobs_active_overlap_scope", table_name="jobs")
