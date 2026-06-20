"""add child_pids and job_events index for reliability improvements

Revision ID: 85985807cac7
Revises: 9e1c0b3d7f42
Create Date: 2026-06-19 22:30:00.920875

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '85985807cac7'
down_revision: Union[str, None] = '9e1c0b3d7f42'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add child_pids column to job_attempts for orphaned process tracking
    op.add_column('job_attempts', sa.Column('child_pids', sa.JSON(), nullable=True))

    # Add index on job_events.created_at for debugging queries (all events in last hour)
    op.create_index('ix_job_events_created_at', 'job_events', ['created_at'])


def downgrade() -> None:
    # Remove index on job_events.created_at
    op.drop_index('ix_job_events_created_at', table_name='job_events')

    # Remove child_pids column from job_attempts
    op.drop_column('job_attempts', 'child_pids')
