"""retire parallel registries and require canonical pipeline projections

Revision ID: 0012_jmc6h
Revises: 0011_jmc6h
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012_jmc6h"
down_revision: str | None = "0011_jmc6h"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_table("artifact_snapshot_movies")
    op.drop_table("artifact_snapshots")
    op.execute(
        "DELETE FROM pipeline_runs WHERE job_id IS NULL OR attempt_id IS NULL "
        "OR fence_token IS NULL OR archive_artifact_id IS NULL"
    )
    op.drop_constraint("ck_pipeline_runs_subject", "pipeline_runs", type_="check")
    op.create_check_constraint(
        "ck_pipeline_runs_subject",
        "pipeline_runs",
        "(media_type = 'movie' AND movie_id IS NOT NULL "
        "AND series_id IS NULL AND season_id IS NULL) OR "
        "(media_type = 'series' AND movie_id IS NULL "
        "AND series_id IS NOT NULL AND season_id IS NULL) OR "
        "(media_type = 'season' AND movie_id IS NULL AND season_id IS NOT NULL) OR "
        "(movie_id IS NULL AND series_id IS NULL AND season_id IS NULL)",
    )
    op.drop_constraint("fk_pipeline_runs_job_id", "pipeline_runs", type_="foreignkey")
    op.drop_constraint("fk_pipeline_runs_attempt_id", "pipeline_runs", type_="foreignkey")
    op.drop_constraint("fk_pipeline_runs_archive_artifact_id", "pipeline_runs", type_="foreignkey")
    op.alter_column("pipeline_runs", "job_id", nullable=False)
    op.alter_column("pipeline_runs", "attempt_id", nullable=False)
    op.alter_column("pipeline_runs", "fence_token", nullable=False)
    op.alter_column("pipeline_runs", "archive_artifact_id", nullable=False)
    op.create_foreign_key(
        "fk_pipeline_runs_job_id",
        "pipeline_runs",
        "jobs",
        ["job_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_pipeline_runs_attempt_id",
        "pipeline_runs",
        "job_attempts",
        ["attempt_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_pipeline_runs_archive_artifact_id",
        "pipeline_runs",
        "job_artifacts",
        ["archive_artifact_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_unique_constraint("uq_pipeline_runs_job_id", "pipeline_runs", ["job_id"])
    op.add_column("pipeline_runs", sa.Column("correlation_id", sa.String(64), nullable=True))
    op.create_index(
        "ix_pipeline_runs_correlation_id", "pipeline_runs", ["correlation_id"], unique=False
    )
    op.drop_column("pipeline_runs", "archive_path")
    op.drop_column("pipeline_runs", "output_dir")


def downgrade() -> None:
    op.add_column("pipeline_runs", sa.Column("output_dir", sa.Text(), nullable=True))
    op.add_column("pipeline_runs", sa.Column("archive_path", sa.Text(), nullable=True))
    op.drop_index("ix_pipeline_runs_correlation_id", table_name="pipeline_runs")
    op.drop_column("pipeline_runs", "correlation_id")
    op.drop_constraint("uq_pipeline_runs_job_id", "pipeline_runs", type_="unique")
    op.drop_constraint("fk_pipeline_runs_job_id", "pipeline_runs", type_="foreignkey")
    op.drop_constraint("fk_pipeline_runs_attempt_id", "pipeline_runs", type_="foreignkey")
    op.drop_constraint("fk_pipeline_runs_archive_artifact_id", "pipeline_runs", type_="foreignkey")
    op.alter_column("pipeline_runs", "job_id", nullable=True)
    op.alter_column("pipeline_runs", "attempt_id", nullable=True)
    op.alter_column("pipeline_runs", "fence_token", nullable=True)
    op.alter_column("pipeline_runs", "archive_artifact_id", nullable=True)
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
        "fk_pipeline_runs_archive_artifact_id",
        "pipeline_runs",
        "job_artifacts",
        ["archive_artifact_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.drop_constraint("ck_pipeline_runs_subject", "pipeline_runs", type_="check")
    op.create_check_constraint(
        "ck_pipeline_runs_subject",
        "pipeline_runs",
        "(media_type = 'movie' AND movie_id IS NOT NULL) OR "
        "(media_type = 'series' AND series_id IS NOT NULL) OR "
        "(media_type = 'season' AND season_id IS NOT NULL) OR "
        "(movie_id IS NULL AND series_id IS NULL AND season_id IS NULL)",
    )
    op.create_table(
        "artifact_snapshots",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("label", sa.String(length=255), nullable=False),
        sa.Column("storage_path", sa.Text(), nullable=False),
        sa.Column("active_path", sa.Text(), nullable=False),
        sa.Column("model_name", sa.String(length=80), nullable=True),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("source_mode", sa.String(length=40), nullable=True),
        sa.Column("imported_from_active", sa.Boolean(), nullable=False),
        sa.Column("summary", sa.JSON(), nullable=False),
        sa.Column("trained_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("kind", "storage_path", name="uq_artifact_snapshot_kind_storage_path"),
    )
    op.create_index(
        "ix_artifact_snapshots_kind_activated_at",
        "artifact_snapshots",
        ["kind", "activated_at"],
    )
    op.create_index(
        "ix_artifact_snapshots_kind_status",
        "artifact_snapshots",
        ["kind", "status"],
    )
    op.create_table(
        "artifact_snapshot_movies",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("artifact_id", sa.String(length=36), nullable=False),
        sa.Column("movie_id", sa.Integer(), nullable=True),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("year", sa.Integer(), nullable=True),
        sa.Column("tmdb_id", sa.Integer(), nullable=True),
        sa.Column("contribution_count", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["artifact_id"], ["artifact_snapshots.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["movie_id"], ["movies.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_artifact_snapshot_movies_artifact_id",
        "artifact_snapshot_movies",
        ["artifact_id"],
    )
    op.create_index(
        "ix_artifact_snapshot_movies_movie_id",
        "artifact_snapshot_movies",
        ["movie_id"],
    )
