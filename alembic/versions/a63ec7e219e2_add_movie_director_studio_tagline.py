"""add_movie_director_studio_tagline

Revision ID: a63ec7e219e2
Revises: 5e1f6a7b8c9d
Create Date: 2026-07-03 13:09:22.303669

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a63ec7e219e2"
down_revision: str | None = "5e1f6a7b8c9d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "movies",
        sa.Column(
            "director",
            sa.String(length=500),
            nullable=True,
            comment="Director name from TMDB movie credits — used by the text gate to identify director-credit text on posters.",
        ),
    )
    op.add_column(
        "movies",
        sa.Column(
            "production_companies_json",
            sa.JSON(),
            nullable=True,
            comment="Production company names from TMDB — used by the text gate to identify studio branding text on posters (e.g. 'Marvel Studios').",
        ),
    )
    op.add_column(
        "movies",
        sa.Column(
            "tagline",
            sa.Text(),
            nullable=True,
            comment="Movie tagline from TMDB — used by the text gate to allow matching promotional text on posters.",
        ),
    )


def downgrade() -> None:
    op.drop_column("movies", "tagline")
    op.drop_column("movies", "production_companies_json")
    op.drop_column("movies", "director")
