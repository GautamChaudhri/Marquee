"""add tv hdr and sonarr overlay schema

Revision ID: aaff5a275d56
Revises: a9118f47741f
Create Date: 2026-07-03 00:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "aaff5a275d56"
down_revision: str | None = "a9118f47741f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Episode HDR columns
    op.add_column("episodes", sa.Column("hdr_type_raw", sa.String(length=64), nullable=True))
    op.add_column("episodes", sa.Column("video_width", sa.Integer(), nullable=True))
    op.add_column("episodes", sa.Column("video_height", sa.Integer(), nullable=True))

    # 2. Sonarr overlay tables (mirror Radarr, minus custom-format scores)
    op.create_table(
        "sonarr_custom_formats",
        sa.Column("id", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("include_when_renaming", sa.Boolean(), nullable=False),
        sa.Column("specifications_json", sa.JSON(), nullable=True),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "sonarr_quality_profiles",
        sa.Column("id", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("upgrade_allowed", sa.Boolean(), nullable=True),
        sa.Column("cutoff_format_score", sa.Integer(), nullable=True),
        sa.Column("min_format_score", sa.Integer(), nullable=True),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "sonarr_profile_format_items",
        sa.Column("profile_id", sa.Integer(), nullable=False),
        sa.Column("custom_format_id", sa.Integer(), nullable=False),
        sa.Column("score", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["custom_format_id"], ["sonarr_custom_formats.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["profile_id"], ["sonarr_quality_profiles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("profile_id", "custom_format_id"),
    )
    op.create_table(
        "sonarr_overlay_profile_preferences",
        sa.Column("profile_id", sa.Integer(), nullable=False),
        sa.Column("meet_target", sa.String(length=32), nullable=True),
        sa.Column("exceed_target", sa.String(length=32), nullable=True),
        sa.Column("excluded_targets", sa.JSON(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["profile_id"], ["sonarr_quality_profiles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("profile_id"),
    )

    # 3. DoviState subject generalization (movie | episode)
    op.alter_column("dovi_state", "movie_id", nullable=True)
    op.add_column(
        "dovi_state",
        sa.Column("media_type", sa.String(length=10), server_default=sa.text("'movie'"), nullable=False),
    )
    op.add_column(
        "dovi_state",
        sa.Column("episode_id", sa.Integer(), sa.ForeignKey("episodes.id", ondelete="CASCADE"), nullable=True),
    )
    op.create_index("ix_dovi_state_media_type", "dovi_state", ["media_type"])
    op.create_index("ix_dovi_state_episode_id", "dovi_state", ["episode_id"], unique=True)
    op.create_check_constraint(
        "ck_dovi_state_subject",
        "dovi_state",
        "(media_type = 'movie' AND movie_id IS NOT NULL) OR "
        "(media_type = 'episode' AND episode_id IS NOT NULL)",
    )


def downgrade() -> None:
    # 3. DoviState downgrade
    op.drop_constraint("ck_dovi_state_subject", "dovi_state")
    op.drop_index("ix_dovi_state_episode_id", "dovi_state")
    op.drop_index("ix_dovi_state_media_type", "dovi_state")
    op.drop_column("dovi_state", "episode_id")
    op.drop_column("dovi_state", "media_type")
    op.alter_column("dovi_state", "movie_id", nullable=False)

    # 2. Sonarr overlay tables downgrade
    op.drop_table("sonarr_overlay_profile_preferences")
    op.drop_table("sonarr_profile_format_items")
    op.drop_table("sonarr_quality_profiles")
    op.drop_table("sonarr_custom_formats")

    # 1. Episode HDR columns downgrade
    op.drop_column("episodes", "video_height")
    op.drop_column("episodes", "video_width")
    op.drop_column("episodes", "hdr_type_raw")
