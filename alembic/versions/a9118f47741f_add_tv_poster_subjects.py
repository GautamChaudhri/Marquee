"""add tv poster subjects

Revision ID: a9118f47741f
Revises: 2197cd88f27e
Create Date: 2026-07-03 18:25:11.115644

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a9118f47741f'
down_revision: Union[str, None] = '2197cd88f27e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. pipeline_runs changes
    op.alter_column("pipeline_runs", "movie_id", nullable=True)
    op.add_column("pipeline_runs", sa.Column("media_type", sa.String(10), nullable=False, server_default="movie"))
    op.add_column("pipeline_runs", sa.Column("series_id", sa.Integer(), sa.ForeignKey("series.id", ondelete="CASCADE"), nullable=True))
    op.add_column("pipeline_runs", sa.Column("season_id", sa.Integer(), sa.ForeignKey("seasons.id", ondelete="CASCADE"), nullable=True))
    op.create_index("ix_pipeline_runs_media_type", "pipeline_runs", ["media_type"])
    op.create_index("ix_pipeline_runs_series_id", "pipeline_runs", ["series_id"])
    op.create_index("ix_pipeline_runs_season_id", "pipeline_runs", ["season_id"])
    op.create_check_constraint(
        "ck_pipeline_runs_subject",
        "pipeline_runs",
        "(media_type = 'movie' AND movie_id IS NOT NULL) OR "
        "(media_type = 'series' AND series_id IS NOT NULL) OR "
        "(media_type = 'season' AND season_id IS NOT NULL)"
    )

    # 2. artwork_events changes
    op.alter_column("artwork_events", "movie_id", nullable=True)
    op.add_column("artwork_events", sa.Column("media_type", sa.String(10), nullable=False, server_default="movie"))
    op.add_column("artwork_events", sa.Column("series_id", sa.Integer(), sa.ForeignKey("series.id", ondelete="CASCADE"), nullable=True))
    op.add_column("artwork_events", sa.Column("season_id", sa.Integer(), sa.ForeignKey("seasons.id", ondelete="CASCADE"), nullable=True))
    op.create_index("ix_artwork_events_media_type", "artwork_events", ["media_type"])
    op.create_index("ix_artwork_events_series_id", "artwork_events", ["series_id"])
    op.create_index("ix_artwork_events_season_id", "artwork_events", ["season_id"])
    op.create_check_constraint(
        "ck_artwork_events_subject",
        "artwork_events",
        "(media_type = 'movie' AND movie_id IS NOT NULL) OR "
        "(media_type = 'series' AND series_id IS NOT NULL) OR "
        "(media_type = 'season' AND season_id IS NOT NULL)"
    )

    # 3. series changes
    op.add_column("series", sa.Column("tagline", sa.Text(), nullable=True))
    op.add_column("series", sa.Column("director", sa.String(500), nullable=True))
    op.add_column("series", sa.Column("production_companies_json", sa.JSON(), nullable=True))
    op.add_column("series", sa.Column("show_text_profile_id", sa.String(64), nullable=True))
    op.add_column("series", sa.Column("season_text_profile_id", sa.String(64), nullable=True))

    # 4. seasons changes
    op.add_column("seasons", sa.Column("episode_count", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("seasons", sa.Column("episode_file_count", sa.Integer(), nullable=False, server_default="0"))
    op.create_index("ix_seasons_episode_file_count", "seasons", ["episode_file_count"])


def downgrade() -> None:
    # 4. seasons downgrade
    op.drop_index("ix_seasons_episode_file_count", "seasons")
    op.drop_column("seasons", "episode_file_count")
    op.drop_column("seasons", "episode_count")

    # 3. series downgrade
    op.drop_column("series", "season_text_profile_id")
    op.drop_column("series", "show_text_profile_id")
    op.drop_column("series", "production_companies_json")
    op.drop_column("series", "director")
    op.drop_column("series", "tagline")

    # 2. artwork_events downgrade
    op.drop_constraint("ck_artwork_events_subject", "artwork_events")
    op.drop_index("ix_artwork_events_season_id", "artwork_events")
    op.drop_index("ix_artwork_events_series_id", "artwork_events")
    op.drop_index("ix_artwork_events_media_type", "artwork_events")
    op.drop_column("artwork_events", "season_id")
    op.drop_column("artwork_events", "series_id")
    op.drop_column("artwork_events", "media_type")
    op.alter_column("artwork_events", "movie_id", nullable=False)

    # 1. pipeline_runs downgrade
    op.drop_constraint("ck_pipeline_runs_subject", "pipeline_runs")
    op.drop_index("ix_pipeline_runs_season_id", "pipeline_runs")
    op.drop_index("ix_pipeline_runs_series_id", "pipeline_runs")
    op.drop_index("ix_pipeline_runs_media_type", "pipeline_runs")
    op.drop_column("pipeline_runs", "season_id")
    op.drop_column("pipeline_runs", "series_id")
    op.drop_column("pipeline_runs", "media_type")
    op.alter_column("pipeline_runs", "movie_id", nullable=False)
