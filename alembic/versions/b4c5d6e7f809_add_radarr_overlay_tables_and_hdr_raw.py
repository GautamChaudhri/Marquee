"""add Radarr overlay tables plus raw HDR/cutoff movie fields

Revision ID: b4c5d6e7f809
Revises: a1b2c3d4e5f6
Create Date: 2026-06-24 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b4c5d6e7f809"
down_revision: str | None = "a1b2c3d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("movies", sa.Column("quality_cutoff_met", sa.Boolean(), nullable=True))
    op.add_column("movies", sa.Column("hdr_type_raw", sa.String(length=64), nullable=True))

    op.create_table(
        "radarr_custom_formats",
        sa.Column("id", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("include_when_renaming", sa.Boolean(), nullable=False),
        sa.Column("specifications_json", sa.JSON(), nullable=True),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "radarr_quality_profiles",
        sa.Column("id", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("upgrade_allowed", sa.Boolean(), nullable=True),
        sa.Column("cutoff_format_score", sa.Integer(), nullable=True),
        sa.Column("min_format_score", sa.Integer(), nullable=True),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "radarr_profile_format_items",
        sa.Column("profile_id", sa.Integer(), nullable=False),
        sa.Column("custom_format_id", sa.Integer(), nullable=False),
        sa.Column("score", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["custom_format_id"], ["radarr_custom_formats.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["profile_id"], ["radarr_quality_profiles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("profile_id", "custom_format_id"),
    )
    op.create_table(
        "movie_custom_format_scores",
        sa.Column("movie_id", sa.Integer(), nullable=False),
        sa.Column("custom_format_id", sa.Integer(), nullable=False),
        sa.Column("score", sa.Integer(), nullable=False),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["custom_format_id"], ["radarr_custom_formats.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["movie_id"], ["movies.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("movie_id", "custom_format_id"),
    )


def downgrade() -> None:
    op.drop_table("movie_custom_format_scores")
    op.drop_table("radarr_profile_format_items")
    op.drop_table("radarr_quality_profiles")
    op.drop_table("radarr_custom_formats")
    op.drop_column("movies", "hdr_type_raw")
    op.drop_column("movies", "quality_cutoff_met")
