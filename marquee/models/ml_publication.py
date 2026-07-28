"""Fenced compare-and-set pointers for immutable ML artifacts."""

from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column

from marquee.database import Base


class MlActivePublication(Base):
    """The sole mutable row for an ML family; artifacts themselves remain immutable."""

    __tablename__ = "ml_active_publications"
    __table_args__ = (
        CheckConstraint("generation >= 1", name="ck_ml_active_publications_generation"),
        CheckConstraint("fence_token >= 1", name="ck_ml_active_publications_fence"),
    )

    family: Mapped[str] = mapped_column(String(48), primary_key=True)
    generation: Mapped[int] = mapped_column(BigInteger, nullable=False)
    artifact_id: Mapped[int] = mapped_column(
        ForeignKey("job_artifacts.id", ondelete="RESTRICT"), unique=True, nullable=False
    )
    version: Mapped[str] = mapped_column(String(96), nullable=False)
    checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="RESTRICT"), nullable=False)
    attempt_id: Mapped[int] = mapped_column(
        ForeignKey("job_attempts.id", ondelete="RESTRICT"), nullable=False
    )
    fence_token: Mapped[int] = mapped_column(BigInteger, nullable=False)
    activated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
