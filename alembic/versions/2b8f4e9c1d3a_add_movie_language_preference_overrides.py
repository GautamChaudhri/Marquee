"""add movie language preference overrides

Revision ID: 2b8f4e9c1d3a
Revises: c3a7f1d9b8e2
Create Date: 2026-06-25 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "2b8f4e9c1d3a"
down_revision: str | None = "c3a7f1d9b8e2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("movies", sa.Column("preferred_audio_languages_json", sa.JSON(), nullable=True))
    op.add_column(
        "movies", sa.Column("preferred_subtitle_languages_json", sa.JSON(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("movies", "preferred_subtitle_languages_json")
    op.drop_column("movies", "preferred_audio_languages_json")
