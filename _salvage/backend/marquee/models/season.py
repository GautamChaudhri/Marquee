"""Season model — one row per season of a TV show."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from marquee.database import Base
from marquee.models.base import ArtworkMixin, TimestampMixin


class Season(Base, TimestampMixin, ArtworkMixin):
    """A season of a TV series with its own poster state.

    Season posters are deployed to the show's root folder using a naming
    convention like ``season01-poster.jpg``.  The ``poster_path`` column
    stores the full absolute path.
    """

    __tablename__ = "seasons"

    # ── Primary Key ──────────────────────────────────────────────────
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # ── Relationships ────────────────────────────────────────────────
    series_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("series.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # ── Identity ─────────────────────────────────────────────────────
    season_number: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str | None] = mapped_column(
        String(500), nullable=True, comment="Official season name from TMDB"
    )
    tmdb_id: Mapped[int | None] = mapped_column(
        Integer, nullable=True, comment="TMDB season ID for artwork lookup"
    )

    is_present: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true"), index=True
    )
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    # ── Statistics ───────────────────────────────────────────────────
    episode_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    episode_file_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0", index=True
    )

    # ── Constraints ──────────────────────────────────────────────────
    __table_args__ = (
        UniqueConstraint("series_id", "season_number", name="uq_seasons_series_number"),
        Index(
            "ix_seasons_missing_poster",
            "id",
            postgresql_where=text("poster_path IS NULL"),
        ),
    )

    def __repr__(self) -> str:
        return f"<Season(id={self.id}, series_id={self.series_id}, season={self.season_number})>"
