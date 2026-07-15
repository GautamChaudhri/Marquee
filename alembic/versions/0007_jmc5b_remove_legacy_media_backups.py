"""Remove the legacy physical-path media backup authority.

Revision ID: 0007_jmc5b
Revises: 0006_jmc4c
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0007_jmc5b"
down_revision: str | None = "0006_jmc4c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_table("media_backups")


def downgrade() -> None:
    # Marquee is unreleased and JMC5B intentionally removes the legacy
    # physical-path authority.  Restoring it would recreate a second mutation
    # lifecycle, so this clean-target revision is forward-only.
    raise RuntimeError("0007_jmc5b is intentionally irreversible")
