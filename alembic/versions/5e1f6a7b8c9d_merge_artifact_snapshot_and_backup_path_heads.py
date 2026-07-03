"""merge artifact snapshot and backup path heads

Revision ID: 5e1f6a7b8c9d
Revises: 1f4d2c7b9a10, e43d9c2a8f10
Create Date: 2026-07-03 00:00:01.000000

"""

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "5e1f6a7b8c9d"
down_revision: tuple[str, str] | None = ("1f4d2c7b9a10", "e43d9c2a8f10")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
