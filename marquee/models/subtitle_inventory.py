"""Subtitle inventory — the persisted, cached view of a file's subtitle tracks.

One ``SubtitleInventory`` per media file holds the container-level facts (audio
languages, duration, coverage summary) plus a ``file_signature`` used purely for
stale-plan detection (it is a cheap path+size+mtime+edge-block hash, NOT a
content hash of a multi-GB file). ``SubtitleTrack`` rows are versioned by their
parent inventory and replaced transactionally on each rescan, so a remux that
renumbers streams yields a fresh inventory with fresh track IDs.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from marquee.database import Base


class SubtitleInventory(Base):
    """Current subtitle/container snapshot for one media file."""

    __tablename__ = "subtitle_inventories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    media_file_id: Mapped[int] = mapped_column(
        ForeignKey("media_files.id", ondelete="CASCADE"),
        unique=True,
        index=True,
        nullable=False,
    )
    file_signature: Mapped[str | None] = mapped_column(String(128), nullable=True)
    container: Mapped[str | None] = mapped_column(String(20), nullable=True)
    duration_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    audio_streams_json: Mapped[str | None] = mapped_column(JSON, nullable=True)
    chapters_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    attachments_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    coverage_json: Mapped[str | None] = mapped_column(JSON, nullable=True)
    probe_tool_versions_json: Mapped[str | None] = mapped_column(JSON, nullable=True)
    scanned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    def __repr__(self) -> str:
        return (
            f"<SubtitleInventory(media_file_id={self.media_file_id}, container={self.container!r})>"
        )


class SubtitleTrack(Base):
    """One subtitle track (embedded stream or external sidecar)."""

    __tablename__ = "subtitle_tracks"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    inventory_id: Mapped[int] = mapped_column(
        ForeignKey("subtitle_inventories.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )

    # embedded | external
    source: Mapped[str] = mapped_column(String(10), nullable=False)
    stream_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tool_track_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    external_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    paired_path: Mapped[str | None] = mapped_column(Text, nullable=True)

    codec: Mapped[str | None] = mapped_column(String(40), nullable=True)
    # text | bitmap | teletext | unknown
    kind: Mapped[str] = mapped_column(String(20), nullable=False, default="unknown")

    language_raw: Mapped[str | None] = mapped_column(String(40), nullable=True)
    language_tag: Mapped[str] = mapped_column(String(40), nullable=False, default="und")
    # metadata | filename | user | unknown
    language_source: Mapped[str] = mapped_column(String(20), nullable=False, default="unknown")

    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_forced: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_sdh: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_commentary: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_generated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    content_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    metadata_json: Mapped[str | None] = mapped_column(JSON, nullable=True)

    def __repr__(self) -> str:
        return (
            f"<SubtitleTrack(id={self.id!r}, source={self.source!r}, "
            f"lang={self.language_tag!r}, codec={self.codec!r})>"
        )
