"""Series model — one row per TV show in the user's library."""

from __future__ import annotations

from sqlalchemy import JSON, Index, Integer, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from marquee.database import Base
from marquee.models.base import ArtworkMixin, TimestampMixin


class Series(Base, TimestampMixin, ArtworkMixin):
    """A TV show identified by Sonarr or standalone filesystem scan.

    Poster retrieval uses ``tmdb_id`` when available, falling back to
    ``tvdb_id`` for Fanart.tv queries.  Sonarr primarily uses TVDB IDs.
    """

    __tablename__ = "series"

    # ── Primary Key ──────────────────────────────────────────────────
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # ── Identity ─────────────────────────────────────────────────────
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    year: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    tvdb_id: Mapped[int | None] = mapped_column(
        Integer,
        unique=True,
        index=True,
        nullable=True,
        comment="Primary ID from Sonarr; required by Fanart.tv",
    )
    tmdb_id: Mapped[int | None] = mapped_column(
        Integer,
        index=True,
        nullable=True,
        comment="Resolved from Sonarr or TMDB /find endpoint",
    )
    imdb_id: Mapped[str | None] = mapped_column(String(20), nullable=True)

    # ── Filesystem ───────────────────────────────────────────────────
    series_path: Mapped[str] = mapped_column(
        Text, nullable=False, comment="Root folder path from Sonarr"
    )

    # ── Origin ───────────────────────────────────────────────────────
    sonarr_id: Mapped[int | None] = mapped_column(Integer, unique=True, index=True, nullable=True)

    # ── Sonarr Metadata ──────────────────────────────────────────────
    quality_profile_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    season_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # ── TMDB-enriched metadata (OCR text-gate classification) ─────────
    tagline: Mapped[str | None] = mapped_column(Text, nullable=True)
    director: Mapped[str | None] = mapped_column(String(500), nullable=True)
    production_companies_json: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)

    # ── Audio/subtitle preference overrides ───────────────────────────
    # NULL means inherit the global subtitle settings. Lists are normalized
    # BCP-47 language tags and apply to all seasons/episodes in the series.
    preferred_audio_languages_json: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    preferred_subtitle_languages_json: Mapped[list[str] | None] = mapped_column(
        JSON, nullable=True
    )

    # ── Text Profile Overrides ───────────────────────────────────────
    show_text_profile_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    season_text_profile_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # ── Indexes ──────────────────────────────────────────────────────
    __table_args__ = (
        Index(
            "ix_series_missing_poster",
            "id",
            postgresql_where=text("poster_path IS NULL"),
        ),
    )

    def __repr__(self) -> str:
        return f"<Series(id={self.id}, title={self.title!r}, tvdb_id={self.tvdb_id})>"
