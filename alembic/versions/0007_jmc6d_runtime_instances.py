"""Add durable runtime-incarnation safety evidence.

Revision ID: 0007_jmc6d
Revises: 0006_jmc4c
Create Date: 2026-07-17
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0007_jmc6d"
down_revision: str | None = "0006_jmc4c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "runtime_instances",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("node_label", sa.String(length=100), nullable=False),
        sa.Column("build", sa.String(length=64), nullable=False),
        sa.Column("host_boot_id", sa.String(length=64), nullable=False),
        sa.Column("process_id", sa.Integer(), nullable=False),
        sa.Column("process_start_ticks", sa.BigInteger(), nullable=False),
        sa.Column("process_group_id", sa.Integer(), nullable=False),
        sa.Column("cgroup_path", sa.String(length=200), nullable=True),
        sa.Column("advertised_entrypoints", sa.JSON(), nullable=False),
        sa.Column("capabilities", sa.JSON(), nullable=False),
        sa.Column("readiness", sa.String(length=16), server_default="starting", nullable=False),
        sa.Column("heartbeat_failures", sa.Integer(), server_default="0", nullable=False),
        sa.Column("last_telemetry_error_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("stopped_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "last_heartbeat_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("heartbeat_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("role IN ('worker', 'scheduler')", name="ck_runtime_instances_role"),
        sa.CheckConstraint(
            "readiness IN ('starting', 'ready', 'not_ready', 'stopped')",
            name="ck_runtime_instances_readiness",
        ),
        sa.CheckConstraint(
            "heartbeat_failures >= 0", name="ck_runtime_instances_heartbeat_failures"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "host_boot_id",
            "process_id",
            "process_start_ticks",
            name="uq_runtime_instances_process_identity",
        ),
    )
    op.create_index(
        "ix_runtime_instances_role", "runtime_instances", ["role"], unique=False
    )
    op.create_index(
        "ix_runtime_instances_node_label", "runtime_instances", ["node_label"], unique=False
    )
    op.create_index(
        "ix_runtime_instances_last_heartbeat_at",
        "runtime_instances",
        ["last_heartbeat_at"],
        unique=False,
    )
    op.create_index(
        "ix_runtime_instances_heartbeat_expires_at",
        "runtime_instances",
        ["heartbeat_expires_at"],
        unique=False,
    )
    with op.batch_alter_table("job_attempts") as batch:
        batch.add_column(sa.Column("runtime_instance_id", sa.String(length=36), nullable=True))
        batch.create_index(
            "ix_job_attempts_runtime_instance_id", ["runtime_instance_id"], unique=False
        )
        batch.create_foreign_key(
            "fk_job_attempts_runtime_instance_id_runtime_instances",
            "runtime_instances",
            ["runtime_instance_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    with op.batch_alter_table("job_attempts") as batch:
        batch.drop_constraint(
            "fk_job_attempts_runtime_instance_id_runtime_instances", type_="foreignkey"
        )
        batch.drop_index("ix_job_attempts_runtime_instance_id")
        batch.drop_column("runtime_instance_id")
    op.drop_index("ix_runtime_instances_heartbeat_expires_at", table_name="runtime_instances")
    op.drop_index("ix_runtime_instances_last_heartbeat_at", table_name="runtime_instances")
    op.drop_index("ix_runtime_instances_node_label", table_name="runtime_instances")
    op.drop_index("ix_runtime_instances_role", table_name="runtime_instances")
    op.drop_table("runtime_instances")
