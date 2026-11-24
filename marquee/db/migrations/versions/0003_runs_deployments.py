"""Runs and deployments: run records, their candidates, the write audit, and origin marquee.

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-24

The downgrade restores the library-only origin check, so it fails while any ``artwork`` row
has origin ``marquee``: undoing the schema must not quietly forget a poster Marquee wrote.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | Sequence[str] | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SHA256 = "~ '^[0-9a-f]{64}$'"
_PICK = sa.text("is_pick")


def _now(name: str) -> sa.Column:
    return sa.Column(
        name, sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
    )


def _file_name_check(column: str) -> str:
    return f"{column} <> '' AND {column} NOT IN ('.', '..') AND strpos({column}, '/') = 0"


def upgrade() -> None:
    op.create_table(
        "poster_runs",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("subject_kind", sa.Text(), server_default=sa.text("'movie'"), nullable=False),
        sa.Column("movie_id", sa.Integer(), nullable=False),
        sa.Column("job_id", sa.BigInteger(), nullable=True),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("run_folder", sa.Text(), nullable=False),
        sa.Column("engine_run_id", sa.Text(), nullable=True),
        sa.Column("engine_status", sa.Text(), nullable=True),
        sa.Column("scorer_name", sa.Text(), nullable=True),
        sa.Column("ai_model", sa.Text(), nullable=True),
        sa.Column("profile_sha256", sa.Text(), nullable=True),
        sa.Column("counts", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("stages", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("candidate_count", sa.Integer(), nullable=True),
        sa.Column("ranked_count", sa.Integer(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        _now("created_at"),
        _now("updated_at"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_poster_runs")),
        sa.ForeignKeyConstraint(
            ["movie_id"], ["movies.id"], name=op.f("fk_poster_runs_movie_id_movies")
        ),
        sa.ForeignKeyConstraint(
            ["job_id"], ["jobs.id"], name=op.f("fk_poster_runs_job_id_jobs"), ondelete="SET NULL"
        ),
        sa.UniqueConstraint("job_id", name=op.f("uq_poster_runs_job_id")),
        sa.CheckConstraint("subject_kind = 'movie'", name=op.f("ck_poster_runs_subject_kind")),
        sa.CheckConstraint(
            "state IN ('running', 'completed', 'failed', 'cancelled')",
            name=op.f("ck_poster_runs_state"),
        ),
        sa.CheckConstraint(_file_name_check("run_folder"), name=op.f("ck_poster_runs_run_folder")),
        sa.CheckConstraint(f"profile_sha256 {_SHA256}", name=op.f("ck_poster_runs_profile_sha256")),
    )
    op.create_index(op.f("ix_poster_runs_movie_id"), "poster_runs", ["movie_id"], unique=False)

    op.create_table(
        "run_candidates",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("run_id", sa.BigInteger(), nullable=False),
        sa.Column("source_file", sa.Text(), nullable=False),
        sa.Column("sha256", sa.Text(), nullable=True),
        sa.Column("stage_reached", sa.Text(), nullable=True),
        sa.Column("gate_decision", sa.Text(), nullable=True),
        sa.Column("rejection_reason", sa.Text(), nullable=True),
        sa.Column("rank", sa.Integer(), nullable=True),
        sa.Column("final_score", sa.Double(), nullable=True),
        sa.Column("stack_rank", sa.Integer(), nullable=True),
        sa.Column("stack_label", sa.Text(), nullable=True),
        sa.Column("is_pick", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        _now("created_at"),
        _now("updated_at"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_run_candidates")),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["poster_runs.id"],
            name=op.f("fk_run_candidates_run_id_poster_runs"),
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "run_id", "source_file", name=op.f("uq_run_candidates_run_id_source_file")
        ),
        sa.CheckConstraint(f"sha256 {_SHA256}", name=op.f("ck_run_candidates_sha256")),
        sa.CheckConstraint(
            _file_name_check("source_file"), name=op.f("ck_run_candidates_source_file")
        ),
    )
    op.create_index(op.f("ix_run_candidates_run_id"), "run_candidates", ["run_id"], unique=False)
    # One pick per run.
    op.create_index(
        "ix_run_candidates_pick", "run_candidates", ["run_id"], unique=True, postgresql_where=_PICK
    )

    op.create_table(
        "deployments",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("subject_kind", sa.Text(), server_default=sa.text("'movie'"), nullable=False),
        sa.Column("movie_id", sa.Integer(), nullable=False),
        sa.Column("artwork_kind", sa.Text(), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("initiated_by", sa.Text(), nullable=False),
        sa.Column("run_id", sa.BigInteger(), nullable=True),
        sa.Column("candidate_id", sa.BigInteger(), nullable=True),
        sa.Column("source_file", sa.Text(), nullable=True),
        sa.Column("job_id", sa.BigInteger(), nullable=True),
        sa.Column("file_name", sa.Text(), nullable=False),
        sa.Column("new_sha256", sa.Text(), nullable=True),
        sa.Column("new_size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("previous_sha256", sa.Text(), nullable=True),
        sa.Column("previous_origin", sa.Text(), nullable=True),
        sa.Column("backup_file", sa.Text(), nullable=True),
        sa.Column("restores_deployment_id", sa.BigInteger(), nullable=True),
        _now("created_at"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_deployments")),
        sa.ForeignKeyConstraint(
            ["movie_id"], ["movies.id"], name=op.f("fk_deployments_movie_id_movies")
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["poster_runs.id"],
            name=op.f("fk_deployments_run_id_poster_runs"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["candidate_id"],
            ["run_candidates.id"],
            name=op.f("fk_deployments_candidate_id_run_candidates"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["job_id"], ["jobs.id"], name=op.f("fk_deployments_job_id_jobs"), ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["restores_deployment_id"],
            ["deployments.id"],
            name=op.f("fk_deployments_restores_deployment_id_deployments"),
        ),
        sa.UniqueConstraint("job_id", name=op.f("uq_deployments_job_id")),
        sa.CheckConstraint("subject_kind = 'movie'", name=op.f("ck_deployments_subject_kind")),
        sa.CheckConstraint("artwork_kind = 'poster'", name=op.f("ck_deployments_artwork_kind")),
        sa.CheckConstraint("action IN ('deploy', 'restore')", name=op.f("ck_deployments_action")),
        sa.CheckConstraint(
            "initiated_by IN ('policy', 'owner')", name=op.f("ck_deployments_initiated_by")
        ),
        sa.CheckConstraint(
            "previous_origin IN ('library', 'marquee')",
            name=op.f("ck_deployments_previous_origin"),
        ),
        sa.CheckConstraint(f"new_sha256 {_SHA256}", name=op.f("ck_deployments_new_sha256")),
        sa.CheckConstraint(
            f"previous_sha256 {_SHA256}", name=op.f("ck_deployments_previous_sha256")
        ),
        sa.CheckConstraint(
            "(new_sha256 IS NULL) = (new_size_bytes IS NULL)", name=op.f("ck_deployments_new_file")
        ),
        sa.CheckConstraint(_file_name_check("file_name"), name=op.f("ck_deployments_file_name")),
        sa.CheckConstraint(
            "backup_file <> '' AND left(backup_file, 1) <> '/' "
            "AND backup_file !~ '(^|/)\\.\\.(/|$)'",
            name=op.f("ck_deployments_backup_file"),
        ),
        sa.CheckConstraint(
            "(action = 'restore') = (restores_deployment_id IS NOT NULL)",
            name=op.f("ck_deployments_restores"),
        ),
    )
    op.create_index(op.f("ix_deployments_movie_id"), "deployments", ["movie_id"], unique=False)

    op.drop_constraint(op.f("ck_artwork_origin"), "artwork", type_="check")
    op.create_check_constraint(
        op.f("ck_artwork_origin"), "artwork", "origin IN ('library', 'marquee')"
    )


def downgrade() -> None:
    op.drop_constraint(op.f("ck_artwork_origin"), "artwork", type_="check")
    op.create_check_constraint(op.f("ck_artwork_origin"), "artwork", "origin = 'library'")
    op.drop_index(op.f("ix_deployments_movie_id"), table_name="deployments")
    op.drop_table("deployments")
    op.drop_index("ix_run_candidates_pick", table_name="run_candidates", postgresql_where=_PICK)
    op.drop_index(op.f("ix_run_candidates_run_id"), table_name="run_candidates")
    op.drop_table("run_candidates")
    op.drop_index(op.f("ix_poster_runs_movie_id"), table_name="poster_runs")
    op.drop_table("poster_runs")
