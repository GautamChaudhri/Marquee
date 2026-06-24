"""merge jobs purge and Radarr overlay heads

Revision ID: e1f2a3b4c5d6
Revises: d8071c895bc7, b4c5d6e7f809
Create Date: 2026-06-24 00:00:00.000000

"""

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "e1f2a3b4c5d6"
down_revision: tuple[str, str] | None = ("d8071c895bc7", "b4c5d6e7f809")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
