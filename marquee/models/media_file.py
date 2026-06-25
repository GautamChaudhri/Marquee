"""MediaFile — the physical-file unit of work for media mutations.

Subtitle (and future letterbox/HDR) operations modify a *file*, not a Movie or
Episode row. A movie maps to one active file; a multi-episode file maps to many
Episode rows via ``episode_media_files``. Jobs lock and fingerprint the exact
``MediaFile`` they mutate.

The stored ``path`` is in the *source application's* namespace (Radarr/Sonarr)
and is NEVER trusted as a local path — every filesystem operation re-resolves it
through ``safe_translate_and_validate()``. ``last_resolved_path`` is diagnostic
only.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from marquee.database import Base
from marquee.models.base import TimestampMixin


class MediaFile(Base, TimestampMixin):
    """One physical media file tracked from Radarr/Sonarr (or standalone)."""

    __tablename__ = "media_files"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # radarr | sonarr | standalone
    source: Mapped[str] = mapped_column(String(20), nullable=False)
    # Stable logical key, e.g. "radarr:movie-file:1234" / "sonarr:episode-file:5678"
    # or a path-derived fallback before the native id is known.
    source_key: Mapped[str] = mapped_column(String(200), unique=True, index=True, nullable=False)
    source_file_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    movie_id: Mapped[int | None] = mapped_column(
        ForeignKey("movies.id", ondelete="CASCADE"), index=True, nullable=True
    )

    path: Mapped[str] = mapped_column(Text, nullable=False)
    relative_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    container: Mapped[str | None] = mapped_column(String(20), nullable=True)

    # Current file vs. a replaced historical file kept for job/event history.
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_resolved_path: Mapped[str | None] = mapped_column(Text, nullable=True)

    def __repr__(self) -> str:
        return f"<MediaFile(id={self.id}, source_key={self.source_key!r}, active={self.is_active})>"


class EpisodeMediaFile(Base):
    """Association: which Episode rows live in which physical file.

    A double-episode file (one Sonarr episode-file id, two Episode rows) maps to
    two rows here sharing one ``media_file_id``.
    """

    __tablename__ = "episode_media_files"

    episode_id: Mapped[int] = mapped_column(
        ForeignKey("episodes.id", ondelete="CASCADE"), primary_key=True
    )
    media_file_id: Mapped[int] = mapped_column(
        ForeignKey("media_files.id", ondelete="CASCADE"), primary_key=True, index=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return (
            f"<EpisodeMediaFile(episode_id={self.episode_id}, media_file_id={self.media_file_id})>"
        )
