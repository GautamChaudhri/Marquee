"""Tracked permanent letterbox re-encode artifacts."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from marquee.database import Base


class LetterboxReencodeArtifact(Base):
    """Generated candidate and saved-original files for a re-encode job."""

    __tablename__ = "letterbox_reencode_artifacts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(
        ForeignKey("media_jobs.job_id", ondelete="SET NULL"), index=True, nullable=True
    )
    movie_id: Mapped[int] = mapped_column(
        ForeignKey("movies.id", ondelete="CASCADE"), index=True, nullable=False
    )
    media_file_id: Mapped[int | None] = mapped_column(
        ForeignKey("media_files.id", ondelete="SET NULL"), index=True, nullable=True
    )

    original_path: Mapped[str] = mapped_column(Text, nullable=False)
    candidate_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    saved_original_path: Mapped[str | None] = mapped_column(Text, nullable=True)

    original_size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    candidate_size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    saved_original_size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    original_signature: Mapped[str | None] = mapped_column(String(128), nullable=True)
    candidate_signature: Mapped[str | None] = mapped_column(String(128), nullable=True)
    saved_original_signature: Mapped[str | None] = mapped_column(String(128), nullable=True)

    encoder: Mapped[str | None] = mapped_column(String(40), nullable=True)
    encoder_family: Mapped[str | None] = mapped_column(String(20), nullable=True)
    codec: Mapped[str | None] = mapped_column(String(20), nullable=True)
    crop_top: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    crop_bottom: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    hdr_status: Mapped[str | None] = mapped_column(String(24), nullable=True)
    dovi_status: Mapped[str | None] = mapped_column(String(24), nullable=True)
    detail_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    # candidate_ready | kept | replaced | restored | deleted | failed | missing
    status: Mapped[str] = mapped_column(
        String(24), nullable=False, default="candidate_ready", server_default="'candidate_ready'"
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=True
    )

    def __repr__(self) -> str:
        return f"<LetterboxReencodeArtifact(id={self.id}, status={self.status!r})>"
