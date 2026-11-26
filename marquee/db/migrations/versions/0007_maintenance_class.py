"""Jobs may be of class ``maintenance``.

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-26

The database backup and the retention job run in a class of their own, ``maintenance``
(one at a time), so they never wait behind a run or take the slot of a write. This widens
the check constraint on ``jobs.job_class`` and does nothing else. The downgrade puts the
old constraint back, which fails, leaving everything as it was, while any job of class
``maintenance`` is kept.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0007"
down_revision: str | Sequence[str] | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD = "job_class IN ('gpu', 'network', 'media_read', 'media_write', 'batch')"
_NEW = "job_class IN ('gpu', 'network', 'media_read', 'media_write', 'maintenance', 'batch')"


def upgrade() -> None:
    op.drop_constraint(op.f("ck_jobs_job_class"), "jobs", type_="check")
    op.create_check_constraint(op.f("ck_jobs_job_class"), "jobs", _NEW)


def downgrade() -> None:
    op.drop_constraint(op.f("ck_jobs_job_class"), "jobs", type_="check")
    op.create_check_constraint(op.f("ck_jobs_job_class"), "jobs", _OLD)
