"""Add fenced inventory and managed-sidecar provenance.

Revision ID: 0009_jmc6g
Revises: 0008_jmc6e
Create Date: 2026-07-17
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0009_jmc6g"
down_revision: str | None = "0008_jmc6e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("ck_job_artifacts_status", "job_artifacts", type_="check")
    op.create_check_constraint(
        "ck_job_artifacts_status",
        "job_artifacts",
        "status IN ('pending', 'available', 'failed', 'expiring', 'expired')",
    )
    op.drop_constraint("ck_job_logs_seal_status", "job_logs", type_="check")
    op.create_check_constraint(
        "ck_job_logs_seal_status",
        "job_logs",
        "seal_status IN ('open', 'recovering', 'sealed', 'failed', 'expiring', 'expired')",
    )
    op.add_column("subtitle_inventories", sa.Column("source_job_id", sa.String(32)))
    op.add_column("subtitle_inventories", sa.Column("source_attempt_id", sa.BigInteger()))
    op.add_column("subtitle_inventories", sa.Column("source_fence_token", sa.BigInteger()))
    op.create_index(
        "ix_subtitle_inventories_source_job_id",
        "subtitle_inventories",
        ["source_job_id"],
    )

    op.add_column("managed_subtitle_bindings", sa.Column("media_file_id", sa.Integer()))
    op.add_column("managed_subtitle_bindings", sa.Column("source_job_id", sa.String(32)))
    op.add_column("managed_subtitle_bindings", sa.Column("source_attempt_id", sa.BigInteger()))
    op.add_column("managed_subtitle_bindings", sa.Column("source_fence_token", sa.BigInteger()))
    op.alter_column("managed_subtitle_bindings", "owner_type", nullable=True)
    op.alter_column("managed_subtitle_bindings", "owner_id", nullable=True)
    op.create_foreign_key(
        "fk_managed_subtitle_bindings_media_file_id",
        "managed_subtitle_bindings",
        "media_files",
        ["media_file_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_managed_subtitle_bindings_media_file_id",
        "managed_subtitle_bindings",
        ["media_file_id"],
    )
    op.create_index(
        "ix_managed_subtitle_bindings_source_job_id",
        "managed_subtitle_bindings",
        ["source_job_id"],
    )


def downgrade() -> None:
    op.execute("UPDATE job_artifacts SET status = 'available' WHERE status = 'expiring'")
    op.execute("UPDATE job_logs SET seal_status = 'sealed' WHERE seal_status = 'expiring'")
    op.drop_constraint("ck_job_logs_seal_status", "job_logs", type_="check")
    op.create_check_constraint(
        "ck_job_logs_seal_status",
        "job_logs",
        "seal_status IN ('open', 'recovering', 'sealed', 'failed', 'expired')",
    )
    op.drop_constraint("ck_job_artifacts_status", "job_artifacts", type_="check")
    op.create_check_constraint(
        "ck_job_artifacts_status",
        "job_artifacts",
        "status IN ('pending', 'available', 'failed', 'expired')",
    )
    op.drop_index(
        "ix_managed_subtitle_bindings_source_job_id",
        table_name="managed_subtitle_bindings",
    )
    op.drop_index(
        "ix_managed_subtitle_bindings_media_file_id",
        table_name="managed_subtitle_bindings",
    )
    op.drop_constraint(
        "fk_managed_subtitle_bindings_media_file_id",
        "managed_subtitle_bindings",
        type_="foreignkey",
    )
    op.alter_column("managed_subtitle_bindings", "owner_id", nullable=False)
    op.alter_column("managed_subtitle_bindings", "owner_type", nullable=False)
    op.drop_column("managed_subtitle_bindings", "source_fence_token")
    op.drop_column("managed_subtitle_bindings", "source_attempt_id")
    op.drop_column("managed_subtitle_bindings", "source_job_id")
    op.drop_column("managed_subtitle_bindings", "media_file_id")

    op.drop_index(
        "ix_subtitle_inventories_source_job_id", table_name="subtitle_inventories"
    )
    op.drop_column("subtitle_inventories", "source_fence_token")
    op.drop_column("subtitle_inventories", "source_attempt_id")
    op.drop_column("subtitle_inventories", "source_job_id")
