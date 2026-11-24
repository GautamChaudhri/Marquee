"""Taste: preference events, exemplars, and taste profiles.

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-24

The downgrade drops the three tables, and with them every recorded act of feedback.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: str | Sequence[str] | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SHA256 = "~ '^[0-9a-f]{64}$'"
_ACTIVE_POSITIVE = sa.text("state = 'active' AND polarity = 'positive'")
_ACTIVE = sa.text("state = 'active'")
_IS_ACTIVE = sa.text("is_active")


def _now(name: str) -> sa.Column:
    return sa.Column(
        name, sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
    )


def _plain_name(column: str) -> str:
    return f"{column} <> '' AND {column} NOT IN ('.', '..') AND strpos({column}, '/') = 0"


def upgrade() -> None:
    op.create_table(
        "preference_events",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("subject_kind", sa.Text(), server_default=sa.text("'movie'"), nullable=False),
        sa.Column("movie_id", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.BigInteger(), nullable=True),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("chosen_source_file", sa.Text(), nullable=True),
        sa.Column("chosen_sha256", sa.Text(), nullable=True),
        sa.Column("pick_source_file", sa.Text(), nullable=True),
        sa.Column("profile_sha256", sa.Text(), nullable=True),
        sa.Column("scorer_name", sa.Text(), nullable=True),
        sa.Column("blind", sa.Boolean(), nullable=True),
        sa.Column("exposed", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("context", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("revokes_event_id", sa.BigInteger(), nullable=True),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        _now("created_at"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_preference_events")),
        sa.ForeignKeyConstraint(
            ["movie_id"], ["movies.id"], name=op.f("fk_preference_events_movie_id_movies")
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["poster_runs.id"],
            name=op.f("fk_preference_events_run_id_poster_runs"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["revokes_event_id"],
            ["preference_events.id"],
            name=op.f("fk_preference_events_revokes_event_id_preference_events"),
        ),
        sa.UniqueConstraint("revokes_event_id", name=op.f("uq_preference_events_revokes_event_id")),
        sa.UniqueConstraint("idempotency_key", name=op.f("uq_preference_events_idempotency_key")),
        sa.CheckConstraint(
            "subject_kind = 'movie'", name=op.f("ck_preference_events_subject_kind")
        ),
        sa.CheckConstraint(
            "action IN ('approval', 'override', 'rank', 'reject', 'revoke')",
            name=op.f("ck_preference_events_action"),
        ),
        sa.CheckConstraint(
            "(action = 'revoke') = (revokes_event_id IS NOT NULL)",
            name=op.f("ck_preference_events_revokes"),
        ),
        sa.CheckConstraint(
            f"chosen_sha256 {_SHA256}", name=op.f("ck_preference_events_chosen_sha256")
        ),
        sa.CheckConstraint(
            f"profile_sha256 {_SHA256}", name=op.f("ck_preference_events_profile_sha256")
        ),
        sa.CheckConstraint(
            _plain_name("chosen_source_file"), name=op.f("ck_preference_events_chosen_source_file")
        ),
        sa.CheckConstraint(
            _plain_name("pick_source_file"), name=op.f("ck_preference_events_pick_source_file")
        ),
        sa.CheckConstraint(
            "jsonb_typeof(exposed) = 'array' AND jsonb_array_length(exposed) <= 100",
            name=op.f("ck_preference_events_exposed"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(context) = 'object'", name=op.f("ck_preference_events_context")
        ),
        sa.CheckConstraint(
            "length(idempotency_key) BETWEEN 1 AND 160",
            name=op.f("ck_preference_events_idempotency_key"),
        ),
    )
    op.create_index(
        op.f("ix_preference_events_movie_id"), "preference_events", ["movie_id"], unique=False
    )
    op.create_index(
        op.f("ix_preference_events_run_id"), "preference_events", ["run_id"], unique=False
    )

    op.create_table(
        "exemplars",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("subject_kind", sa.Text(), server_default=sa.text("'movie'"), nullable=False),
        sa.Column("movie_id", sa.Integer(), nullable=False),
        sa.Column("polarity", sa.Text(), nullable=False),
        sa.Column("weight", sa.Double(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("event_id", sa.BigInteger(), nullable=True),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("sha256", sa.Text(), nullable=False),
        sa.Column("file_name", sa.Text(), nullable=False),
        sa.Column("staged_name", sa.Text(), nullable=True),
        sa.Column("source_file", sa.Text(), nullable=True),
        sa.Column("replaced_exemplar_id", sa.BigInteger(), nullable=True),
        _now("created_at"),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_exemplars")),
        sa.ForeignKeyConstraint(
            ["movie_id"], ["movies.id"], name=op.f("fk_exemplars_movie_id_movies")
        ),
        sa.ForeignKeyConstraint(
            ["event_id"],
            ["preference_events.id"],
            name=op.f("fk_exemplars_event_id_preference_events"),
        ),
        sa.ForeignKeyConstraint(
            ["replaced_exemplar_id"],
            ["exemplars.id"],
            name=op.f("fk_exemplars_replaced_exemplar_id_exemplars"),
        ),
        sa.CheckConstraint("subject_kind = 'movie'", name=op.f("ck_exemplars_subject_kind")),
        sa.CheckConstraint(
            "polarity IN ('positive', 'negative')", name=op.f("ck_exemplars_polarity")
        ),
        sa.CheckConstraint("weight > 0 AND weight <= 1", name=op.f("ck_exemplars_weight")),
        sa.CheckConstraint("source IN ('seed', 'feedback')", name=op.f("ck_exemplars_source")),
        sa.CheckConstraint(
            "state IN ('active', 'superseded', 'revoked')", name=op.f("ck_exemplars_state")
        ),
        sa.CheckConstraint(
            "(source = 'feedback') = (event_id IS NOT NULL)", name=op.f("ck_exemplars_event")
        ),
        sa.CheckConstraint(
            "(source = 'feedback') = (source_file IS NOT NULL)",
            name=op.f("ck_exemplars_feedback_file"),
        ),
        sa.CheckConstraint(
            "(polarity = 'positive') = (staged_name IS NOT NULL)", name=op.f("ck_exemplars_staged")
        ),
        sa.CheckConstraint(
            "(state = 'active') = (ended_at IS NULL)", name=op.f("ck_exemplars_ended")
        ),
        sa.CheckConstraint(f"sha256 {_SHA256}", name=op.f("ck_exemplars_sha256")),
        sa.CheckConstraint(_plain_name("file_name"), name=op.f("ck_exemplars_file_name")),
        sa.CheckConstraint(_plain_name("staged_name"), name=op.f("ck_exemplars_staged_name")),
        sa.CheckConstraint(_plain_name("source_file"), name=op.f("ck_exemplars_source_file")),
    )
    op.create_index(op.f("ix_exemplars_movie_id"), "exemplars", ["movie_id"], unique=False)
    op.create_index(op.f("ix_exemplars_event_id"), "exemplars", ["event_id"], unique=False)
    # One active positive per movie, and one active row per movie, polarity, and image.
    op.create_index(
        "ix_exemplars_active_positive",
        "exemplars",
        ["movie_id"],
        unique=True,
        postgresql_where=_ACTIVE_POSITIVE,
    )
    op.create_index(
        "ix_exemplars_active_content",
        "exemplars",
        ["movie_id", "polarity", "sha256"],
        unique=True,
        postgresql_where=_ACTIVE,
    )

    op.create_table(
        "taste_profiles",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("sha256", sa.Text(), nullable=False),
        sa.Column("revision", sa.Text(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("positives", sa.Integer(), nullable=False),
        sa.Column("negatives", sa.Integer(), nullable=False),
        sa.Column("positive_movie_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("file_name", sa.Text(), nullable=False),
        sa.Column("job_id", sa.BigInteger(), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        _now("created_at"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_taste_profiles")),
        sa.ForeignKeyConstraint(
            ["job_id"], ["jobs.id"], name=op.f("fk_taste_profiles_job_id_jobs"), ondelete="SET NULL"
        ),
        sa.UniqueConstraint("sha256", name=op.f("uq_taste_profiles_sha256")),
        sa.CheckConstraint(f"sha256 {_SHA256}", name=op.f("ck_taste_profiles_sha256")),
        sa.CheckConstraint(f"revision {_SHA256}", name=op.f("ck_taste_profiles_revision")),
        sa.CheckConstraint("source IN ('seed', 'rebuild')", name=op.f("ck_taste_profiles_source")),
        sa.CheckConstraint(
            "positives >= 0 AND negatives >= 0", name=op.f("ck_taste_profiles_counts")
        ),
        sa.CheckConstraint(
            "jsonb_typeof(positive_movie_ids) = 'array'",
            name=op.f("ck_taste_profiles_positive_movie_ids"),
        ),
        sa.CheckConstraint(_plain_name("file_name"), name=op.f("ck_taste_profiles_file_name")),
    )
    # At most one active profile.
    op.create_index(
        "ix_taste_profiles_active",
        "taste_profiles",
        ["is_active"],
        unique=True,
        postgresql_where=_IS_ACTIVE,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_taste_profiles_active", table_name="taste_profiles", postgresql_where=_IS_ACTIVE
    )
    op.drop_table("taste_profiles")
    op.drop_index("ix_exemplars_active_content", table_name="exemplars", postgresql_where=_ACTIVE)
    op.drop_index(
        "ix_exemplars_active_positive", table_name="exemplars", postgresql_where=_ACTIVE_POSITIVE
    )
    op.drop_index(op.f("ix_exemplars_event_id"), table_name="exemplars")
    op.drop_index(op.f("ix_exemplars_movie_id"), table_name="exemplars")
    op.drop_table("exemplars")
    op.drop_index(op.f("ix_preference_events_run_id"), table_name="preference_events")
    op.drop_index(op.f("ix_preference_events_movie_id"), table_name="preference_events")
    op.drop_table("preference_events")
