"""Blind evaluations: a batch of masked runs, and where the owner's poster landed in each.

An evaluation is one batch (its parent job); each of its masked runs writes one result row
for its movie when it ends. A result keeps its numbers when its run is deleted (a movie keeps
only its newest masked run): the run link is set to null.
"""

from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Double,
    ForeignKey,
    Identity,
    Integer,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from marquee.db.base import Base, TimestampMixin
from marquee.db.models.artwork import SUBJECT_MOVIE
from marquee.db.models.run import SHA256_CHECK

OUTCOME_RANKED = "ranked"
OUTCOME_GATED = "gated"
OUTCOME_NOT_OFFERED = "not_offered"
OUTCOME_FAILED = "failed"
OUTCOMES = (OUTCOME_RANKED, OUTCOME_GATED, OUTCOME_NOT_OFFERED, OUTCOME_FAILED)
SCORERS = ("weighted", "residual")
EXEMPLAR_SOURCES = ("seed", "feedback")


def _one_of(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class Evaluation(TimestampMixin, Base):
    """One blind evaluation: the batch of masked runs, and what it ran against.

    ``scorer`` is what the runs were expected to use when it was queued (each run records the
    scorer that ranked it). ``movies`` is how many movies were asked for and ``queued`` how
    many runs the batch queued.
    """

    __tablename__ = "evaluations"
    __table_args__ = (
        CheckConstraint(_one_of("scorer", SCORERS), name="scorer"),
        CheckConstraint(f"profile_sha256 {SHA256_CHECK}", name="profile_sha256"),
        CheckConstraint("movies >= 0 AND queued >= 0 AND queued <= movies", name="counts"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    job_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("jobs.id", ondelete="SET NULL"), unique=True
    )
    """The batch's parent job."""
    profile_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("taste_profiles.id"))
    profile_sha256: Mapped[str] = mapped_column(Text, nullable=False)
    residual_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("residual_trainings.id", ondelete="SET NULL")
    )
    scorer: Mapped[str] = mapped_column(Text, nullable=False)
    movies: Mapped[int] = mapped_column(Integer, nullable=False)
    queued: Mapped[int] = mapped_column(Integer, nullable=False)

    def __repr__(self) -> str:
        return f"<Evaluation id={self.id} job_id={self.job_id} queued={self.queued}>"


class EvaluationResult(TimestampMixin, Base):
    """Where the owner's poster for one movie landed in that movie's masked run.

    ``ranked``: the match was ranked (``rank`` of ``ranked_count``, and ``card`` of ``cards``,
    where a card is a design stack in the order the Review page shows them). ``gated``: the
    match was rejected (``stage`` and ``reason``). ``not_offered``: no candidate reached the
    match threshold (``best_cosine`` is the closest). ``failed``: the run did not complete
    (``reason``). The match is a candidate named by TMDB's file name.
    """

    __tablename__ = "evaluation_results"
    __table_args__ = (
        UniqueConstraint("evaluation_id", "movie_id"),
        CheckConstraint(f"subject_kind = '{SUBJECT_MOVIE}'", name="subject_kind"),
        CheckConstraint(_one_of("outcome", OUTCOMES), name="outcome"),
        CheckConstraint(_one_of("exemplar_source", EXEMPLAR_SOURCES), name="exemplar_source"),
        CheckConstraint(f"match_sha256 {SHA256_CHECK}", name="match_sha256"),
        CheckConstraint(
            f"outcome <> '{OUTCOME_RANKED}' OR (rank IS NOT NULL AND ranked_count IS NOT NULL "
            "AND card IS NOT NULL AND cards IS NOT NULL AND rank >= 1 AND ranked_count >= rank "
            "AND card >= 1 AND cards >= card AND pick_is_match IS NOT NULL "
            "AND same_design IS NOT NULL)",
            name="ranked",
        ),
        CheckConstraint(
            f"(match_file IS NOT NULL) = (outcome IN ('{OUTCOME_RANKED}', '{OUTCOME_GATED}'))",
            name="match",
        ),
        CheckConstraint(
            "match_cosine IS NULL OR (match_cosine >= -1.0001 AND match_cosine <= 1.0001)",
            name="match_cosine",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    evaluation_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("evaluations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    subject_kind: Mapped[str] = mapped_column(
        Text, nullable=False, default=SUBJECT_MOVIE, server_default=text(f"'{SUBJECT_MOVIE}'")
    )
    movie_id: Mapped[int] = mapped_column(Integer, ForeignKey("movies.id"), nullable=False)
    exemplar_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("exemplars.id"))
    exemplar_source: Mapped[str | None] = mapped_column(Text)
    run_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("poster_runs.id", ondelete="SET NULL"), index=True
    )
    outcome: Mapped[str] = mapped_column(Text, nullable=False)
    matched_by: Mapped[str | None] = mapped_column(Text)
    """``file`` (TMDB file name) or ``cosine``."""
    match_file: Mapped[str | None] = mapped_column(Text)
    match_sha256: Mapped[str | None] = mapped_column(Text)
    match_cosine: Mapped[float | None] = mapped_column(Double)
    best_cosine: Mapped[float | None] = mapped_column(Double)
    best_file: Mapped[str | None] = mapped_column(Text)
    """The candidate closest to the owner's poster, matched or not."""
    unembedded: Mapped[int | None] = mapped_column(Integer)
    """Candidates with no cached embedding (rejected before the style stage): not matchable."""
    rank: Mapped[int | None] = mapped_column(Integer)
    ranked_count: Mapped[int | None] = mapped_column(Integer)
    card: Mapped[int | None] = mapped_column(Integer)
    cards: Mapped[int | None] = mapped_column(Integer)
    pick_file: Mapped[str | None] = mapped_column(Text)
    pick_is_match: Mapped[bool | None] = mapped_column(Boolean)
    same_design: Mapped[bool | None] = mapped_column(Boolean)
    stage: Mapped[str | None] = mapped_column(Text)
    reason: Mapped[str | None] = mapped_column(Text)

    def __repr__(self) -> str:
        return (
            f"<EvaluationResult evaluation={self.evaluation_id} movie={self.movie_id} "
            f"{self.outcome}>"
        )
