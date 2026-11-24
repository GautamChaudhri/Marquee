"""Artwork files a movie folder holds: one row per movie per artwork kind."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Integer,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from marquee.db.base import Base, TimestampMixin

# Each is the only value its column accepts today. Adding one is a migration.
SUBJECT_MOVIE = "movie"
ARTWORK_POSTER = "poster"
ORIGIN_LIBRARY = "library"
"""Found in the movie folder by a sync, not written by Marquee."""


class Artwork(TimestampMixin, Base):
    """One artwork file inside a movie folder.

    ``file_name`` is the file's name inside the folder, never a path. ``file_modified_at``
    and ``size_bytes`` are the file's as they were when it was hashed; a sync hashes the file
    again only when either differs.
    """

    __tablename__ = "artwork"
    __table_args__ = (
        UniqueConstraint("movie_id", "artwork_kind"),
        CheckConstraint(f"subject_kind = '{SUBJECT_MOVIE}'", name="subject_kind"),
        CheckConstraint(f"artwork_kind = '{ARTWORK_POSTER}'", name="artwork_kind"),
        CheckConstraint(f"origin = '{ORIGIN_LIBRARY}'", name="origin"),
        CheckConstraint("sha256 ~ '^[0-9a-f]{64}$'", name="sha256"),
        CheckConstraint(
            "file_name <> '' AND file_name NOT IN ('.', '..') AND strpos(file_name, '/') = 0",
            name="file_name",
        ),
        CheckConstraint("size_bytes >= 0", name="size_bytes"),
    )

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    subject_kind: Mapped[str] = mapped_column(
        Text, nullable=False, default=SUBJECT_MOVIE, server_default=text(f"'{SUBJECT_MOVIE}'")
    )
    movie_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("movies.id", ondelete="CASCADE"), nullable=False
    )
    artwork_kind: Mapped[str] = mapped_column(Text, nullable=False)
    file_name: Mapped[str] = mapped_column(Text, nullable=False)
    sha256: Mapped[str] = mapped_column(Text, nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    file_modified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    origin: Mapped[str] = mapped_column(Text, nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    def __repr__(self) -> str:
        return (
            f"<Artwork id={self.id} movie_id={self.movie_id} kind={self.artwork_kind!r} "
            f"file_name={self.file_name!r}>"
        )
