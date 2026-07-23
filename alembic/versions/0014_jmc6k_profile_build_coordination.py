"""add serialized per-library taste profile build lineage

Revision ID: 0014_jmc6k
Revises: 0013_jmc6j
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0014_jmc6k"
down_revision: str | None = "0013_jmc6j"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "taste_profile_builds",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("revision_digest", sa.String(64), nullable=False),
        sa.Column("library", sa.String(8), nullable=False),
        sa.Column("expected_generation", sa.BigInteger(), nullable=False),
        sa.Column("job_id", sa.String(32), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("result_generation", sa.BigInteger(), nullable=True),
        sa.Column("result_checksum", sa.String(64), nullable=True),
        sa.Column("consumer_reload_checksum", sa.String(64), nullable=True),
        sa.Column("failure", sa.JSON(), nullable=True),
        sa.Column("supersedes_build_id", sa.String(32), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("library IN ('movies', 'tv')", name="ck_taste_profile_builds_library"),
        sa.CheckConstraint(
            "state IN ('queued', 'running', 'succeeded', 'no_change', 'superseded', 'failed', 'cancelled')",
            name="ck_taste_profile_builds_state",
        ),
        sa.CheckConstraint(
            "expected_generation >= 0", name="ck_taste_profile_builds_expected_generation"
        ),
        sa.CheckConstraint(
            "result_generation IS NULL OR result_generation >= 0",
            name="ck_taste_profile_builds_result_generation",
        ),
        sa.CheckConstraint(
            "result_checksum IS NULL OR length(result_checksum) = 64",
            name="ck_taste_profile_builds_result_checksum",
        ),
        sa.CheckConstraint(
            "consumer_reload_checksum IS NULL OR length(consumer_reload_checksum) = 64",
            name="ck_taste_profile_builds_reload_checksum",
        ),
        sa.ForeignKeyConstraint(
            ["revision_digest"], ["taste_profile_revisions.digest"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["supersedes_build_id"], ["taste_profile_builds.id"], ondelete="RESTRICT"
        ),
        sa.UniqueConstraint("job_id", name="uq_taste_profile_builds_job"),
    )
    op.create_index(
        "ix_taste_profile_builds_library_created",
        "taste_profile_builds",
        ["library", "created_at"],
    )
    op.create_index("ix_taste_profile_builds_revision", "taste_profile_builds", ["revision_digest"])
    op.create_index(
        "uq_taste_profile_builds_inflight_library",
        "taste_profile_builds",
        ["library"],
        unique=True,
        postgresql_where=sa.text("state IN ('queued', 'running')"),
    )

    op.create_table(
        "taste_profile_coordinators",
        sa.Column("library", sa.String(8), primary_key=True),
        sa.Column("desired_revision_digest", sa.String(64), nullable=True),
        sa.Column("desired_generation", sa.BigInteger(), nullable=True),
        sa.Column("inflight_build_id", sa.String(32), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "library IN ('movies', 'tv')", name="ck_taste_profile_coordinators_library"
        ),
        sa.CheckConstraint(
            "desired_generation IS NULL OR desired_generation >= 0",
            name="ck_taste_profile_coordinators_desired_generation",
        ),
        sa.ForeignKeyConstraint(
            ["desired_revision_digest"], ["taste_profile_revisions.digest"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["inflight_build_id"], ["taste_profile_builds.id"], ondelete="RESTRICT"
        ),
    )


def downgrade() -> None:
    op.drop_table("taste_profile_coordinators")
    op.drop_index("uq_taste_profile_builds_inflight_library", table_name="taste_profile_builds")
    op.drop_index("ix_taste_profile_builds_revision", table_name="taste_profile_builds")
    op.drop_index("ix_taste_profile_builds_library_created", table_name="taste_profile_builds")
    op.drop_table("taste_profile_builds")
