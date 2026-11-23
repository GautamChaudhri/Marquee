"""Shared SQLAlchemy mixins — timestamps and artwork state."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, LargeBinary, String, Text, func, text
from sqlalchemy.orm import Mapped, mapped_column


class TimestampMixin:
    """Adds ``created_at`` and ``updated_at`` columns to a model.

    - ``created_at`` — set automatically on INSERT
    - ``updated_at`` — set on INSERT, refreshed on UPDATE
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


class ArtworkMixin:
    """Poster artwork state for Movie, Series, and Season.

    All columns are prefixed with ``poster_`` to enable clean extraction
    to a separate ``artwork`` table when additional artwork types
    (backdrops, logos, banners) are added in Phase N.

    Usage::

        class Movie(Base, TimestampMixin, ArtworkMixin):
            __tablename__ = "movies"
            ...
    """

    poster_path: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Deployed poster file path. NULL = needs poster.",
    )

    poster_source: Mapped[str | None] = mapped_column(
        String(20),
        nullable=True,
        comment="Source database: tmdb, fanart, tvdb, tvmaze",
    )

    poster_source_url: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Original remote URL for reference",
    )

    poster_ai_selected: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        comment="Was this poster chosen by the AI engine?",
    )

    poster_embedding: Mapped[bytes | None] = mapped_column(
        LargeBinary,
        nullable=True,
        comment="Serialised numpy CLIP embedding of the deployed poster",
    )

    poster_sha256: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        comment="SHA-256 hash of the deployed poster file",
    )

    poster_phash: Mapped[str | None] = mapped_column(
        String(16),
        nullable=True,
        comment="Perceptual hash of the deployed poster",
    )

    poster_user_approved: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
        comment="True = a human approved this poster (vs an unreviewed AI pick)",
    )

    poster_deployed_filename: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        comment="Filename written into the media folder (e.g. poster.jpg) — "
        "restoration re-uses this exact name",
    )

    poster_deployed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="When the poster was last deployed to the media folder",
    )

    poster_local_backup_path: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Local backup copy for restoring deployed poster bytes",
    )

    @property
    def needs_poster(self) -> bool:
        """True when this entity still needs a poster."""
        return self.poster_path is None
