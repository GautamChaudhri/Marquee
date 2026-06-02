"""Shared SQLAlchemy mixins."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, func
from sqlalchemy.orm import Mapped, mapped_column


class TimestampMixin:
    """Adds ``created_at`` and ``updated_at`` columns to a model.

    - ``created_at`` — set automatically on INSERT
    - ``updated_at`` — set on INSERT, refreshed on UPDATE

    Usage::

        class Media(Base, TimestampMixin):
            __tablename__ = "media"
            ...
    """

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=True,
    )
