"""Movie model — one row per film in the user's library."""

from __future__ import annotations

from sqlalchemy import JSON, Boolean, Index, Integer, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from marquee.database import Base
from marquee.models.base import ArtworkMixin, TimestampMixin


class Movie(Base, TimestampMixin, ArtworkMixin):
    """A movie identified by Radarr or standalone filesystem scan.

    Poster retrieval uses ``tmdb_id`` as the universal lookup key.
    HDR/DV tracking uses ``hdr_type_raw`` + ``has_hdr`` / ``has_dv``.
    """

    __tablename__ = "movies"

    # ── Primary Key ──────────────────────────────────────────────────
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # ── Identity ─────────────────────────────────────────────────────
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    year: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    tmdb_id: Mapped[int | None] = mapped_column(Integer, unique=True, index=True, nullable=True)
    imdb_id: Mapped[str | None] = mapped_column(String(20), nullable=True)

    # ── Genres ───────────────────────────────────────────────────────
    # JSON array synced from Radarr (e.g. ["Action", "Thriller"]). Powers
    # label-diversity tracking (design 09) and the taste map (design 11).
    genres: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)

    # ── Filesystem ───────────────────────────────────────────────────
    folder_path: Mapped[str] = mapped_column(Text, nullable=False)
    movie_file_path: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ── Encoded video (letterbox pre-filter, design 04-letterbox §4) ──
    # Populated from Radarr movieFile.mediaInfo during sync; lets the
    # resolution pre-filter triage candidates with no frame decode.
    video_width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    video_height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    container: Mapped[str | None] = mapped_column(
        String(16), nullable=True, comment="Container/extension: matroska, mp4, ..."
    )

    # ── Origin ───────────────────────────────────────────────────────
    radarr_id: Mapped[int | None] = mapped_column(Integer, unique=True, index=True, nullable=True)

    # ── Quality / HDR-DV (Phase N) ────────────────────────────────────
    quality_profile_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    quality_cutoff_met: Mapped[bool | None] = mapped_column(
        Boolean,
        nullable=True,
        comment="NULL=unknown, True=current file meets Radarr cutoff, False=below cutoff",
    )
    current_cf_score: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        comment="Raw aggregate custom-format score from Radarr's current movieFile payload",
    )
    hdr_type_raw: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        comment=(
            "Raw Radarr dynamic-range descriptor; prefers videoDynamicRangeType and "
            "falls back to videoDynamicRange"
        ),
    )

    has_hdr: Mapped[bool | None] = mapped_column(
        Boolean, nullable=True, comment="NULL=not checked, True=has HDR, False=missing HDR"
    )
    has_dv: Mapped[bool | None] = mapped_column(
        Boolean, nullable=True, comment="NULL=not checked, True=has DV, False=missing DV"
    )

    # ── Audio/subtitle preference overrides ───────────────────────────
    # NULL means inherit the global subtitle settings. Lists are normalized
    # BCP-47 language tags and apply only to this movie.
    preferred_audio_languages_json: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    preferred_subtitle_languages_json: Mapped[list[str] | None] = mapped_column(
        JSON, nullable=True
    )

    # ── Indexes ──────────────────────────────────────────────────────
    __table_args__ = (
        Index(
            "ix_movies_missing_poster",
            "id",
            postgresql_where=text("poster_path IS NULL"),
        ),
    )

    def __repr__(self) -> str:
        return f"<Movie(id={self.id}, title={self.title!r}, tmdb_id={self.tmdb_id})>"
