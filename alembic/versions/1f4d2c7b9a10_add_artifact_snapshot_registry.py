"""add artifact snapshot registry

Revision ID: 1f4d2c7b9a10
Revises: 806830740d24, c7e2a4f1b903
Create Date: 2026-07-03 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "1f4d2c7b9a10"
down_revision: tuple[str, str] | None = ("806830740d24", "c7e2a4f1b903")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
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
        sa.Column("imported_from_active", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("summary", sa.JSON(), nullable=False),
        sa.Column("trained_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("kind", "storage_path", name="uq_artifact_snapshot_kind_storage_path"),
    )
    op.create_index(
        "ix_artifact_snapshots_kind_status",
        "artifact_snapshots",
        ["kind", "status"],
        unique=False,
    )
    op.create_index(
        "ix_artifact_snapshots_kind_activated_at",
        "artifact_snapshots",
        ["kind", "activated_at"],
        unique=False,
    )

    op.create_table(
        "artifact_snapshot_movies",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("artifact_id", sa.String(length=36), nullable=False),
        sa.Column("movie_id", sa.Integer(), nullable=True),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("year", sa.Integer(), nullable=True),
        sa.Column("tmdb_id", sa.Integer(), nullable=True),
        sa.Column("contribution_count", sa.Integer(), nullable=False, server_default="1"),
        sa.ForeignKeyConstraint(["artifact_id"], ["artifact_snapshots.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["movie_id"], ["movies.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_artifact_snapshot_movies_artifact_id",
        "artifact_snapshot_movies",
        ["artifact_id"],
        unique=False,
    )
    op.create_index(
        "ix_artifact_snapshot_movies_movie_id",
        "artifact_snapshot_movies",
        ["movie_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_artifact_snapshot_movies_movie_id", table_name="artifact_snapshot_movies")
    op.drop_index("ix_artifact_snapshot_movies_artifact_id", table_name="artifact_snapshot_movies")
    op.drop_table("artifact_snapshot_movies")
    op.drop_index("ix_artifact_snapshots_kind_activated_at", table_name="artifact_snapshots")
    op.drop_index("ix_artifact_snapshots_kind_status", table_name="artifact_snapshots")
    op.drop_table("artifact_snapshots")
