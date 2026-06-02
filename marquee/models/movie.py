"""Movie model — one row per film in the user's library."""

from __future__ import annotations

from sqlalchemy import Boolean, Index, Integer, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from marquee.database import Base
from marquee.models.base import ArtworkMixin, TimestampMixin


class Movie(Base, TimestampMixin, ArtworkMixin):
    """A movie identified by Radarr or standalone filesystem scan.

    Poster retrieval uses ``tmdb_id`` as the universal lookup key.
    HDR/DV tracking uses ``has_hdr`` / ``has_dv`` (NULL = not yet checked).
    """

    __tablename__ = "movies"

    # ── Primary Key ──────────────────────────────────────────────────
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # ── Identity ─────────────────────────────────────────────────────
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    year: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    tmdb_id: Mapped[int | None] = mapped_column(
        Integer, unique=True, index=True, nullable=True
    )
    imdb_id: Mapped[str | None] = mapped_column(String(20), nullable=True)

    # ── Filesystem ───────────────────────────────────────────────────
    folder_path: Mapped[str] = mapped_column(Text, nullable=False)
    movie_file_path: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ── Origin ───────────────────────────────────────────────────────
    radarr_id: Mapped[int | None] = mapped_column(
        Integer, unique=True, index=True, nullable=True
    )

    # ── Quality / HDR-DV (Phase N) ────────────────────────────────────
    quality_profile_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    has_hdr: Mapped[bool | None] = mapped_column(
        Boolean, nullable=True, comment="NULL=not checked, True=has HDR, False=missing HDR"
    )
    has_dv: Mapped[bool | None] = mapped_column(
        Boolean, nullable=True, comment="NULL=not checked, True=has DV, False=missing DV"
    )

    # ── Indexes ──────────────────────────────────────────────────────
    __table_args__ = (
        Index(
            "ix_movies_missing_poster",
            "id",
            sqlite_where=text("poster_path IS NULL"),
        ),
    )

    def __repr__(self) -> str:
        return f"<Movie(id={self.id}, title={self.title!r}, tmdb_id={self.tmdb_id})>"
