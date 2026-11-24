"""Baseline: the movies Radarr lists and the artwork their folders hold.

Revision ID: 0001
Revises:
Create Date: 2026-11-24
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    ]


def upgrade() -> None:
    op.create_table(
        "movies",
        sa.Column("id", sa.Integer(), sa.Identity(always=False), nullable=False),
        sa.Column("radarr_id", sa.Integer(), nullable=False),
        sa.Column("tmdb_id", sa.Integer(), nullable=True),
        sa.Column("imdb_id", sa.Text(), nullable=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("sort_title", sa.Text(), nullable=False),
        sa.Column("year", sa.Integer(), nullable=True),
        sa.Column(
            "genres",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("radarr_path", sa.Text(), nullable=False),
        sa.Column("has_file", sa.Boolean(), nullable=False),
        sa.Column("file_relative_path", sa.Text(), nullable=True),
        sa.Column("file_size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("radarr_file_id", sa.Integer(), nullable=True),
        sa.Column("radarr_added_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("director", sa.Text(), nullable=True),
        sa.Column("production_companies", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("tagline", sa.Text(), nullable=True),
        sa.Column("tmdb_enriched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_present", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_movies")),
        sa.UniqueConstraint("radarr_id", name=op.f("uq_movies_radarr_id")),
    )
    op.create_index(op.f("ix_movies_tmdb_id"), "movies", ["tmdb_id"], unique=False)

    op.create_table(
        "artwork",
        sa.Column("id", sa.Integer(), sa.Identity(always=False), nullable=False),
        sa.Column("subject_kind", sa.Text(), server_default=sa.text("'movie'"), nullable=False),
        sa.Column("movie_id", sa.Integer(), nullable=False),
        sa.Column("artwork_kind", sa.Text(), nullable=False),
        sa.Column("file_name", sa.Text(), nullable=False),
        sa.Column("sha256", sa.Text(), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("file_modified_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("origin", sa.Text(), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_artwork")),
        sa.ForeignKeyConstraint(
            ["movie_id"],
            ["movies.id"],
            name=op.f("fk_artwork_movie_id_movies"),
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "movie_id", "artwork_kind", name=op.f("uq_artwork_movie_id_artwork_kind")
        ),
        # Each kind, and the origin, has one value today; adding one is a migration.
        sa.CheckConstraint("subject_kind = 'movie'", name=op.f("ck_artwork_subject_kind")),
        sa.CheckConstraint("artwork_kind = 'poster'", name=op.f("ck_artwork_artwork_kind")),
        sa.CheckConstraint("origin = 'library'", name=op.f("ck_artwork_origin")),
        sa.CheckConstraint("sha256 ~ '^[0-9a-f]{64}$'", name=op.f("ck_artwork_sha256")),
        # A file name inside the movie folder, never a path.
        sa.CheckConstraint(
            "file_name <> '' AND file_name NOT IN ('.', '..') AND strpos(file_name, '/') = 0",
            name=op.f("ck_artwork_file_name"),
        ),
        sa.CheckConstraint("size_bytes >= 0", name=op.f("ck_artwork_size_bytes")),
    )


def downgrade() -> None:
    op.drop_table("artwork")
    op.drop_index(op.f("ix_movies_tmdb_id"), table_name="movies")
    op.drop_table("movies")
