"""add pipeline_run timings, duration, and batch_id for metrics + batch runs

Revision ID: a1b2c3d4e5f6
Revises: 85985807cac7
Create Date: 2026-06-21 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, None] = '85985807cac7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Per-stage timings + total duration so the metrics endpoint can aggregate
    # stage cost and throughput without opening every archived run JSON.
    op.add_column('pipeline_runs', sa.Column('timings_json', sa.Text(), nullable=True))
    op.add_column('pipeline_runs', sa.Column('duration_seconds', sa.Float(), nullable=True))
    # Group every per-movie run produced by one cross-movie batch.
    op.add_column('pipeline_runs', sa.Column('batch_id', sa.String(length=32), nullable=True))
    op.create_index('ix_pipeline_runs_batch_id', 'pipeline_runs', ['batch_id'])


def downgrade() -> None:
    op.drop_index('ix_pipeline_runs_batch_id', table_name='pipeline_runs')
    op.drop_column('pipeline_runs', 'batch_id')
    op.drop_column('pipeline_runs', 'duration_seconds')
    op.drop_column('pipeline_runs', 'timings_json')
