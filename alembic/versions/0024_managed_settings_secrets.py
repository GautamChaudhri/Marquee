"""Add encrypted integration credential storage.

Revision ID: 0024_managed_settings_secrets
Revises: 0023_series_genres
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0024_managed_settings_secrets"
down_revision: str | None = "0023_series_genres"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "managed_secrets",
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("ciphertext", sa.LargeBinary(), nullable=False),
        sa.Column("nonce", sa.LargeBinary(length=12), nullable=False),
        sa.Column("key_id", sa.String(length=80), nullable=False),
        sa.Column("configured", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("generation", sa.BigInteger(), nullable=False),
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
        sa.CheckConstraint("generation >= 1", name="ck_managed_secrets_generation"),
        sa.CheckConstraint(
            "octet_length(nonce) = 12",
            name="ck_managed_secrets_nonce_length",
        ),
        sa.PrimaryKeyConstraint("name"),
    )
    op.create_table(
        "managed_secret_events",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("action", sa.String(length=16), nullable=False),
        sa.Column("actor", sa.JSON(), nullable=False),
        sa.Column("generation", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "action IN ('imported', 'replaced', 'cleared', 'rotated')",
            name="ck_managed_secret_events_action",
        ),
        sa.CheckConstraint("generation >= 1", name="ck_managed_secret_events_generation"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_managed_secret_events_name",
        "managed_secret_events",
        ["name"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_managed_secret_events_name", table_name="managed_secret_events")
    op.drop_table("managed_secret_events")
    op.drop_table("managed_secrets")
