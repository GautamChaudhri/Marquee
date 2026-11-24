"""Residual trainings: every attempt to train the bounded correction, and the active artifact.

A training is recorded whether or not it produced an artifact. One that did keeps the file in
``data/ml/residuals/<sha256>.npz`` (``file_name``, never a path); at most one row is active,
and an active row is bound to the profile it was trained for: publishing another profile
makes it inactive.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from marquee.db.base import Base, TimestampMixin
from marquee.db.models.run import SHA256_CHECK

OUTCOME_ACTIVATE = "activate"
OUTCOME_NO_CHANGE = "no_change"
OUTCOME_FAILED = "failed"
TRAINING_OUTCOMES = (OUTCOME_ACTIVATE, OUTCOME_NO_CHANGE, OUTCOME_FAILED)
"""``activate`` and ``no_change`` are the engine's own outcomes; ``failed`` is a training that
raised."""


def _one_of(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class ResidualTraining(TimestampMixin, Base):
    """One training of the residual from the owner's feedback, and what came of it.

    ``report`` is the engine's report as it gave it. ``evidence_digest`` names the ordered
    events it trained from. ``artifact_sha256`` and ``file_name`` are set only on a row whose
    training produced an artifact; ``is_active`` only on such a row, at most one at a time.
    """

    __tablename__ = "residual_trainings"
    __table_args__ = (
        CheckConstraint(_one_of("outcome", TRAINING_OUTCOMES), name="outcome"),
        CheckConstraint("subjects >= 0 AND pairs >= 0 AND events >= 0", name="counts"),
        CheckConstraint(f"profile_sha256 {SHA256_CHECK}", name="profile_sha256"),
        CheckConstraint(f"evidence_digest {SHA256_CHECK}", name="evidence_digest"),
        CheckConstraint(f"artifact_sha256 {SHA256_CHECK}", name="artifact_sha256"),
        CheckConstraint("(artifact_sha256 IS NULL) = (file_name IS NULL)", name="artifact_file"),
        CheckConstraint(
            f"(artifact_sha256 IS NOT NULL) = (outcome = '{OUTCOME_ACTIVATE}')", name="artifact"
        ),
        CheckConstraint("NOT is_active OR artifact_sha256 IS NOT NULL", name="active_artifact"),
        CheckConstraint(
            "file_name IS NULL OR file_name = artifact_sha256 || '.npz'", name="file_name"
        ),
        # At most one active artifact.
        Index(
            "ix_residual_trainings_active",
            "is_active",
            unique=True,
            postgresql_where=text("is_active"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    job_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("jobs.id", ondelete="SET NULL")
    )
    """The rebuild job that trained it, or null for ``marquee taste residual train``."""
    profile_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("taste_profiles.id"), index=True
    )
    profile_sha256: Mapped[str] = mapped_column(Text, nullable=False)
    outcome: Mapped[str] = mapped_column(Text, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    subjects: Mapped[int] = mapped_column(Integer, nullable=False)
    pairs: Mapped[int] = mapped_column(Integer, nullable=False)
    events: Mapped[int] = mapped_column(Integer, nullable=False)
    evidence_digest: Mapped[str] = mapped_column(Text, nullable=False)
    report: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    artifact_sha256: Mapped[str | None] = mapped_column(Text, unique=True)
    file_name: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    deactivated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    def __repr__(self) -> str:
        return f"<ResidualTraining id={self.id} {self.outcome} active={self.is_active}>"
