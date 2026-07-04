"""PipelineRun model — one row per pipeline execution.

A run is identified by a stable ``run_id`` (uuid4 hex) so its results,
event stream, and any feedback labels written against it survive a re-run
of the same movie (the working directory under ``data/runs/work/`` is
per-title and gets overwritten; this row + the archived JSON do not).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Float, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from marquee.database import Base


class PipelineRun(Base):
    """Provenance + status for a single poster-pipeline run."""

    __tablename__ = "pipeline_runs"

    __table_args__ = (
        CheckConstraint(
            "(media_type = 'movie'  AND movie_id  IS NOT NULL) OR "
            "(media_type = 'series' AND series_id IS NOT NULL) OR "
            "(media_type = 'season' AND season_id IS NOT NULL)",
            name="ck_pipeline_runs_subject",
        ),
    )

    run_id: Mapped[str] = mapped_column(String(32), primary_key=True)

    movie_id: Mapped[int | None] = mapped_column(
        ForeignKey("movies.id", ondelete="CASCADE"),
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
        ForeignKey("series.id", ondelete="CASCADE"),
        index=True,
        nullable=True,
    )

    season_id: Mapped[int | None] = mapped_column(
        ForeignKey("seasons.id", ondelete="CASCADE"),
        index=True,
        nullable=True,
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

    # When this run was produced by a cross-movie batch, the batch's job id —
    # lets the UI group every movie that moved through one batch together.
    batch_id: Mapped[str | None] = mapped_column(String(32), index=True, nullable=True)

    # data/runs/archive/{run_id}.json — survives re-runs of the same movie.
    archive_path: Mapped[str | None] = mapped_column(Text, nullable=True)

    # orig_filename of the run's auto-pick ("1A") — denormalized at finalize so
    # the Review queue can show the chosen poster without opening every archive.
    # Null for runs predating this column or with no rankable candidate.
    auto_pick_filename: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Working directory under data/runs/work/<title>/.
    output_dir: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Set once feedback is submitted for this run — the UI shows reviewed state.
    feedback_event_id: Mapped[str | None] = mapped_column(String(32), nullable=True)

    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    def __repr__(self) -> str:
        return (
            f"<PipelineRun(run_id={self.run_id!r}, movie_id={self.movie_id}, "
            f"status={self.status!r})>"
        )
