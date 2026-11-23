"""PipelineRun model — one row per pipeline execution.

A run is identified by a stable ``run_id`` (uuid4 hex) so its results,
event stream, and any feedback labels written against it survive a re-run
of the same movie (the working directory under ``data/runs/work/`` is
per-title and gets overwritten; this row + the archived JSON do not).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from marquee.database import Base


def _default_subject_key(context: Any) -> str:
    """Preserve canonical identity for legacy ORM call sites that omit the new field."""

    values = context.get_current_parameters()
    snapshot = values.get("subject_snapshot")
    if isinstance(snapshot, dict):
        display_id = snapshot.get("display_id")
        if isinstance(display_id, str) and 1 <= len(display_id) <= 200:
            return display_id
    media_type = values.get("media_type")
    identifier = (
        values.get(f"{media_type}_id") if media_type in {"movie", "series", "season"} else None
    )
    if isinstance(identifier, int) and identifier > 0:
        return f"{media_type}:{identifier}"
    return f"run:{values.get('run_id') or 'unresolved'}"


class PipelineRun(Base):
    """Provenance + status for a single poster-pipeline run."""

    __tablename__ = "pipeline_runs"

    __table_args__ = (
        UniqueConstraint("job_id", "subject_key", name="uq_pipeline_runs_job_subject"),
        CheckConstraint(
            "(media_type = 'movie' AND movie_id IS NOT NULL "
            "AND series_id IS NULL AND season_id IS NULL) OR "
            "(media_type = 'series' AND movie_id IS NULL "
            "AND series_id IS NOT NULL AND season_id IS NULL) OR "
            "(media_type = 'season' AND movie_id IS NULL AND season_id IS NOT NULL) OR "
            "(movie_id IS NULL AND series_id IS NULL AND season_id IS NULL)",
            name="ck_pipeline_runs_subject",
        ),
    )

    run_id: Mapped[str] = mapped_column(String(32), primary_key=True)

    movie_id: Mapped[int | None] = mapped_column(
        ForeignKey("movies.id", ondelete="SET NULL"),
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
        ForeignKey("series.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )

    season_id: Mapped[int | None] = mapped_column(
        ForeignKey("seasons.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )
    subject_snapshot: Mapped[dict] = mapped_column(
        JSON, nullable=False, default=dict, server_default="{}"
    )
    subject_key: Mapped[str] = mapped_column(
        String(200), nullable=False, default=_default_subject_key
    )

    # running | completed | flagged_manual | failed
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="running")

    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Which scorer head ranked this run — provenance for labels (weighted/learned).
    scorer_name: Mapped[str | None] = mapped_column(String(20), nullable=True)

    # JSON dump of the stage survivor counts (the outcome.counts dict).
    counts_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    # JSON dump of the per-stage wall-clock timings (the timings dict). Stored
    # alongside counts so the metrics endpoint can aggregate stage cost over
    # time without opening every archived run JSON.
    timings_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Total run wall-clock (seconds) — the headline number for throughput stats.
    duration_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Parent batch identity, shared by single-subject and grouped projections.
    batch_id: Mapped[str | None] = mapped_column(String(32), index=True, nullable=True)

    # Canonical job linkage (JMC6H H10). Every row is the product projection of
    # exactly one canonical poster-analysis attempt and immutable archive.
    job_id: Mapped[str] = mapped_column(
        ForeignKey("jobs.id", ondelete="CASCADE"), index=True, nullable=False
    )
    attempt_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("job_attempts.id", ondelete="CASCADE"), nullable=False
    )
    fence_token: Mapped[int] = mapped_column(BigInteger, nullable=False)
    selected_artifact_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("job_artifacts.id", ondelete="SET NULL"), nullable=True
    )
    archive_artifact_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("job_artifacts.id", ondelete="RESTRICT"), nullable=False
    )
    correlation_id: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)

    # orig_filename of the run's auto-pick ("1A") — denormalized at finalize so
    # the Review queue can show the chosen poster without opening every archive.
    # Null for runs predating this column or with no rankable candidate.
    auto_pick_filename: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Set once feedback is submitted for this run — the UI shows reviewed state.
    feedback_event_id: Mapped[str | None] = mapped_column(String(32), nullable=True)

    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    def __repr__(self) -> str:
        return (
            f"<PipelineRun(run_id={self.run_id!r}, movie_id={self.movie_id}, "
            f"status={self.status!r})>"
        )
