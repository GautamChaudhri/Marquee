"""add tv-ready letterbox subjects

Revision ID: b1c2d3e4f5a6
Revises: c9d8e7f6a5b4
Create Date: 2026-07-04 15:15:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b1c2d3e4f5a6"
down_revision: str | None = "c9d8e7f6a5b4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column("letterbox_state", "movie_id", nullable=True)
    op.add_column(
        "letterbox_state",
        sa.Column("media_type", sa.String(length=10), server_default=sa.text("'movie'"), nullable=False),
    )
    op.add_column(
        "letterbox_state",
        sa.Column("episode_id", sa.Integer(), sa.ForeignKey("episodes.id", ondelete="CASCADE"), nullable=True),
    )
    op.add_column("letterbox_state", sa.Column("resolved_by", sa.String(length=16), nullable=True))
    op.add_column("letterbox_state", sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("letterbox_state", sa.Column("original_crop_top", sa.Integer(), nullable=True))
    op.add_column("letterbox_state", sa.Column("original_crop_bottom", sa.Integer(), nullable=True))
    op.add_column("letterbox_state", sa.Column("original_aspect_label", sa.String(length=12), nullable=True))
    op.create_index("ix_letterbox_state_media_type", "letterbox_state", ["media_type"])
    op.create_index("ix_letterbox_state_episode_id", "letterbox_state", ["episode_id"], unique=True)
    op.create_check_constraint(
        "ck_letterbox_state_subject",
        "letterbox_state",
        "(media_type = 'movie' AND movie_id IS NOT NULL AND episode_id IS NULL) OR "
        "(media_type = 'episode' AND episode_id IS NOT NULL AND movie_id IS NULL)",
    )

    op.alter_column("letterbox_events", "movie_id", nullable=True)
    op.add_column(
        "letterbox_events",
        sa.Column("media_type", sa.String(length=10), server_default=sa.text("'movie'"), nullable=False),
    )
    op.add_column(
        "letterbox_events",
        sa.Column("episode_id", sa.Integer(), sa.ForeignKey("episodes.id", ondelete="CASCADE"), nullable=True),
    )
    op.create_index("ix_letterbox_events_media_type", "letterbox_events", ["media_type"])
    op.create_index("ix_letterbox_events_episode_id", "letterbox_events", ["episode_id"], unique=False)
    op.create_check_constraint(
        "ck_letterbox_event_subject",
        "letterbox_events",
        "(media_type = 'movie' AND movie_id IS NOT NULL AND episode_id IS NULL) OR "
        "(media_type = 'episode' AND episode_id IS NOT NULL AND movie_id IS NULL)",
    )

    op.alter_column("letterbox_reencode_artifacts", "movie_id", nullable=True)
    op.add_column(
        "letterbox_reencode_artifacts",
        sa.Column("media_type", sa.String(length=10), server_default=sa.text("'movie'"), nullable=False),
    )
    op.add_column(
        "letterbox_reencode_artifacts",
        sa.Column(
            "episode_id",
            sa.Integer(),
            sa.ForeignKey("episodes.id", ondelete="CASCADE"),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_letterbox_reencode_artifacts_media_type",
        "letterbox_reencode_artifacts",
        ["media_type"],
    )
    op.create_index(
        "ix_letterbox_reencode_artifacts_episode_id",
        "letterbox_reencode_artifacts",
        ["episode_id"],
        unique=False,
    )
    op.create_check_constraint(
        "ck_letterbox_reencode_artifact_subject",
        "letterbox_reencode_artifacts",
        "(media_type = 'movie' AND movie_id IS NOT NULL AND episode_id IS NULL) OR "
        "(media_type = 'episode' AND episode_id IS NOT NULL AND movie_id IS NULL)",
    )

    op.execute("UPDATE letterbox_state SET media_type = 'movie' WHERE media_type IS NULL")
    op.execute("UPDATE letterbox_events SET media_type = 'movie' WHERE media_type IS NULL")
    op.execute(
        "UPDATE letterbox_reencode_artifacts SET media_type = 'movie' WHERE media_type IS NULL"
    )

    op.execute(
        """
        UPDATE letterbox_state AS state
        SET
            resolved_by = 'reencode',
            resolved_at = latest.updated_at,
            original_crop_top = latest.crop_top,
            original_crop_bottom = latest.crop_bottom,
            original_aspect_label = latest.aspect_label,
            status = 'reencoded',
            applied_crop_top = latest.crop_top,
            applied_crop_bottom = latest.crop_bottom,
            last_applied_at = COALESCE(state.last_applied_at, latest.updated_at)
        FROM (
            SELECT DISTINCT ON (movie_id)
                movie_id,
                crop_top,
                crop_bottom,
                updated_at,
                CASE
                    WHEN crop_top IS NULL OR crop_bottom IS NULL THEN NULL
                    ELSE CASE
                        WHEN (
                            COALESCE((detail_json::jsonb -> 'plan' -> 'source' ->> 'width')::numeric, 0) > 0
                            AND COALESCE((detail_json::jsonb -> 'plan' -> 'source' ->> 'height')::numeric, 0)
                                - COALESCE(crop_top, 0)
                                - COALESCE(crop_bottom, 0) > 0
                        )
                        THEN ROUND(
                            (detail_json::jsonb -> 'plan' -> 'source' ->> 'width')::numeric
                            / (
                                (detail_json::jsonb -> 'plan' -> 'source' ->> 'height')::numeric
                                - COALESCE(crop_top, 0)
                                - COALESCE(crop_bottom, 0)
                            ),
                            2
                        )::text || ':1'
                        ELSE NULL
                    END
                END AS aspect_label
            FROM letterbox_reencode_artifacts
            WHERE media_type = 'movie'
              AND movie_id IS NOT NULL
              AND status IN ('replaced', 'kept')
            ORDER BY movie_id, updated_at DESC NULLS LAST, id DESC
        ) AS latest
        WHERE state.media_type = 'movie'
          AND state.movie_id = latest.movie_id
        """
    )


def downgrade() -> None:
    op.drop_constraint("ck_letterbox_reencode_artifact_subject", "letterbox_reencode_artifacts")
    op.drop_index(
        "ix_letterbox_reencode_artifacts_episode_id",
        table_name="letterbox_reencode_artifacts",
    )
    op.drop_index(
        "ix_letterbox_reencode_artifacts_media_type",
        table_name="letterbox_reencode_artifacts",
    )
    op.drop_column("letterbox_reencode_artifacts", "episode_id")
    op.drop_column("letterbox_reencode_artifacts", "media_type")
    op.alter_column("letterbox_reencode_artifacts", "movie_id", nullable=False)

    op.drop_constraint("ck_letterbox_event_subject", "letterbox_events")
    op.drop_index("ix_letterbox_events_episode_id", table_name="letterbox_events")
    op.drop_index("ix_letterbox_events_media_type", table_name="letterbox_events")
    op.drop_column("letterbox_events", "episode_id")
    op.drop_column("letterbox_events", "media_type")
    op.alter_column("letterbox_events", "movie_id", nullable=False)

    op.drop_constraint("ck_letterbox_state_subject", "letterbox_state")
    op.drop_index("ix_letterbox_state_episode_id", table_name="letterbox_state")
    op.drop_index("ix_letterbox_state_media_type", table_name="letterbox_state")
    op.drop_column("letterbox_state", "original_aspect_label")
    op.drop_column("letterbox_state", "original_crop_bottom")
    op.drop_column("letterbox_state", "original_crop_top")
    op.drop_column("letterbox_state", "resolved_at")
    op.drop_column("letterbox_state", "resolved_by")
    op.drop_column("letterbox_state", "episode_id")
    op.drop_column("letterbox_state", "media_type")
    op.alter_column("letterbox_state", "movie_id", nullable=False)
