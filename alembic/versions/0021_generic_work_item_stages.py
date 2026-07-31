"""Allow definition-owned contained-work stage catalogs.

Revision ID: 0021_generic_work_item_stages
Revises: 0020_job_work_items
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0021_generic_work_item_stages"
down_revision: str | None = "0020_job_work_items"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("ck_job_work_items_stage_total", "job_work_items", type_="check")
    op.create_check_constraint(
        "ck_job_work_items_stage_total",
        "job_work_items",
        "stage_total >= 1 AND stage_total <= 100",
    )


def downgrade() -> None:
    op.drop_constraint("ck_job_work_items_stage_total", "job_work_items", type_="check")
    op.create_check_constraint(
        "ck_job_work_items_stage_total",
        "job_work_items",
        "stage_total >= 1 AND stage_total <= 9",
    )
