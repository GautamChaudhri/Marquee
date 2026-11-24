"""Masked runs, blind evaluations, and residual trainings.

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-24

``poster_runs`` gains ``masked`` (a blind evaluation's run, never part of the product loop;
every existing run is a normal one) and ``residual_id`` (the training whose artifact ranked
it). ``residual_trainings`` records every training of the residual, at most one row active.
``evaluations`` and ``evaluation_results`` hold blind evaluations and, per movie, where the
owner's poster landed. The downgrade drops the three tables and the two columns.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006"
down_revision: str | Sequence[str] | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "residual_trainings",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("job_id", sa.BigInteger(), nullable=True),
        sa.Column("profile_id", sa.BigInteger(), nullable=True),
        sa.Column("profile_sha256", sa.Text(), nullable=False),
        sa.Column("outcome", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("subjects", sa.Integer(), nullable=False),
        sa.Column("pairs", sa.Integer(), nullable=False),
        sa.Column("events", sa.Integer(), nullable=False),
        sa.Column("evidence_digest", sa.Text(), nullable=False),
        sa.Column("report", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("artifact_sha256", sa.Text(), nullable=True),
        sa.Column("file_name", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("deactivated_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.CheckConstraint(
            "(artifact_sha256 IS NOT NULL) = (outcome = 'activate')",
            name=op.f("ck_residual_trainings_artifact"),
        ),
        sa.CheckConstraint(
            "artifact_sha256 ~ '^[0-9a-f]{64}$'", name=op.f("ck_residual_trainings_artifact_sha256")
        ),
        sa.CheckConstraint(
            "evidence_digest ~ '^[0-9a-f]{64}$'", name=op.f("ck_residual_trainings_evidence_digest")
        ),
        sa.CheckConstraint(
            "file_name IS NULL OR file_name = artifact_sha256 || '.npz'",
            name=op.f("ck_residual_trainings_file_name"),
        ),
        sa.CheckConstraint(
            "outcome IN ('activate', 'no_change', 'failed')",
            name=op.f("ck_residual_trainings_outcome"),
        ),
        sa.CheckConstraint(
            "profile_sha256 ~ '^[0-9a-f]{64}$'", name=op.f("ck_residual_trainings_profile_sha256")
        ),
        sa.CheckConstraint(
            "(artifact_sha256 IS NULL) = (file_name IS NULL)",
            name=op.f("ck_residual_trainings_artifact_file"),
        ),
        sa.CheckConstraint(
            "NOT is_active OR artifact_sha256 IS NOT NULL",
            name=op.f("ck_residual_trainings_active_artifact"),
        ),
        sa.CheckConstraint(
            "subjects >= 0 AND pairs >= 0 AND events >= 0",
            name=op.f("ck_residual_trainings_counts"),
        ),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["jobs.id"],
            name=op.f("fk_residual_trainings_job_id_jobs"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["profile_id"],
            ["taste_profiles.id"],
            name=op.f("fk_residual_trainings_profile_id_taste_profiles"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_residual_trainings")),
        sa.UniqueConstraint("artifact_sha256", name=op.f("uq_residual_trainings_artifact_sha256")),
    )
    op.create_index(
        "ix_residual_trainings_active",
        "residual_trainings",
        ["is_active"],
        unique=True,
        postgresql_where=sa.text("is_active"),
    )
    op.create_index(
        op.f("ix_residual_trainings_profile_id"), "residual_trainings", ["profile_id"], unique=False
    )
    op.create_table(
        "evaluations",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("job_id", sa.BigInteger(), nullable=True),
        sa.Column("profile_id", sa.BigInteger(), nullable=True),
        sa.Column("profile_sha256", sa.Text(), nullable=False),
        sa.Column("residual_id", sa.BigInteger(), nullable=True),
        sa.Column("scorer", sa.Text(), nullable=False),
        sa.Column("movies", sa.Integer(), nullable=False),
        sa.Column("queued", sa.Integer(), nullable=False),
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
        sa.CheckConstraint(
            "profile_sha256 ~ '^[0-9a-f]{64}$'", name=op.f("ck_evaluations_profile_sha256")
        ),
        sa.CheckConstraint(
            "scorer IN ('weighted', 'residual')", name=op.f("ck_evaluations_scorer")
        ),
        sa.CheckConstraint(
            "movies >= 0 AND queued >= 0 AND queued <= movies", name=op.f("ck_evaluations_counts")
        ),
        sa.ForeignKeyConstraint(
            ["job_id"], ["jobs.id"], name=op.f("fk_evaluations_job_id_jobs"), ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["profile_id"],
            ["taste_profiles.id"],
            name=op.f("fk_evaluations_profile_id_taste_profiles"),
        ),
        sa.ForeignKeyConstraint(
            ["residual_id"],
            ["residual_trainings.id"],
            name=op.f("fk_evaluations_residual_id_residual_trainings"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_evaluations")),
        sa.UniqueConstraint("job_id", name=op.f("uq_evaluations_job_id")),
    )
    op.create_table(
        "evaluation_results",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("evaluation_id", sa.BigInteger(), nullable=False),
        sa.Column("subject_kind", sa.Text(), server_default=sa.text("'movie'"), nullable=False),
        sa.Column("movie_id", sa.Integer(), nullable=False),
        sa.Column("exemplar_id", sa.BigInteger(), nullable=True),
        sa.Column("exemplar_source", sa.Text(), nullable=True),
        sa.Column("run_id", sa.BigInteger(), nullable=True),
        sa.Column("outcome", sa.Text(), nullable=False),
        sa.Column("matched_by", sa.Text(), nullable=True),
        sa.Column("match_file", sa.Text(), nullable=True),
        sa.Column("match_sha256", sa.Text(), nullable=True),
        sa.Column("match_cosine", sa.Double(), nullable=True),
        sa.Column("best_cosine", sa.Double(), nullable=True),
        sa.Column("best_file", sa.Text(), nullable=True),
        sa.Column("unembedded", sa.Integer(), nullable=True),
        sa.Column("rank", sa.Integer(), nullable=True),
        sa.Column("ranked_count", sa.Integer(), nullable=True),
        sa.Column("card", sa.Integer(), nullable=True),
        sa.Column("cards", sa.Integer(), nullable=True),
        sa.Column("pick_file", sa.Text(), nullable=True),
        sa.Column("pick_is_match", sa.Boolean(), nullable=True),
        sa.Column("same_design", sa.Boolean(), nullable=True),
        sa.Column("stage", sa.Text(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
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
        sa.CheckConstraint(
            "(match_file IS NOT NULL) = (outcome IN ('ranked', 'gated'))",
            name=op.f("ck_evaluation_results_match"),
        ),
        sa.CheckConstraint(
            "exemplar_source IN ('seed', 'feedback')",
            name=op.f("ck_evaluation_results_exemplar_source"),
        ),
        sa.CheckConstraint(
            "match_sha256 ~ '^[0-9a-f]{64}$'", name=op.f("ck_evaluation_results_match_sha256")
        ),
        sa.CheckConstraint(
            "outcome <> 'ranked' OR (rank IS NOT NULL AND ranked_count IS NOT NULL AND card IS NOT NULL AND cards IS NOT NULL AND rank >= 1 AND ranked_count >= rank AND card >= 1 AND cards >= card AND pick_is_match IS NOT NULL AND same_design IS NOT NULL)",
            name=op.f("ck_evaluation_results_ranked"),
        ),
        sa.CheckConstraint(
            "outcome IN ('ranked', 'gated', 'not_offered', 'failed')",
            name=op.f("ck_evaluation_results_outcome"),
        ),
        sa.CheckConstraint(
            "subject_kind = 'movie'", name=op.f("ck_evaluation_results_subject_kind")
        ),
        sa.CheckConstraint(
            "match_cosine IS NULL OR (match_cosine >= -1.0001 AND match_cosine <= 1.0001)",
            name=op.f("ck_evaluation_results_match_cosine"),
        ),
        sa.ForeignKeyConstraint(
            ["evaluation_id"],
            ["evaluations.id"],
            name=op.f("fk_evaluation_results_evaluation_id_evaluations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["exemplar_id"],
            ["exemplars.id"],
            name=op.f("fk_evaluation_results_exemplar_id_exemplars"),
        ),
        sa.ForeignKeyConstraint(
            ["movie_id"], ["movies.id"], name=op.f("fk_evaluation_results_movie_id_movies")
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["poster_runs.id"],
            name=op.f("fk_evaluation_results_run_id_poster_runs"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_evaluation_results")),
        sa.UniqueConstraint(
            "evaluation_id", "movie_id", name=op.f("uq_evaluation_results_evaluation_id_movie_id")
        ),
    )
    op.create_index(
        op.f("ix_evaluation_results_evaluation_id"),
        "evaluation_results",
        ["evaluation_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_evaluation_results_run_id"), "evaluation_results", ["run_id"], unique=False
    )
    op.add_column(
        "poster_runs",
        sa.Column("masked", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )
    op.add_column("poster_runs", sa.Column("residual_id", sa.BigInteger(), nullable=True))
    op.create_foreign_key(
        op.f("fk_poster_runs_residual_id_residual_trainings"),
        "poster_runs",
        "residual_trainings",
        ["residual_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("fk_poster_runs_residual_id_residual_trainings"), "poster_runs", type_="foreignkey"
    )
    op.drop_column("poster_runs", "residual_id")
    op.drop_column("poster_runs", "masked")
    op.drop_index(op.f("ix_evaluation_results_run_id"), table_name="evaluation_results")
    op.drop_index(op.f("ix_evaluation_results_evaluation_id"), table_name="evaluation_results")
    op.drop_table("evaluation_results")
    op.drop_table("evaluations")
    op.drop_index(op.f("ix_residual_trainings_profile_id"), table_name="residual_trainings")
    op.drop_index(
        "ix_residual_trainings_active",
        table_name="residual_trainings",
        postgresql_where=sa.text("is_active"),
    )
    op.drop_table("residual_trainings")
