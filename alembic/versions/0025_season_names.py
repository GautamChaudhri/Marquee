"""Persist official TMDB season names.

Revision ID: 0025_season_names
Revises: 0024_managed_settings_secrets
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0025_season_names"
down_revision: str | None = "0024_managed_settings_secrets"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "seasons",
        sa.Column(
            "name",
            sa.String(length=500),
            nullable=True,
            comment="Official season name from TMDB",
        ),
    )


def downgrade() -> None:
    op.drop_column("seasons", "name")
