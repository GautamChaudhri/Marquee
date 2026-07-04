"""add episode audio subs and series overrides

Revision ID: c9d8e7f6a5b4
Revises: aaff5a275d56
Create Date: 2026-07-04 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "c9d8e7f6a5b4"
down_revision: str | None = "aaff5a275d56"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("episodes", sa.Column("audio_languages_json", sa.JSON(), nullable=True))
    op.add_column("episodes", sa.Column("subtitle_languages_json", sa.JSON(), nullable=True))
    op.add_column("series", sa.Column("preferred_audio_languages_json", sa.JSON(), nullable=True))
    op.add_column("series", sa.Column("preferred_subtitle_languages_json", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("series", "preferred_subtitle_languages_json")
    op.drop_column("series", "preferred_audio_languages_json")
    op.drop_column("episodes", "subtitle_languages_json")
    op.drop_column("episodes", "audio_languages_json")
