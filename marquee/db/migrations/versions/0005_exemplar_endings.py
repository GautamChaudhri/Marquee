"""Exemplars record the event that ended them; exemplars and taste profiles gain updated_at.

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-24

``exemplars.ended_by_event_id`` names the act of feedback that superseded or revoked a row,
so a revoke can bring back what that event ended. Rows already ended keep null there. Both
tables change rows in place (an exemplar's state, a profile's ``is_active``), so both gain
``updated_at``; existing rows get the time of the upgrade.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | Sequence[str] | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _updated_at() -> sa.Column:
    return sa.Column(
        "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
    )


def upgrade() -> None:
    op.add_column("exemplars", sa.Column("ended_by_event_id", sa.BigInteger(), nullable=True))
    op.create_foreign_key(
        op.f("fk_exemplars_ended_by_event_id_preference_events"),
        "exemplars",
        "preference_events",
        ["ended_by_event_id"],
        ["id"],
    )
    op.create_check_constraint(
        op.f("ck_exemplars_ended_by"),
        "exemplars",
        "state <> 'active' OR ended_by_event_id IS NULL",
    )
    op.add_column("exemplars", _updated_at())
    op.add_column("taste_profiles", _updated_at())


def downgrade() -> None:
    op.drop_column("taste_profiles", "updated_at")
    op.drop_column("exemplars", "updated_at")
    op.drop_constraint(op.f("ck_exemplars_ended_by"), "exemplars", type_="check")
    op.drop_constraint(
        op.f("fk_exemplars_ended_by_event_id_preference_events"), "exemplars", type_="foreignkey"
    )
    op.drop_column("exemplars", "ended_by_event_id")
