"""merge subtitle and letterbox prefilter heads

Revision ID: c0b7f6a91e2d
Revises: 0f6c2d9b3a41, 8ad76a0e2c1f
Create Date: 2026-06-16 01:20:00.000000

"""
from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "c0b7f6a91e2d"
down_revision: tuple[str, str] | None = ("0f6c2d9b3a41", "8ad76a0e2c1f")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
