"""Add fenced source metadata to Dolby Vision derived state.

Revision ID: 0005_jmc4b
Revises: 0004_jmc4a
Create Date: 2026-07-14
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0005_jmc4b"
down_revision: str | None = "0004_jmc4a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("dovi_state") as batch:
        batch.add_column(sa.Column("source_signature", sa.String(length=128), nullable=True))
        batch.add_column(sa.Column("source_fence_token", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("source_hdr_base", sa.String(length=32), nullable=True))
        batch.add_column(sa.Column("source_bit_depth", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("color_primaries", sa.String(length=32), nullable=True))
        batch.add_column(sa.Column("color_transfer", sa.String(length=32), nullable=True))
        batch.add_column(sa.Column("color_space", sa.String(length=32), nullable=True))
        batch.add_column(sa.Column("rpu_present", sa.Boolean(), nullable=True))
        batch.add_column(sa.Column("bl_present", sa.Boolean(), nullable=True))
        batch.add_column(sa.Column("analysis_depth", sa.String(length=16), nullable=True))
        batch.add_column(sa.Column("analysis_supported", sa.Boolean(), nullable=True))
        batch.add_column(sa.Column("warnings_json", sa.Text(), nullable=True))
        batch.add_column(sa.Column("validation_json", sa.Text(), nullable=True))
        batch.create_index("ix_dovi_state_source_signature", ["source_signature"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("dovi_state") as batch:
        batch.drop_index("ix_dovi_state_source_signature")
        batch.drop_column("validation_json")
        batch.drop_column("warnings_json")
        batch.drop_column("analysis_supported")
        batch.drop_column("analysis_depth")
        batch.drop_column("bl_present")
        batch.drop_column("rpu_present")
        batch.drop_column("color_space")
        batch.drop_column("color_transfer")
        batch.drop_column("color_primaries")
        batch.drop_column("source_bit_depth")
        batch.drop_column("source_hdr_base")
        batch.drop_column("source_fence_token")
        batch.drop_column("source_signature")
