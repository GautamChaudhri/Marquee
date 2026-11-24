"""Pipeline runs: one row per run of one movie, and one per candidate it considered.

The run archive (``pipeline_run.json`` in the run's folder) stays the full record of a run;
these tables hold what is queried: the run's state and counts, and each candidate's fate,
rank, score, and stack. Feature values stay in the archive.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Double,
    ForeignKey,
    Identity,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql.elements import ColumnElement

from marquee.db.base import Base, TimestampMixin
from marquee.db.models.artwork import SUBJECT_MOVIE

RUN_STATES = ("running", "completed", "failed", "cancelled")
RUN_ACTIVE = "running"
RUN_COMPLETED = "completed"
RUN_FAILED = "failed"
RUN_CANCELLED = "cancelled"

SHA256_CHECK = "~ '^[0-9a-f]{64}$'"


def _one_of(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class PosterRun(TimestampMixin, Base):
    """One run of the pipeline over one movie's candidates.

    ``run_folder`` is the folder's name inside the runs directory, never a path. A job has
    at most one run: a second attempt of the job reuses its row.
    """

    __tablename__ = "poster_runs"
    __table_args__ = (
        CheckConstraint(f"subject_kind = '{SUBJECT_MOVIE}'", name="subject_kind"),
        CheckConstraint(_one_of("state", RUN_STATES), name="state"),
        CheckConstraint(
            "run_folder <> '' AND run_folder NOT IN ('.', '..') AND strpos(run_folder, '/') = 0",
            name="run_folder",
        ),
        CheckConstraint(f"profile_sha256 {SHA256_CHECK}", name="profile_sha256"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    subject_kind: Mapped[str] = mapped_column(
        Text, nullable=False, default=SUBJECT_MOVIE, server_default=text(f"'{SUBJECT_MOVIE}'")
    )
    # No cascade: movies are retired, never deleted.
    movie_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("movies.id"), nullable=False, index=True
    )
    job_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("jobs.id", ondelete="SET NULL"), nullable=True, unique=True
    )
    state: Mapped[str] = mapped_column(Text, nullable=False)
    run_folder: Mapped[str] = mapped_column(Text, nullable=False)
    engine_run_id: Mapped[str | None] = mapped_column(Text)
    engine_status: Mapped[str | None] = mapped_column(Text)
    scorer_name: Mapped[str | None] = mapped_column(Text)
    ai_model: Mapped[str | None] = mapped_column(Text)
    profile_sha256: Mapped[str | None] = mapped_column(Text)
    counts: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    stages: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB(none_as_null=True))
    candidate_count: Mapped[int | None] = mapped_column(Integer)
    ranked_count: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    masked: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    """A run against the active profile with the movie's own exemplars left out (a blind
    evaluation's). Never part of the product loop: see :func:`in_product_loop`."""
    residual_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("residual_trainings.id", ondelete="SET NULL")
    )
    """The residual training whose artifact ranked the run, when ``scorer_name`` is
    ``residual``."""

    def __repr__(self) -> str:
        return f"<PosterRun id={self.id} movie_id={self.movie_id} state={self.state!r}>"


def in_product_loop() -> ColumnElement[bool]:
    """The runs of the product loop: review, feedback, deploys, the deploy policy, and a
    movie's latest run on the Library pages. Every run but a masked one.

    Every query that means "the movie's latest run" uses this condition, so a masked run
    (which is newer than the run the owner reviews) never takes its place.
    """
    return PosterRun.masked.is_(False)


class RunCandidate(TimestampMixin, Base):
    """One candidate a run considered: where it stopped, and its rank, score, and stack.

    ``source_file`` is TMDB's file name. ``sha256`` is the hash of the 500 px file the run
    downloaded, or null when it was never downloaded. One candidate per run is the pick.
    """

    __tablename__ = "run_candidates"
    __table_args__ = (
        UniqueConstraint("run_id", "source_file"),
        CheckConstraint(f"sha256 {SHA256_CHECK}", name="sha256"),
        CheckConstraint(
            "source_file <> '' AND source_file NOT IN ('.', '..') AND strpos(source_file, '/') = 0",
            name="source_file",
        ),
        # One pick per run.
        Index("ix_run_candidates_pick", "run_id", unique=True, postgresql_where=text("is_pick")),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    run_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("poster_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    source_file: Mapped[str] = mapped_column(Text, nullable=False)
    sha256: Mapped[str | None] = mapped_column(Text)
    stage_reached: Mapped[str | None] = mapped_column(Text)
    gate_decision: Mapped[str | None] = mapped_column(Text)
    rejection_reason: Mapped[str | None] = mapped_column(Text)
    rank: Mapped[int | None] = mapped_column(Integer)
    final_score: Mapped[float | None] = mapped_column(Double)
    stack_rank: Mapped[int | None] = mapped_column(Integer)
    stack_label: Mapped[str | None] = mapped_column(Text)
    is_pick: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )

    def __repr__(self) -> str:
        return f"<RunCandidate id={self.id} run_id={self.run_id} rank={self.rank}>"
