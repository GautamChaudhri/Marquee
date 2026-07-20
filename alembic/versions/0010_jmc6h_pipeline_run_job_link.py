"""Link PipelineRun to the canonical poster-analysis job (JMC6H H10).

A ``PipelineRun`` becomes a durable projection of one canonical poster-analysis
attempt: its canonical job, attempt, fence, and the registered artifact of the
selected/recommended candidate. All columns are nullable so pre-JMC6H rows and any
non-job run stay valid; every foreign key is ``ON DELETE SET NULL`` so canonical
job/artifact retention never orphans or deletes the projection.

Revision ID: 0010_jmc6h
Revises: 0009_jmc6g
Create Date: 2026-07-19
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0010_jmc6h"
down_revision: str | None = "0009_jmc6g"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("pipeline_runs", sa.Column("job_id", sa.String(32), nullable=True))
    op.add_column("pipeline_runs", sa.Column("attempt_id", sa.BigInteger(), nullable=True))
    op.add_column("pipeline_runs", sa.Column("fence_token", sa.BigInteger(), nullable=True))
    op.add_column(
        "pipeline_runs", sa.Column("selected_artifact_id", sa.BigInteger(), nullable=True)
    )
    op.create_index("ix_pipeline_runs_job_id", "pipeline_runs", ["job_id"])
    op.create_foreign_key(
        "fk_pipeline_runs_job_id",
        "pipeline_runs",
        "jobs",
        ["job_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_pipeline_runs_attempt_id",
        "pipeline_runs",
        "job_attempts",
        ["attempt_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_pipeline_runs_selected_artifact_id",
        "pipeline_runs",
        "job_artifacts",
        ["selected_artifact_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_pipeline_runs_selected_artifact_id", "pipeline_runs", type_="foreignkey")
    op.drop_constraint("fk_pipeline_runs_attempt_id", "pipeline_runs", type_="foreignkey")
    op.drop_constraint("fk_pipeline_runs_job_id", "pipeline_runs", type_="foreignkey")
    op.drop_index("ix_pipeline_runs_job_id", table_name="pipeline_runs")
    op.drop_column("pipeline_runs", "selected_artifact_id")
    op.drop_column("pipeline_runs", "fence_token")
    op.drop_column("pipeline_runs", "attempt_id")
    op.drop_column("pipeline_runs", "job_id")
