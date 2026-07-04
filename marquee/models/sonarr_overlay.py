"""Normalized Sonarr overlay metadata synced from quality profiles and CFs."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from marquee.database import Base


class SonarrCustomFormat(Base):
    """A Sonarr custom-format definition."""

    __tablename__ = "sonarr_custom_formats"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    include_when_renaming: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    specifications_json: Mapped[list[dict] | None] = mapped_column(JSON, nullable=True)
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SonarrQualityProfile(Base):
    """A Sonarr quality profile definition."""

    __tablename__ = "sonarr_quality_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    upgrade_allowed: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    cutoff_format_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    min_format_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SonarrProfileFormatItem(Base):
    """A scored custom-format entry within a Sonarr quality profile."""

    __tablename__ = "sonarr_profile_format_items"

    profile_id: Mapped[int] = mapped_column(
        ForeignKey("sonarr_quality_profiles.id", ondelete="CASCADE"),
        primary_key=True,
    )
    custom_format_id: Mapped[int] = mapped_column(
        ForeignKey("sonarr_custom_formats.id", ondelete="CASCADE"),
        primary_key=True,
    )
    score: Mapped[int] = mapped_column(Integer, nullable=False)


class SonarrOverlayProfilePreference(Base):
    """User preference targets layered on top of a Sonarr quality profile."""

    __tablename__ = "sonarr_overlay_profile_preferences"

    profile_id: Mapped[int] = mapped_column(
        ForeignKey("sonarr_quality_profiles.id", ondelete="CASCADE"),
        primary_key=True,
    )
    meet_target: Mapped[str | None] = mapped_column(String(32), nullable=True)
    exceed_target: Mapped[str | None] = mapped_column(String(32), nullable=True)
    excluded_targets: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
