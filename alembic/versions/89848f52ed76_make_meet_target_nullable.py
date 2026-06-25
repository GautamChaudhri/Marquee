"""make_meet_target_nullable

Revision ID: 89848f52ed76
Revises: 8afb6c88720d
Create Date: 2026-06-24 18:15:11.157389

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '89848f52ed76'
down_revision: Union[str, None] = '8afb6c88720d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "radarr_overlay_profile_preferences",
        "meet_target",
        existing_type=sa.String(length=32),
        nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "radarr_overlay_profile_preferences",
        "meet_target",
        existing_type=sa.String(length=32),
        nullable=False,
    )
