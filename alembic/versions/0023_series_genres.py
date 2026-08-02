"""Store Sonarr genres for television libraries.

Revision ID: 0023_series_genres
Revises: 0022_work_item_source_count
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0023_series_genres"
down_revision: str | None = "0022_work_item_source_count"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("series", sa.Column("genres", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("series", "genres")
