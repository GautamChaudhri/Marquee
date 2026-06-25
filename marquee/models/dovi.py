"""Dolby Vision analysis state — one row per movie.

``DoviState`` holds the result of a deep DoVi inspection (ffprobe + dovi_tool):
the exact profile (5 / 7 / 8.x), level, enhancement-layer presence and — for
profile 7 — whether the EL is **FEL** (full, playback-hostile) or **MEL**
(minimal, safe). Radarr only tells us *that* a file is Dolby Vision; this is
where the *kind* lives, so the HDR detail page and the (future) Profile 5→8.1 /
FEL→8.1 remediation jobs have something to act on.

Mirrors ``LetterboxState``: one upserted row per movie, keyed by ``movie_id``.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from marquee.database import Base
from marquee.models.base import TimestampMixin


class DoviState(Base, TimestampMixin):
    """Latest Dolby Vision analysis state for one movie."""

    __tablename__ = "dovi_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    movie_id: Mapped[int] = mapped_column(
        ForeignKey("movies.id", ondelete="CASCADE"),
        unique=True,
        index=True,
        nullable=False,
    )

    # unknown | analyzing | analyzed | not_dovi | error
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'unknown'")
    )

    # Dolby Vision profile (5, 7, 8 ...) and level, from the RPU / ffprobe.
    dovi_profile: Mapped[int | None] = mapped_column(Integer, nullable=True)
    dovi_level: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Enhancement-layer presence + kind. el_type is only meaningful for profile
    # 7: "FEL" (full enhancement layer) or "MEL" (minimal). None = single-layer
    # (profiles 5/8) or EL type could not be determined.
    el_present: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    el_type: Mapped[str | None] = mapped_column(String(4), nullable=True)

    # Base-layer signal compatibility id (0 = none/P5, 1 = HDR10, 2 = SDR, 4 = HLG).
    bl_signal_compatibility_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    source_codec: Mapped[str | None] = mapped_column(String(16), nullable=True)

    # Raw ``dovi_tool info --summary`` text, kept for the inspection UI / debugging.
    rpu_summary_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    error_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_analyzed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    def __repr__(self) -> str:
        return (
            f"<DoviState(movie_id={self.movie_id}, status={self.status!r}, "
            f"profile={self.dovi_profile!r}, el_type={self.el_type!r})>"
        )
