"""Episode model — one row per episode file of a TV show."""

from __future__ import annotations

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from marquee.database import Base
from marquee.models.base import TimestampMixin


class Episode(Base, TimestampMixin):
    """A single episode file belonging to a TV series.

    Episodes do NOT carry ``ArtworkMixin`` — they don't get their own
    poster artwork.  The ``has_hdr`` / ``has_dv`` columns support the
    future HDR/DV tracking feature (Phase N).
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

    # ── Quality / HDR-DV (Phase N) ────────────────────────────────────
    has_hdr: Mapped[bool | None] = mapped_column(
        Boolean, nullable=True, comment="NULL=not checked, True=has HDR, False=missing HDR"
    )
    has_dv: Mapped[bool | None] = mapped_column(
        Boolean, nullable=True, comment="NULL=not checked, True=has DV, False=missing DV"
    )

    def __repr__(self) -> str:
        return (
            f"<Episode(id={self.id}, series_id={self.series_id}, "
            f"S{self.season_number:02d}E{self.episode_number:02d})>"
        )
