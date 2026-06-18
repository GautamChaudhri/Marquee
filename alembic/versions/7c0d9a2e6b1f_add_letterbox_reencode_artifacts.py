"""add letterbox reencode artifacts

Revision ID: 7c0d9a2e6b1f
Revises: c0b7f6a91e2d
Create Date: 2026-06-18 12:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "7c0d9a2e6b1f"
down_revision: str | None = "c0b7f6a91e2d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "letterbox_reencode_artifacts",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("job_id", sa.String(length=32), nullable=True),
        sa.Column("movie_id", sa.Integer(), nullable=False),
        sa.Column("media_file_id", sa.Integer(), nullable=True),
        sa.Column("original_path", sa.Text(), nullable=False),
        sa.Column("candidate_path", sa.Text(), nullable=True),
        sa.Column("saved_original_path", sa.Text(), nullable=True),
        sa.Column("original_size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("candidate_size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("saved_original_size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("original_signature", sa.String(length=128), nullable=True),
        sa.Column("candidate_signature", sa.String(length=128), nullable=True),
        sa.Column("saved_original_signature", sa.String(length=128), nullable=True),
        sa.Column("encoder", sa.String(length=40), nullable=True),
        sa.Column("encoder_family", sa.String(length=20), nullable=True),
        sa.Column("codec", sa.String(length=20), nullable=True),
        sa.Column("crop_top", sa.Integer(), nullable=False),
        sa.Column("crop_bottom", sa.Integer(), nullable=False),
        sa.Column("hdr_status", sa.String(length=24), nullable=True),
        sa.Column("dovi_status", sa.String(length=24), nullable=True),
        sa.Column("detail_json", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=24), server_default=sa.text("'candidate_ready'"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=True,
        ),
        sa.ForeignKeyConstraint(["job_id"], ["media_jobs.job_id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["media_file_id"], ["media_files.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["movie_id"], ["movies.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_letterbox_reencode_artifacts_job_id"),
        "letterbox_reencode_artifacts",
        ["job_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_letterbox_reencode_artifacts_media_file_id"),
        "letterbox_reencode_artifacts",
        ["media_file_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_letterbox_reencode_artifacts_movie_id"),
        "letterbox_reencode_artifacts",
        ["movie_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_letterbox_reencode_artifacts_movie_id"), table_name="letterbox_reencode_artifacts")
    op.drop_index(op.f("ix_letterbox_reencode_artifacts_media_file_id"), table_name="letterbox_reencode_artifacts")
    op.drop_index(op.f("ix_letterbox_reencode_artifacts_job_id"), table_name="letterbox_reencode_artifacts")
    op.drop_table("letterbox_reencode_artifacts")
