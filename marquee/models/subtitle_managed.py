"""Managed subtitle assets — the subtitle equivalent of PosterService restore.

When a user embeds an external/generated subtitle and opts into "manage across
upgrades", Marquee caches the exact source subtitle bytes under its own data
dir (never the media root, never full containers) and binds the asset to the
logical Movie/Episode. After a Radarr/Sonarr file replacement, missing managed
assets can be re-embedded through the normal, audited mutation queue.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from marquee.database import Base


class ManagedSubtitleAsset(Base):
    """A cached subtitle Marquee can re-embed after a media-file replacement."""

    __tablename__ = "managed_subtitle_assets"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    cache_path: Mapped[str] = mapped_column(Text, nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)

    language_tag: Mapped[str] = mapped_column(String(40), nullable=False, default="und")
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    kind: Mapped[str] = mapped_column(String(20), nullable=False, default="text")

    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_forced: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_sdh: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_commentary: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # external | generated | extracted
    source: Mapped[str] = mapped_column(String(20), nullable=False, default="external")
    provenance_json: Mapped[str | None] = mapped_column(JSON, nullable=True)

    restore_on_replacement: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_restored_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    def __repr__(self) -> str:
        return (
            f"<ManagedSubtitleAsset(id={self.id!r}, lang={self.language_tag!r}, "
            f"source={self.source!r})>"
        )


class ManagedSubtitleBinding(Base):
    """Binds a managed asset to its media, producing job, and logical owner.

    A single asset may bind to multiple Episode rows for a multi-episode file.
    Owner existence is validated in application code (polymorphic FK).
    """

    __tablename__ = "managed_subtitle_bindings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    asset_id: Mapped[str] = mapped_column(
        ForeignKey("managed_subtitle_assets.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    media_file_id: Mapped[int | None] = mapped_column(
        ForeignKey("media_files.id", ondelete="SET NULL"), index=True, nullable=True
    )
    source_job_id: Mapped[str | None] = mapped_column(String(32), index=True, nullable=True)
    source_attempt_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    source_fence_token: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # movie | episode; legacy assets may retain only this logical binding.
    owner_type: Mapped[str | None] = mapped_column(String(10), nullable=True)
    owner_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)

    def __repr__(self) -> str:
        return (
            f"<ManagedSubtitleBinding(asset_id={self.asset_id!r}, "
            f"{self.owner_type}={self.owner_id})>"
        )
