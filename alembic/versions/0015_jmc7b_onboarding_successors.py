"""add recoverable onboarding analysis and deployment successor lineage

Revision ID: 0015_jmc7b
Revises: 0014_jmc6k
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0015_jmc7b"
down_revision: str | None = "0014_jmc6k"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "taste_profile_revisions",
        sa.Column("exemplar_manifest", sa.JSON(), nullable=False, server_default=sa.text("'[]'::json")),
    )
    bind = op.get_bind()
    revisions = sa.table(
        "taste_profile_revisions",
        sa.column("digest", sa.String()),
        sa.column("exemplar_ids", sa.JSON()),
        sa.column("exemplar_manifest", sa.JSON()),
    )
    exemplars = sa.table(
        "taste_exemplars",
        sa.column("id", sa.String()),
        sa.column("namespace", sa.String()),
        sa.column("polarity", sa.String()),
        sa.column("evidence_weight", sa.Float()),
        sa.column("retained_artifact_id", sa.Integer()),
        sa.column("checksum", sa.String()),
        sa.column("embedding_identity", sa.JSON()),
        sa.column("supersedes_exemplar_id", sa.String()),
    )
    for revision_row in bind.execute(
        sa.select(revisions.c.digest, revisions.c.exemplar_ids)
    ).mappings():
        exemplar_ids = revision_row["exemplar_ids"]
        if not isinstance(exemplar_ids, list):
            raise RuntimeError("taste profile revision exemplar_ids must be a JSON list")
        source_rows = bind.execute(
            sa.select(
                exemplars.c.id,
                exemplars.c.namespace,
                exemplars.c.polarity,
                exemplars.c.evidence_weight,
                exemplars.c.retained_artifact_id,
                exemplars.c.checksum,
                exemplars.c.embedding_identity,
                exemplars.c.supersedes_exemplar_id,
            ).where(exemplars.c.id.in_(exemplar_ids))
        ).mappings()
        by_id = {row["id"]: row for row in source_rows}
        if len(by_id) != len(exemplar_ids):
            raise RuntimeError("taste profile revision references a missing exemplar")
        manifest = [
            {
                "exemplar_id": exemplar_id,
                "namespace": by_id[exemplar_id]["namespace"],
                "polarity": by_id[exemplar_id]["polarity"],
                "weight": float(by_id[exemplar_id]["evidence_weight"]),
                "retained_artifact_id": by_id[exemplar_id]["retained_artifact_id"],
                "checksum": by_id[exemplar_id]["checksum"] or "",
                "embedding_identity": by_id[exemplar_id]["embedding_identity"],
                "supersedes_exemplar_id": by_id[exemplar_id]["supersedes_exemplar_id"],
            }
            for exemplar_id in exemplar_ids
        ]
        bind.execute(
            sa.update(revisions)
            .where(revisions.c.digest == revision_row["digest"])
            .values(exemplar_manifest=manifest)
        )
    op.alter_column("taste_profile_revisions", "exemplar_manifest", server_default=None)

    op.create_table(
        "ml_consumer_acknowledgements",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("family", sa.String(64), nullable=False),
        sa.Column("consumer_role", sa.String(64), nullable=False),
        sa.Column("instance_id", sa.String(128), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("checksum", sa.String(64), nullable=False),
        sa.Column("artifact_id", sa.BigInteger(), nullable=False),
        sa.Column("load_result", sa.JSON(), nullable=False),
        sa.Column("loaded_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("generation >= 1", name="ck_ml_consumer_ack_generation"),
        sa.CheckConstraint("length(checksum) = 64", name="ck_ml_consumer_ack_checksum"),
        sa.ForeignKeyConstraint(["artifact_id"], ["job_artifacts.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint(
            "family", "consumer_role", "instance_id", "generation", "checksum",
            name="uq_ml_consumer_ack_identity",
        ),
    )
    op.create_index(
        "ix_ml_consumer_ack_family_generation",
        "ml_consumer_acknowledgements",
        ["family", "generation"],
    )

    op.create_table(
        "onboarding_analysis_successors",
        sa.Column("job_id", sa.String(32), primary_key=True),
        sa.Column("subject_kind", sa.String(24), nullable=False),
        sa.Column("subject_reference", sa.String(96), nullable=False),
        sa.Column("predecessor_job_id", sa.String(32), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "subject_kind IN ('movie', 'series', 'season')",
            name="ck_onboarding_analysis_successors_subject_kind",
        ),
        sa.CheckConstraint(
            "predecessor_job_id IS NULL OR predecessor_job_id <> job_id",
            name="ck_onboarding_analysis_successors_not_self",
        ),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["predecessor_job_id"], ["jobs.id"], ondelete="RESTRICT"
        ),
    )
    op.create_index(
        "ix_onboarding_analysis_successors_subject_created",
        "onboarding_analysis_successors",
        ["subject_kind", "subject_reference", "created_at"],
    )

    op.create_table(
        "taste_deployment_successors",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("exemplar_id", sa.String(32), nullable=False),
        sa.Column("job_id", sa.String(32), nullable=False),
        sa.Column("predecessor_job_id", sa.String(32), nullable=True),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("post_effect_validation", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("ordinal >= 0", name="ck_taste_deployment_successors_ordinal"),
        sa.CheckConstraint(
            "state IN ('queued', 'running', 'succeeded', 'no_change', 'failed', "
            "'cancelled', 'superseded', 'unsafe', 'dead_letter')",
            name="ck_taste_deployment_successors_state",
        ),
        sa.CheckConstraint(
            "predecessor_job_id IS NULL OR predecessor_job_id <> job_id",
            name="ck_taste_deployment_successors_not_self",
        ),
        sa.ForeignKeyConstraint(
            ["exemplar_id"], ["taste_exemplars.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["predecessor_job_id"], ["jobs.id"], ondelete="RESTRICT"
        ),
        sa.UniqueConstraint("job_id", name="uq_taste_deployment_successors_job"),
        sa.UniqueConstraint(
            "exemplar_id", "ordinal", name="uq_taste_deployment_successors_ordinal"
        ),
    )
    op.create_index(
        "ix_taste_deployment_successors_exemplar",
        "taste_deployment_successors",
        ["exemplar_id", "ordinal"],
    )


def downgrade() -> None:
    op.drop_index("ix_taste_deployment_successors_exemplar", table_name="taste_deployment_successors")
    op.drop_table("taste_deployment_successors")
    op.drop_index(
        "ix_onboarding_analysis_successors_subject_created",
        table_name="onboarding_analysis_successors",
    )
    op.drop_table("onboarding_analysis_successors")
    op.drop_index("ix_ml_consumer_ack_family_generation", table_name="ml_consumer_acknowledgements")
    op.drop_table("ml_consumer_acknowledgements")
    op.drop_column("taste_profile_revisions", "exemplar_manifest")
