"""Letterbox models — crop-detection state + an audit trail.

``LetterboxState`` stores the latest detector + workflow truth for one movie or
episode. Status vocabulary: ``prefilter_candidate`` | ``prefilter_unknown`` |
``prefilter_skipped`` | ``candidate`` | ``sampled_clear`` |
``not_letterboxed`` | ``variable_unsafe`` | ``tagged`` | ``reencoded`` |
``skipped`` | ``ineligible`` | ``errored``. ``LetterboxEvent`` is the
append-only audit history (detect / apply / remove / ignore / confirm /
mark_not_letterboxed / heal_reapply / reset / error), mirroring
``ArtworkEvent`` for posters.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
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
    """Latest letterbox detection/application state for one movie or episode."""

    __tablename__ = "letterbox_state"
    __table_args__ = (
        CheckConstraint(
            "(media_type = 'movie' AND movie_id IS NOT NULL AND episode_id IS NULL) OR "
            "(media_type = 'episode' AND episode_id IS NOT NULL AND movie_id IS NULL)",
            name="ck_letterbox_state_subject",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    movie_id: Mapped[int | None] = mapped_column(
        ForeignKey("movies.id", ondelete="CASCADE"),
        unique=True,
        index=True,
        nullable=True,
    )
    media_type: Mapped[str] = mapped_column(
        String(10),
        nullable=False,
        default="movie",
        server_default=text("'movie'"),
        index=True,
    )
    episode_id: Mapped[int | None] = mapped_column(
        ForeignKey("episodes.id", ondelete="CASCADE"),
        unique=True,
        index=True,
        nullable=True,
    )
    # prefilter_candidate | prefilter_unknown | prefilter_skipped
    #   | candidate | sampled_clear | not_letterboxed | variable_unsafe
    #   | tagged | reencoded | skipped | ineligible | errored
    status: Mapped[str] = mapped_column(
        String(24), nullable=False, server_default=text("'prefilter_candidate'")
    )

    # high | medium | variable | low | none
    confidence: Mapped[str | None] = mapped_column(String(8), nullable=True)

    # Eligibility for tag application (MKV + writable + has a video track).
    eligible: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
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
        Boolean, nullable=False, default=False, server_default=text("false")
    )

    last_detected_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_by: Mapped[str | None] = mapped_column(String(16), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    original_crop_top: Mapped[int | None] = mapped_column(Integer, nullable=True)
    original_crop_bottom: Mapped[int | None] = mapped_column(Integer, nullable=True)
    original_aspect_label: Mapped[str | None] = mapped_column(String(12), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    # True when the detector found two or more well-supported, mutually
    # disagreeing bar clusters in one file (e.g. IMAX 1.90:1 expansion scenes
    # mixed with 2.40:1 scope, or full-frame 16:9 scenes mixed with letterboxed
    # ones). `variable_ar_note` is a human-readable explanation for the UI.
    variable_ar: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    variable_ar_note: Mapped[str | None] = mapped_column(Text, nullable=True)

    def __repr__(self) -> str:
        return (
            f"<LetterboxState(media_type={self.media_type!r}, movie_id={self.movie_id}, "
            f"episode_id={self.episode_id}, status={self.status!r}, "
            f"confidence={self.confidence!r})>"
        )


class LetterboxEvent(Base):
    """One letterbox-lifecycle event for a movie or episode subject."""

    __tablename__ = "letterbox_events"
    __table_args__ = (
        CheckConstraint(
            "(media_type = 'movie' AND movie_id IS NOT NULL AND episode_id IS NULL) OR "
            "(media_type = 'episode' AND episode_id IS NOT NULL AND movie_id IS NULL) OR "
            "(movie_id IS NULL AND episode_id IS NULL)",
            name="ck_letterbox_event_subject",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    movie_id: Mapped[int | None] = mapped_column(
        ForeignKey("movies.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )
    media_type: Mapped[str] = mapped_column(
        String(10),
        nullable=False,
        default="movie",
        server_default=text("'movie'"),
        index=True,
    )
    episode_id: Mapped[int | None] = mapped_column(
        ForeignKey("episodes.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )
    subject_snapshot: Mapped[dict] = mapped_column(
        JSON, nullable=False, default=dict, server_default="{}"
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
            f"<LetterboxEvent(id={self.id}, media_type={self.media_type!r}, "
            f"movie_id={self.movie_id}, episode_id={self.episode_id}, action={self.action!r})>"
        )
