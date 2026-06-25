"""add overlay preferences and current CF score

Revision ID: f2c4d6e8a901
Revises: e1f2a3b4c5d6
Create Date: 2026-06-24 16:10:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f2c4d6e8a901"
down_revision: str | None = "e1f2a3b4c5d6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("movies", sa.Column("current_cf_score", sa.Integer(), nullable=True))

    op.create_table(
        "radarr_overlay_profile_preferences",
        sa.Column("profile_id", sa.Integer(), nullable=False),
        sa.Column("meet_target", sa.String(length=32), nullable=False),
        sa.Column("exceed_target", sa.String(length=32), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["profile_id"],
            ["radarr_quality_profiles.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("profile_id"),
    )


def downgrade() -> None:
    op.drop_table("radarr_overlay_profile_preferences")
    op.drop_column("movies", "current_cf_score")
