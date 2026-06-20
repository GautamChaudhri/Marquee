"""add letterbox state, events, movie video columns

Revision ID: fd3dee2ea0da
Revises: 14e34b6bd956
Create Date: 2026-06-15 13:18:04.203483

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'fd3dee2ea0da'
down_revision: Union[str, None] = '14e34b6bd956'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── movies: encoded-video columns for the letterbox pre-filter ──────
    op.add_column('movies', sa.Column('video_width', sa.Integer(), nullable=True))
    op.add_column('movies', sa.Column('video_height', sa.Integer(), nullable=True))
    op.add_column(
        'movies',
        sa.Column('container', sa.String(length=16), nullable=True,
                  comment='Container/extension: matroska, mp4, ...'),
    )

    # ── letterbox_state: one row per movie ──────────────────────────────
    op.create_table(
        'letterbox_state',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('movie_id', sa.Integer(), nullable=False),
        sa.Column('status', sa.String(length=24), server_default=sa.text("'candidate'"), nullable=False),
        sa.Column('confidence', sa.String(length=8), nullable=True),
        sa.Column('eligible', sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column('ineligible_reason', sa.String(length=120), nullable=True),
        sa.Column('source_width', sa.Integer(), nullable=True),
        sa.Column('source_height', sa.Integer(), nullable=True),
        sa.Column('recommended_crop_top', sa.Integer(), nullable=True),
        sa.Column('recommended_crop_bottom', sa.Integer(), nullable=True),
        sa.Column('aspect_label', sa.String(length=12), nullable=True),
        sa.Column('applied_crop_top', sa.Integer(), nullable=True),
        sa.Column('applied_crop_bottom', sa.Integer(), nullable=True),
        sa.Column('detect_method', sa.String(length=16), nullable=True),
        sa.Column('samples_json', sa.Text(), nullable=True),
        sa.Column('reviewed', sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column('last_detected_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_applied_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=True),
        sa.ForeignKeyConstraint(['movie_id'], ['movies.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_letterbox_state_movie_id'), 'letterbox_state', ['movie_id'], unique=True)

    # ── letterbox_events: append-only audit trail ───────────────────────
    op.create_table(
        'letterbox_events',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('movie_id', sa.Integer(), nullable=False),
        sa.Column('action', sa.String(length=20), nullable=False),
        sa.Column('source', sa.String(length=20), nullable=False),
        sa.Column('detail', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.ForeignKeyConstraint(['movie_id'], ['movies.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_letterbox_events_movie_id'), 'letterbox_events', ['movie_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_letterbox_events_movie_id'), table_name='letterbox_events')
    op.drop_table('letterbox_events')
    op.drop_index(op.f('ix_letterbox_state_movie_id'), table_name='letterbox_state')
    op.drop_table('letterbox_state')
    op.drop_column('movies', 'container')
    op.drop_column('movies', 'video_height')
    op.drop_column('movies', 'video_width')
