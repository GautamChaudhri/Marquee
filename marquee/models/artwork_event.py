"""ArtworkEvent model — append-only audit trail of poster deployments.

Every time a poster is written, restored, or a restore is attempted (by the
pipeline, the feedback endpoint, a webhook, or the self-heal scan) one row is
recorded here. This is the restoration-history feed for the frontend and the
debugging trail for webhook behavior.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from marquee.database import Base


class ArtworkEvent(Base):
    """One poster-lifecycle event for a movie."""

    __tablename__ = "artwork_events"

    __table_args__ = (
        CheckConstraint(
            "(media_type = 'movie'  AND movie_id  IS NOT NULL) OR "
            "(media_type = 'series' AND series_id IS NOT NULL) OR "
            "(media_type = 'season' AND season_id IS NOT NULL)",
            name="ck_artwork_events_subject",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    movie_id: Mapped[int | None] = mapped_column(
        ForeignKey("movies.id", ondelete="CASCADE"),
        index=True,
        nullable=True,
    )

    media_type: Mapped[str] = mapped_column(
        String(10),
        index=True,
        nullable=False,
        default="movie",
        server_default="movie",
    )

    series_id: Mapped[int | None] = mapped_column(
        ForeignKey("series.id", ondelete="CASCADE"),
        index=True,
        nullable=True,
    )

    season_id: Mapped[int | None] = mapped_column(
        ForeignKey("seasons.id", ondelete="CASCADE"),
        index=True,
        nullable=True,
    )

    # deploy | restore | restore_failed | heal_restore | webhook_noop | webhook_error
    action: Mapped[str] = mapped_column(String(20), nullable=False)

    # pipeline | feedback | webhook | heal | manual
    source: Mapped[str] = mapped_column(String(20), nullable=False)

    # JSON: old/new paths, cache hit/miss, error string, etc.
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return (
            f"<ArtworkEvent(id={self.id}, movie_id={self.movie_id}, "
            f"action={self.action!r}, source={self.source!r})>"
        )
