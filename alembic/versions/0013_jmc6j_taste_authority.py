"""add canonical taste exemplar and preference authorities

Revision ID: 0013_jmc6j
Revises: 0012_jmc6h
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0013_jmc6j"
down_revision: str | None = "0012_jmc6h"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "poster_preference_events",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("idempotency_key", sa.String(160), nullable=False, unique=True),
        sa.Column("namespace", sa.String(12), nullable=False),
        sa.Column("subject_kind", sa.String(24), nullable=False),
        sa.Column("subject_reference", sa.String(96), nullable=False),
        sa.Column("subject_snapshot", sa.JSON(), nullable=False),
        sa.Column("pipeline_run_id", sa.String(32), nullable=True),
        sa.Column("candidate_artifact_id", sa.BigInteger(), nullable=True),
        sa.Column("action", sa.String(16), nullable=False),
        sa.Column("exposed_candidates", sa.JSON(), nullable=False),
        sa.Column("presentation_order", sa.JSON(), nullable=False),
        sa.Column("training_context", sa.JSON(), nullable=False),
        sa.Column("confidence", sa.String(12), nullable=False),
        sa.Column("initiator", sa.JSON(), nullable=False),
        sa.Column("supersedes_event_id", sa.String(32), nullable=True),
        sa.Column("revoked_event_id", sa.String(32), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("version >= 1", name="ck_preference_events_version"),
        sa.CheckConstraint("namespace IN ('global', 'movies', 'tv')", name="ck_preference_events_namespace"),
        sa.CheckConstraint("action IN ('selection', 'approval', 'override', 'rank', 'hate', 'undo', 'revoke')", name="ck_preference_events_action"),
        sa.CheckConstraint("confidence IN ('explicit', 'strong', 'medium', 'weak')", name="ck_preference_events_confidence"),
        sa.ForeignKeyConstraint(["pipeline_run_id"], ["pipeline_runs.run_id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["candidate_artifact_id"], ["job_artifacts.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["supersedes_event_id"], ["poster_preference_events.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["revoked_event_id"], ["poster_preference_events.id"], ondelete="RESTRICT"),
    )
    op.create_index("ix_preference_events_namespace_created", "poster_preference_events", ["namespace", "created_at"])
    op.create_index("ix_preference_events_subject", "poster_preference_events", ["subject_kind", "subject_reference"])

    op.create_table(
        "taste_exemplars",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("namespace", sa.String(12), nullable=False),
        sa.Column("polarity", sa.String(8), nullable=False),
        sa.Column("evidence_weight", sa.Float(), nullable=False),
        sa.Column("evidence_source", sa.String(32), nullable=False),
        sa.Column("subject_kind", sa.String(24), nullable=False),
        sa.Column("subject_reference", sa.String(96), nullable=False),
        sa.Column("subject_snapshot", sa.JSON(), nullable=False),
        sa.Column("pipeline_run_id", sa.String(32), nullable=True),
        sa.Column("candidate_artifact_id", sa.BigInteger(), nullable=True),
        sa.Column("retained_artifact_id", sa.BigInteger(), nullable=True),
        sa.Column("preference_event_id", sa.String(32), nullable=False, unique=True),
        sa.Column("deployment_job_id", sa.String(32), nullable=True),
        sa.Column("deployment_result", sa.JSON(), nullable=True),
        sa.Column("initiator", sa.JSON(), nullable=False),
        sa.Column("asset_key", sa.String(300), nullable=True),
        sa.Column("checksum", sa.String(64), nullable=True),
        sa.Column("perceptual_hash", sa.String(64), nullable=True),
        sa.Column("content_type", sa.String(100), nullable=True),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column("embedding_identity", sa.JSON(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("revision_digest", sa.String(64), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("supersedes_exemplar_id", sa.String(32), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("version >= 1", name="ck_taste_exemplars_version"),
        sa.CheckConstraint("namespace IN ('global', 'movies', 'tv')", name="ck_taste_exemplars_namespace"),
        sa.CheckConstraint("polarity IN ('positive', 'negative')", name="ck_taste_exemplars_polarity"),
        sa.CheckConstraint("status IN ('pending_deploy', 'active', 'revoked', 'invalid')", name="ck_taste_exemplars_status"),
        sa.CheckConstraint("evidence_weight > 0 AND evidence_weight <= 1", name="ck_taste_exemplars_weight"),
        sa.CheckConstraint("checksum IS NULL OR length(checksum) = 64", name="ck_taste_exemplars_checksum"),
        sa.CheckConstraint("status <> 'active' OR retained_artifact_id IS NOT NULL", name="ck_taste_exemplars_active_asset"),
        sa.ForeignKeyConstraint(["pipeline_run_id"], ["pipeline_runs.run_id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["candidate_artifact_id"], ["job_artifacts.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["retained_artifact_id"], ["job_artifacts.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["preference_event_id"], ["poster_preference_events.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["deployment_job_id"], ["jobs.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["supersedes_exemplar_id"], ["taste_exemplars.id"], ondelete="RESTRICT"),
    )
    op.create_index("ix_taste_exemplars_namespace_status", "taste_exemplars", ["namespace", "status"])
    op.create_index("ix_taste_exemplars_subject", "taste_exemplars", ["subject_kind", "subject_reference"])
    op.create_index("uq_taste_exemplars_active_positive_subject", "taste_exemplars", ["namespace", "subject_kind", "subject_reference"], unique=True, postgresql_where=sa.text("status = 'active' AND polarity = 'positive'"))
    op.create_index("uq_taste_exemplars_active_positive_content", "taste_exemplars", ["checksum"], unique=True, postgresql_where=sa.text("status = 'active' AND polarity = 'positive' AND checksum IS NOT NULL"))

    op.create_table(
        "taste_profile_revisions",
        sa.Column("digest", sa.String(64), primary_key=True),
        sa.Column("exemplar_ids", sa.JSON(), nullable=False),
        sa.Column("exemplar_checksums", sa.JSON(), nullable=False),
        sa.Column("positive_subjects", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("build_job_id", sa.String(32), nullable=True),
        sa.Column("movie_generation", sa.BigInteger(), nullable=True),
        sa.Column("tv_generation", sa.BigInteger(), nullable=True),
        sa.Column("movie_checksum", sa.String(64), nullable=True),
        sa.Column("tv_checksum", sa.String(64), nullable=True),
        sa.Column("consumer_reload", sa.JSON(), nullable=False),
        sa.Column("failure", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("state IN ('eligible', 'building', 'published', 'personalized', 'failed', 'superseded')", name="ck_taste_profile_revisions_state"),
        sa.CheckConstraint("positive_subjects >= 0", name="ck_taste_profile_revisions_subjects"),
        sa.ForeignKeyConstraint(["build_job_id"], ["jobs.id"], ondelete="RESTRICT"),
    )


def downgrade() -> None:
    op.drop_table("taste_profile_revisions")
    op.drop_index("uq_taste_exemplars_active_positive_content", table_name="taste_exemplars")
    op.drop_index("uq_taste_exemplars_active_positive_subject", table_name="taste_exemplars")
    op.drop_index("ix_taste_exemplars_subject", table_name="taste_exemplars")
    op.drop_index("ix_taste_exemplars_namespace_status", table_name="taste_exemplars")
    op.drop_table("taste_exemplars")
    op.drop_index("ix_preference_events_subject", table_name="poster_preference_events")
    op.drop_index("ix_preference_events_namespace_created", table_name="poster_preference_events")
    op.drop_table("poster_preference_events")
