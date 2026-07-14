"""Add bounded attempt-log sealing metadata.

Revision ID: 0003_jmc3b
Revises: 0002_jmc2a
Create Date: 2026-07-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_jmc3b"
down_revision: str | None = "0002_jmc2a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "job_logs",
        sa.Column("last_cursor", sa.BigInteger(), server_default="0", nullable=False),
    )
    op.add_column(
        "job_logs",
        sa.Column("stored_byte_count", sa.BigInteger(), server_default="0", nullable=False),
    )
    op.add_column(
        "job_logs",
        sa.Column("seal_status", sa.String(length=12), server_default="open", nullable=False),
    )
    op.add_column("job_logs", sa.Column("checksum", sa.String(length=64), nullable=True))
    op.add_column("job_logs", sa.Column("failure_code", sa.String(length=40), nullable=True))
    op.create_check_constraint("ck_job_logs_last_cursor", "job_logs", "last_cursor >= 0")
    op.create_check_constraint(
        "ck_job_logs_stored_byte_count", "job_logs", "stored_byte_count >= 0"
    )
    op.create_check_constraint(
        "ck_job_logs_seal_status",
        "job_logs",
        "seal_status IN ('open', 'recovering', 'sealed', 'failed', 'expired')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_job_logs_seal_status", "job_logs", type_="check")
    op.drop_constraint("ck_job_logs_stored_byte_count", "job_logs", type_="check")
    op.drop_constraint("ck_job_logs_last_cursor", "job_logs", type_="check")
    op.drop_column("job_logs", "failure_code")
    op.drop_column("job_logs", "checksum")
    op.drop_column("job_logs", "seal_status")
    op.drop_column("job_logs", "stored_byte_count")
    op.drop_column("job_logs", "last_cursor")
