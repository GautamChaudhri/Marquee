"""Managed taste-profile / learned-head artifact metadata."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from marquee.database import Base
from marquee.models.base import TimestampMixin


class ArtifactSnapshot(Base, TimestampMixin):
    __tablename__ = "artifact_snapshots"
    __table_args__ = (
        Index("ix_artifact_snapshots_kind_status", "kind", "status"),
        Index("ix_artifact_snapshots_kind_activated_at", "kind", "activated_at"),
        UniqueConstraint("kind", "storage_path", name="uq_artifact_snapshot_kind_storage_path"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="archived")
    label: Mapped[str] = mapped_column(String(255), nullable=False)
    storage_path: Mapped[str] = mapped_column(Text, nullable=False)
    active_path: Mapped[str] = mapped_column(Text, nullable=False)
    model_name: Mapped[str | None] = mapped_column(String(80))
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    source_mode: Mapped[str | None] = mapped_column(String(40))
    imported_from_active: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    summary: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    trained_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ArtifactSnapshotMovie(Base):
    __tablename__ = "artifact_snapshot_movies"
    __table_args__ = (
        Index("ix_artifact_snapshot_movies_artifact_id", "artifact_id"),
        Index("ix_artifact_snapshot_movies_movie_id", "movie_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    artifact_id: Mapped[str] = mapped_column(
        ForeignKey("artifact_snapshots.id", ondelete="CASCADE"),
        nullable=False,
    )
    movie_id: Mapped[int | None] = mapped_column(ForeignKey("movies.id", ondelete="SET NULL"))
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    year: Mapped[int | None] = mapped_column(Integer)
    tmdb_id: Mapped[int | None] = mapped_column(Integer)
    contribution_count: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
