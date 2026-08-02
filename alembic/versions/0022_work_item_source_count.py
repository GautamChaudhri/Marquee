"""Record how much work each contained subject brought into its run.

Revision ID: 0022_work_item_source_count
Revises: 0021_generic_work_item_stages
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0022_work_item_source_count"
down_revision: str | None = "0021_generic_work_item_stages"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("job_work_items", sa.Column("source_count", sa.Integer(), nullable=True))
    op.create_check_constraint(
        "ck_job_work_items_source_count",
        "job_work_items",
        "source_count IS NULL OR source_count >= 0",
    )


def downgrade() -> None:
    op.drop_constraint("ck_job_work_items_source_count", "job_work_items", type_="check")
    op.drop_column("job_work_items", "source_count")
