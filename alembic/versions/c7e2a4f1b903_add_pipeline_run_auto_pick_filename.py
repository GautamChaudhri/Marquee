"""add pipeline_runs.auto_pick_filename for review-queue thumbnails

Revision ID: c7e2a4f1b903
Revises: 6d7644acfcd3
Create Date: 2026-06-30 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c7e2a4f1b903'
down_revision: Union[str, None] = '6d7644acfcd3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Denormalized orig_filename of the run's auto-pick ("1A") so the review
    # queue can render the chosen poster without opening every archive JSON.
    op.add_column(
        'pipeline_runs',
        sa.Column('auto_pick_filename', sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('pipeline_runs', 'auto_pick_filename')
