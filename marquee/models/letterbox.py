"""Letterbox models — per-movie crop-detection state + an audit trail.

``LetterboxState`` is one row per movie: the latest detection verdict, the
recommended crop, what's currently applied, and the workflow status that drives
the three UI tabs (Candidates / Tagged / Skipped). ``LetterboxEvent`` is the
append-only history (detect / apply / remove / ignore / heal / error), mirroring
``ArtworkEvent`` for posters.

Phase 1 is movies-only (matching ``PosterService``); the engine itself is
media-type agnostic, so TV support later adds nullable ``series_id`` /
``episode_id`` columns without reshaping this table.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from marquee.database import Base
from marquee.models.base import TimestampMixin


class LetterboxState(Base, TimestampMixin):
    """Latest letterbox detection/application state for one movie."""

    __tablename__ = "letterbox_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    movie_id: Mapped[int] = mapped_column(
        ForeignKey("movies.id", ondelete="CASCADE"),
        unique=True,
        index=True,
        nullable=False,
    )

    # prefilter_candidate | prefilter_unknown | prefilter_skipped
    #   | candidate | not_letterboxed | variable_unsafe | tagged | skipped
    #   | ineligible | errored
    status: Mapped[str] = mapped_column(
        String(24), nullable=False, server_default=text("'prefilter_candidate'")
    )

    # high | medium | variable | low | none
    confidence: Mapped[str | None] = mapped_column(String(8), nullable=True)

    # Eligibility for tag application (MKV + writable + has a video track).
    eligible: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("1")
    )
    ineligible_reason: Mapped[str | None] = mapped_column(String(120), nullable=True)

    # Encoded source dimensions (from sync metadata or an ffprobe fallback).
    source_width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_height: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Cheap resolution-only stage-1 triage. This is intentionally separate
    # from the detector verdict because stage 1 has known false positives.
    prefilter_bucket: Mapped[str | None] = mapped_column(String(24), nullable=True)
    prefilter_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    prefilter_aspect_ratio: Mapped[float | None] = mapped_column(Float, nullable=True)
    last_prefiltered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Recommended crop (px). Symmetric unless asymmetric mode is on.
    recommended_crop_top: Mapped[int | None] = mapped_column(Integer, nullable=True)
    recommended_crop_bottom: Mapped[int | None] = mapped_column(Integer, nullable=True)
    aspect_label: Mapped[str | None] = mapped_column(String(12), nullable=True)

    # Currently-applied crop (NULL = no tags on the file).
    applied_crop_top: Mapped[int | None] = mapped_column(Integer, nullable=True)
    applied_crop_bottom: Mapped[int | None] = mapped_column(Integer, nullable=True)

    detect_method: Mapped[str | None] = mapped_column(String(16), nullable=True)

    # Per-timestamp breakdown (JSON) so the inspection UI + confidence are
    # reproducible without re-running detection.
    samples_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    # User has reviewed this row (variable-unsafe / not-letterboxed / ignored)
    # so library re-scans don't re-flag it.
    reviewed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("0")
    )

    last_detected_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_applied_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    # True when the detector found two or more well-supported, mutually
    # disagreeing bar clusters in one file (e.g. IMAX 1.90:1 expansion scenes
    # mixed with 2.40:1 scope, or full-frame 16:9 scenes mixed with letterboxed
    # ones). `variable_ar_note` is a human-readable explanation for the UI.
    variable_ar: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("0")
    )
    variable_ar_note: Mapped[str | None] = mapped_column(Text, nullable=True)

    def __repr__(self) -> str:
        return (
            f"<LetterboxState(movie_id={self.movie_id}, status={self.status!r}, "
            f"confidence={self.confidence!r})>"
        )


class LetterboxEvent(Base):
    """One letterbox-lifecycle event for a movie (append-only audit trail)."""

    __tablename__ = "letterbox_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    movie_id: Mapped[int] = mapped_column(
        ForeignKey("movies.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )

    # detect | apply | remove | ignore | confirm | heal_reapply | error
    action: Mapped[str] = mapped_column(String(20), nullable=False)

    # detect | api | webhook | heal | manual
    source: Mapped[str] = mapped_column(String(20), nullable=False)

    detail: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return (
            f"<LetterboxEvent(id={self.id}, movie_id={self.movie_id}, "
            f"action={self.action!r})>"
        )
