"""Season model — one row per season of a TV show."""

from __future__ import annotations

from sqlalchemy import ForeignKey, Index, Integer, UniqueConstraint, text
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
    tmdb_id: Mapped[int | None] = mapped_column(
        Integer, nullable=True, comment="TMDB season ID for artwork lookup"
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
        return (
            f"<Season(id={self.id}, series_id={self.series_id}, "
            f"season={self.season_number})>"
        )
