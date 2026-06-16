"""Tracked backups of mutated media files (design §16.7 / §22.1).

Backups live under a hidden ``.marquee/backups/`` dir on the same filesystem as
the source but outside the Radarr/Sonarr title folder, and are tracked here so
they can be restored or explicitly deleted. No automatic cleanup by default.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from marquee.database import Base


class MediaBackup(Base):
    """A retained pre-mutation copy of a media file."""

    __tablename__ = "media_backups"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    job_id: Mapped[str | None] = mapped_column(
        ForeignKey("media_jobs.job_id", ondelete="SET NULL"), index=True, nullable=True
    )
    media_file_id: Mapped[int | None] = mapped_column(
        ForeignKey("media_files.id", ondelete="SET NULL"), index=True, nullable=True
    )

    original_path: Mapped[str] = mapped_column(Text, nullable=False)
    backup_path: Mapped[str] = mapped_column(Text, nullable=False)
    original_signature: Mapped[str | None] = mapped_column(String(128), nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    # available | restored | deleted | missing
    status: Mapped[str] = mapped_column(
        String(12), nullable=False, default="available", server_default="'available'"
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=True
    )

    def __repr__(self) -> str:
        return f"<MediaBackup(id={self.id!r}, status={self.status!r})>"
