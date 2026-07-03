"""add_movie_text_profile_id

Revision ID: 2197cd88f27e
Revises: a63ec7e219e2
Create Date: 2026-07-03 15:38:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "2197cd88f27e"
down_revision: str | None = "a63ec7e219e2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "movies",
        sa.Column(
            "text_profile_id",
            sa.String(length=64),
            nullable=True,
            comment="Optional per-movie text-profile override for the OCR text gate.",
        ),
    )


def downgrade() -> None:
    op.drop_column("movies", "text_profile_id")
