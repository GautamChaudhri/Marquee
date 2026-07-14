"""Immutable, versioned application configuration storage."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from marquee.database import Base


class ConfigurationRevision(Base):
    """One immutable, fully validated database-owned configuration document."""

    __tablename__ = "configuration_revisions"

    version: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    values: Mapped[dict] = mapped_column(JSON, nullable=False)
    checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    actor: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    trigger: Mapped[str] = mapped_column(String(40), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ConfigurationCurrent(Base):
    """Singleton pointer to the current immutable configuration revision."""

    __tablename__ = "configuration_current"
    __table_args__ = (
        CheckConstraint("singleton_id = 1", name="ck_configuration_current_singleton"),
    )

    singleton_id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    current_version: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("configuration_revisions.version", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
