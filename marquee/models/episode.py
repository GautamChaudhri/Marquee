"""Episode model — one row per episode file of a TV show."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, func, text
from sqlalchemy.orm import Mapped, mapped_column

from marquee.database import Base
from marquee.models.base import TimestampMixin


class Episode(Base, TimestampMixin):
    """A single episode file belonging to a TV series.

    Episodes do NOT carry ``ArtworkMixin`` — they don't get their own
    poster artwork; season and show artwork live on ``Season``/``Series``.
    """

    __tablename__ = "episodes"

    # ── Primary Key ──────────────────────────────────────────────────
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # ── Relationships ────────────────────────────────────────────────
    series_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("series.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # ── Identity ─────────────────────────────────────────────────────
    season_number: Mapped[int] = mapped_column(Integer, nullable=False)
    episode_number: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # ── Filesystem ───────────────────────────────────────────────────
    episode_file_path: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ── Origin ───────────────────────────────────────────────────────
    sonarr_episode_id: Mapped[int | None] = mapped_column(
        Integer, unique=True, index=True, nullable=True
    )

    is_present: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true"), index=True
    )
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    # ── Quality / HDR-DV (Phase N) ────────────────────────────────────
    video_width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    video_height: Mapped[int | None] = mapped_column(Integer, nullable=True)

    def __repr__(self) -> str:
        return (
            f"<Episode(id={self.id}, series_id={self.series_id}, "
            f"S{self.season_number:02d}E{self.episode_number:02d})>"
        )
