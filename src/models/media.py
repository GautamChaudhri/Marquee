"""Media model — the single table representing every movie and TV show.

Design per ``design/01-project-spec.md`` Section 8:

- One table for both movies and shows (``media_type`` column discriminates).
- No boolean ``has_poster`` — derived from ``poster_path IS NULL``.
- Partial index ``idx_media_missing_poster`` only covers rows needing posters.
- At the scale of a personal library, SQLite is sub-millisecond for all queries.

All external IDs (tmdb_id, tvdb_id, imdb_id) are nullable because standalone
filesystem scans may not resolve every ID immediately.
"""

from __future__ import annotations

from typing import Optional

from sqlalchemy import (
    Boolean,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from marquee.database import Base
from marquee.models.base import TimestampMixin

# ---------------------------------------------------------------------------
# Media Type Enum (stored as string)
# ---------------------------------------------------------------------------

MEDIA_TYPE_MOVIE = "movie"
MEDIA_TYPE_SHOW = "show"


# ---------------------------------------------------------------------------
# Media
# ---------------------------------------------------------------------------


class Media(Base, TimestampMixin):
    """A movie or TV show in the user's library.

    Attributes:
        id: Internal primary key.
        title: Resolved title from *arr or filename parsing.
        tmdb_id: The Movie Database ID — universal key for artwork lookups.
        tvdb_id: TheTVDB ID — required by Fanart.tv for TV queries.
        imdb_id: IMDB ID (e.g. "tt1234567").
        media_type: ``"movie"`` or ``"show"``.
        file_path: Full folder path on disk.
        identified_by: How this item was discovered (``"radarr"``, ``"sonarr"``, ``"standalone"``).
        radarr_id: Radarr's internal ID (for API callbacks / webhook matching).
        sonarr_id: Sonarr's internal ID (for API callbacks / webhook matching).

        # Poster state
        poster_path: Path to the selected poster file. NULL = needs poster.
        poster_source: Which database the poster came from (``"tmdb"``, ``"fanart"``, etc.).
        poster_url: Original source URL for reference.
        ai_selected: Did the AI engine select this poster?

        # AI / deduplication data
        embedding: CLIP embedding of the selected poster (serialised numpy).
        sha256: SHA-256 hash of the poster file.
        phash: Perceptual hash of the poster file.

        # Timestamps
        created_at: When this row was first inserted.
        updated_at: When this row was last modified.
    """

    __tablename__ = "media"

    # ------------------------------------------------------------------
    # Primary Key
    # ------------------------------------------------------------------
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # ------------------------------------------------------------------
    # Identity
    # ------------------------------------------------------------------
    title: Mapped[str] = mapped_column(String(500), nullable=False)

    tmdb_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        unique=True,
        index=True,
        nullable=True,
    )

    tvdb_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        nullable=True,
    )

    imdb_id: Mapped[Optional[str]] = mapped_column(
        String(20),
        nullable=True,
    )

    media_type: Mapped[str] = mapped_column(
        String(10),
        nullable=False,
    )

    # ------------------------------------------------------------------
    # Filesystem
    # ------------------------------------------------------------------
    file_path: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    # ------------------------------------------------------------------
    # Origin
    # ------------------------------------------------------------------
    identified_by: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
    )

    radarr_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        index=True,
        nullable=True,
    )

    sonarr_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        index=True,
        nullable=True,
    )

    # ------------------------------------------------------------------
    # Poster State
    # ------------------------------------------------------------------
    poster_path: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )

    poster_source: Mapped[Optional[str]] = mapped_column(
        String(20),
        nullable=True,
    )

    poster_url: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )

    ai_selected: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
    )

    # ------------------------------------------------------------------
    # AI / Deduplication
    # ------------------------------------------------------------------
    embedding: Mapped[Optional[bytes]] = mapped_column(
        LargeBinary,
        nullable=True,
    )

    sha256: Mapped[Optional[str]] = mapped_column(
        String(64),
        nullable=True,
    )

    phash: Mapped[Optional[str]] = mapped_column(
        String(16),
        nullable=True,
    )

    # ------------------------------------------------------------------
    # Indexes
    # ------------------------------------------------------------------
    __table_args__ = (
        # Partial index covering only rows that need a poster.
        # SQLAlchemy resolves the column reference as a string at DDL time
        # so this is safe even though ``poster_path`` is defined below.
        Index(
            "idx_media_missing_poster",
            "id",
            sqlite_where=text("poster_path IS NULL"),
        ),
    )

    # ------------------------------------------------------------------
    # Derived Properties
    # ------------------------------------------------------------------

    @property
    def needs_poster(self) -> bool:
        """True when this item still needs a poster."""
        return self.poster_path is None

    @property
    def is_movie(self) -> bool:
        return self.media_type == MEDIA_TYPE_MOVIE

    @property
    def is_show(self) -> bool:
        return self.media_type == MEDIA_TYPE_SHOW

    # ------------------------------------------------------------------
    # Repr
    # ------------------------------------------------------------------

    def __repr__(self) -> str:
        return (
            f"<Media(id={self.id}, type={self.media_type!r}, "
            f"title={self.title!r}, tmdb_id={self.tmdb_id})>"
        )
