"""One row per movie Radarr lists, with or without a file."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Identity, Integer, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from marquee.db.base import Base, TimestampMixin


class Movie(TimestampMixin, Base):
    """A movie as Radarr reports it.

    ``radarr_path`` is the movie folder exactly as Radarr reports it, never a local path: the
    host and the container see the library at different paths and share this table.
    ``marquee.fs.paths`` works out the local folder each time one is needed. The taste code
    queries ``id``, ``title``, ``year``, ``tmdb_id``, and ``genres`` by name.
    """

    __tablename__ = "movies"

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    radarr_id: Mapped[int] = mapped_column(Integer, nullable=False, unique=True)
    # Not unique: one odd Radarr row must not fail a whole sync.
    tmdb_id: Mapped[int | None] = mapped_column(Integer, index=True)
    imdb_id: Mapped[str | None] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    sort_title: Mapped[str] = mapped_column(Text, nullable=False)
    year: Mapped[int | None] = mapped_column(Integer)
    genres: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    radarr_path: Mapped[str] = mapped_column(Text, nullable=False)

    has_file: Mapped[bool] = mapped_column(Boolean, nullable=False)
    file_relative_path: Mapped[str | None] = mapped_column(Text)
    file_size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    radarr_file_id: Mapped[int | None] = mapped_column(Integer)
    radarr_added_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # From TMDB, for the text gate: who directed it, the studios, and the tagline.
    director: Mapped[str | None] = mapped_column(Text)
    production_companies: Mapped[list[str] | None] = mapped_column(JSONB)
    tagline: Mapped[str | None] = mapped_column(Text)
    tmdb_enriched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # False once Radarr no longer lists the movie; the row is kept, never deleted.
    is_present: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    def __repr__(self) -> str:
        return f"<Movie id={self.id} radarr_id={self.radarr_id} title={self.title!r}>"
