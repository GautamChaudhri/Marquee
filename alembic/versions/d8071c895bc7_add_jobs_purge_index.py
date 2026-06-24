"""add ix_jobs_purge index for the job-retention purge query

Revision ID: d8071c895bc7
Revises: a1b2c3d4e5f6
Create Date: 2026-06-23 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'd8071c895bc7'
down_revision: Union[str, None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # The job_retention_purge handler filters terminal jobs by finished_at,
    # which ix_jobs_claim (status, scheduled_at, priority, created_at) doesn't
    # cover.
    op.create_index('ix_jobs_purge', 'jobs', ['status', 'finished_at'])


def downgrade() -> None:
    op.drop_index('ix_jobs_purge', table_name='jobs')
