"""Link PipelineRun to its immutable canonical run archive (JMC6H H11).

Revision ID: 0011_jmc6h
Revises: 0010_jmc6h
Create Date: 2026-07-19
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0011_jmc6h"
down_revision: str | None = "0010_jmc6h"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "pipeline_runs", sa.Column("archive_artifact_id", sa.BigInteger(), nullable=True)
    )
    op.create_foreign_key(
        "fk_pipeline_runs_archive_artifact_id",
        "pipeline_runs",
        "job_artifacts",
        ["archive_artifact_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_pipeline_runs_archive_artifact_id", "pipeline_runs", type_="foreignkey")
    op.drop_column("pipeline_runs", "archive_artifact_id")
