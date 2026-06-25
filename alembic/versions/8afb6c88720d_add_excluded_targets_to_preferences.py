"""add_excluded_targets_to_preferences

Revision ID: 8afb6c88720d
Revises: f2c4d6e8a901
Create Date: 2026-06-24 17:48:28.055671

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '8afb6c88720d'
down_revision: Union[str, None] = 'f2c4d6e8a901'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "radarr_overlay_profile_preferences",
        sa.Column("excluded_targets", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("radarr_overlay_profile_preferences", "excluded_targets")
