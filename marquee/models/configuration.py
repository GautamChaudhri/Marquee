"""Immutable, versioned application configuration storage."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
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


class ManagedSecret(Base):
    """One encrypted integration credential; plaintext never enters revisions."""

    __tablename__ = "managed_secrets"
    __table_args__ = (
        CheckConstraint("generation >= 1", name="ck_managed_secrets_generation"),
        CheckConstraint("octet_length(nonce) = 12", name="ck_managed_secrets_nonce_length"),
    )

    name: Mapped[str] = mapped_column(String(80), primary_key=True)
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    nonce: Mapped[bytes] = mapped_column(LargeBinary(12), nullable=False)
    key_id: Mapped[str] = mapped_column(String(80), nullable=False)
    configured: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    generation: Mapped[int] = mapped_column(BigInteger, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class ManagedSecretEvent(Base):
    """Value-free audit trail for credential lifecycle operations."""

    __tablename__ = "managed_secret_events"
    __table_args__ = (
        CheckConstraint(
            "action IN ('imported', 'replaced', 'cleared', 'rotated')",
            name="ck_managed_secret_events_action",
        ),
        CheckConstraint("generation >= 1", name="ck_managed_secret_events_generation"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(16), nullable=False)
    actor: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    generation: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
