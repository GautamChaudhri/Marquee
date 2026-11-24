"""Taste: the owner's feedback, the exemplars it makes, and the profiles built from them.

``preference_events`` is append-only: one row per act of feedback, keeping its own copy of
what was shown (run candidates go when their run does). ``exemplars`` are the posters the
taste profile is built from, each with a kept image; ``taste_profiles`` records every
profile file that was built or registered, and which one is active.
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
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from marquee.db.base import Base
from marquee.db.models.artwork import SUBJECT_MOVIE
from marquee.db.models.run import SHA256_CHECK

ACTION_APPROVAL = "approval"
ACTION_OVERRIDE = "override"
ACTION_RANK = "rank"
ACTION_REJECT = "reject"
ACTION_REVOKE = "revoke"
ACTIONS = (ACTION_APPROVAL, ACTION_OVERRIDE, ACTION_RANK, ACTION_REJECT, ACTION_REVOKE)
"""The names ``marquee.ml.residual.build_residual_pairs`` reads are the first three."""

POLARITY_POSITIVE = "positive"
POLARITY_NEGATIVE = "negative"
POLARITIES = (POLARITY_POSITIVE, POLARITY_NEGATIVE)
SOURCE_SEED = "seed"
SOURCE_FEEDBACK = "feedback"
EXEMPLAR_SOURCES = (SOURCE_SEED, SOURCE_FEEDBACK)
STATE_ACTIVE = "active"
STATE_SUPERSEDED = "superseded"
STATE_REVOKED = "revoked"
EXEMPLAR_STATES = (STATE_ACTIVE, STATE_SUPERSEDED, STATE_REVOKED)
PROFILE_SOURCE_SEED = "seed"
PROFILE_SOURCE_REBUILD = "rebuild"
PROFILE_SOURCES = (PROFILE_SOURCE_SEED, PROFILE_SOURCE_REBUILD)

MAX_EXPOSED = 100
"""An event keeps at most this many shown candidates (about 25 KB of features)."""
MAX_IDEMPOTENCY_KEY = 160


def _one_of(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def plain_name(column: str) -> str:
    """A file name, never a path."""
    return f"{column} <> '' AND {column} NOT IN ('.', '..') AND strpos({column}, '/') = 0"


class PreferenceEvent(Base):
    """One act of feedback on a run: what was shown, in what order, and what the owner did.

    Rows are inserted, never updated or deleted. A candidate is named by TMDB's file name.
    ``exposed`` holds one object per candidate shown (``candidate_id``, ``sha256``,
    ``baseline_rank``, ``baseline_score``, ``stack``, ``normalized_features``) and
    ``context`` the owner's ``order``, ``hated``, and ``selected_candidate``. ``blind`` is
    whether the run's profile had no positive exemplar of the movie (null: not known).
    """

    __tablename__ = "preference_events"
    __table_args__ = (
        CheckConstraint(f"subject_kind = '{SUBJECT_MOVIE}'", name="subject_kind"),
        CheckConstraint(_one_of("action", ACTIONS), name="action"),
        CheckConstraint(
            f"(action = '{ACTION_REVOKE}') = (revokes_event_id IS NOT NULL)", name="revokes"
        ),
        CheckConstraint(f"chosen_sha256 {SHA256_CHECK}", name="chosen_sha256"),
        CheckConstraint(f"profile_sha256 {SHA256_CHECK}", name="profile_sha256"),
        CheckConstraint(plain_name("chosen_source_file"), name="chosen_source_file"),
        CheckConstraint(plain_name("pick_source_file"), name="pick_source_file"),
        CheckConstraint(
            f"jsonb_typeof(exposed) = 'array' AND jsonb_array_length(exposed) <= {MAX_EXPOSED}",
            name="exposed",
        ),
        CheckConstraint("jsonb_typeof(context) = 'object'", name="context"),
        CheckConstraint(
            f"length(idempotency_key) BETWEEN 1 AND {MAX_IDEMPOTENCY_KEY}",
            name="idempotency_key",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    subject_kind: Mapped[str] = mapped_column(
        Text, nullable=False, default=SUBJECT_MOVIE, server_default=text(f"'{SUBJECT_MOVIE}'")
    )
    # No cascade: movies are retired, never deleted.
    movie_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("movies.id"), nullable=False, index=True
    )
    run_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("poster_runs.id", ondelete="SET NULL"), index=True
    )
    action: Mapped[str] = mapped_column(Text, nullable=False)
    chosen_source_file: Mapped[str | None] = mapped_column(Text)
    chosen_sha256: Mapped[str | None] = mapped_column(Text)
    pick_source_file: Mapped[str | None] = mapped_column(Text)
    profile_sha256: Mapped[str | None] = mapped_column(Text)
    scorer_name: Mapped[str | None] = mapped_column(Text)
    blind: Mapped[bool | None] = mapped_column(Boolean)
    exposed: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    context: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    revokes_event_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("preference_events.id"), unique=True
    )
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    def __repr__(self) -> str:
        return f"<PreferenceEvent id={self.id} movie_id={self.movie_id} action={self.action!r}>"


class Exemplar(Base):
    """A poster the taste profile is built from: one the owner likes or dislikes.

    ``file_name`` is the kept image's name inside the exemplar store (``<sha256>.<ext>``),
    never a path. ``staged_name`` is the name the trainer sees, which it reads the title
    from; positives only. A seed exemplar has no event and no TMDB file; a feedback exemplar
    has both. A movie has at most one active positive.
    """

    __tablename__ = "exemplars"
    __table_args__ = (
        CheckConstraint(f"subject_kind = '{SUBJECT_MOVIE}'", name="subject_kind"),
        CheckConstraint(_one_of("polarity", POLARITIES), name="polarity"),
        CheckConstraint("weight > 0 AND weight <= 1", name="weight"),
        CheckConstraint(_one_of("source", EXEMPLAR_SOURCES), name="source"),
        CheckConstraint(_one_of("state", EXEMPLAR_STATES), name="state"),
        CheckConstraint(f"(source = '{SOURCE_FEEDBACK}') = (event_id IS NOT NULL)", name="event"),
        CheckConstraint(
            f"(source = '{SOURCE_FEEDBACK}') = (source_file IS NOT NULL)", name="feedback_file"
        ),
        CheckConstraint(
            f"(polarity = '{POLARITY_POSITIVE}') = (staged_name IS NOT NULL)", name="staged"
        ),
        CheckConstraint(f"(state = '{STATE_ACTIVE}') = (ended_at IS NULL)", name="ended"),
        CheckConstraint(f"sha256 {SHA256_CHECK}", name="sha256"),
        CheckConstraint(plain_name("file_name"), name="file_name"),
        CheckConstraint(plain_name("staged_name"), name="staged_name"),
        CheckConstraint(plain_name("source_file"), name="source_file"),
        Index(
            "ix_exemplars_active_positive",
            "movie_id",
            unique=True,
            postgresql_where=text(f"state = '{STATE_ACTIVE}' AND polarity = '{POLARITY_POSITIVE}'"),
        ),
        Index(
            "ix_exemplars_active_content",
            "movie_id",
            "polarity",
            "sha256",
            unique=True,
            postgresql_where=text(f"state = '{STATE_ACTIVE}'"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    subject_kind: Mapped[str] = mapped_column(
        Text, nullable=False, default=SUBJECT_MOVIE, server_default=text(f"'{SUBJECT_MOVIE}'")
    )
    movie_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("movies.id"), nullable=False, index=True
    )
    polarity: Mapped[str] = mapped_column(Text, nullable=False)
    weight: Mapped[float] = mapped_column(Double, nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    event_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("preference_events.id"), index=True
    )
    state: Mapped[str] = mapped_column(Text, nullable=False)
    sha256: Mapped[str] = mapped_column(Text, nullable=False)
    file_name: Mapped[str] = mapped_column(Text, nullable=False)
    staged_name: Mapped[str | None] = mapped_column(Text)
    source_file: Mapped[str | None] = mapped_column(Text)
    replaced_exemplar_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("exemplars.id"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    def __repr__(self) -> str:
        return (
            f"<Exemplar id={self.id} movie_id={self.movie_id} {self.polarity} {self.state} "
            f"weight={self.weight}>"
        )


class TasteProfile(Base):
    """A taste profile file Marquee built or registered, and whether it is the active one.

    ``file_name`` is the kept copy's name inside ``data/ml/profiles/``, never a path.
    ``revision`` is the exemplar set it was built from; ``positive_movie_ids`` (sorted) says
    which movies had a positive exemplar in it, which is what makes a run blind or not.
    """

    __tablename__ = "taste_profiles"
    __table_args__ = (
        CheckConstraint(f"sha256 {SHA256_CHECK}", name="sha256"),
        CheckConstraint(f"revision {SHA256_CHECK}", name="revision"),
        CheckConstraint(_one_of("source", PROFILE_SOURCES), name="source"),
        CheckConstraint("positives >= 0 AND negatives >= 0", name="counts"),
        CheckConstraint("jsonb_typeof(positive_movie_ids) = 'array'", name="positive_movie_ids"),
        CheckConstraint(plain_name("file_name"), name="file_name"),
        # At most one active profile.
        Index(
            "ix_taste_profiles_active", "is_active", unique=True, postgresql_where=text("is_active")
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    sha256: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    revision: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    positives: Mapped[int] = mapped_column(Integer, nullable=False)
    negatives: Mapped[int] = mapped_column(Integer, nullable=False)
    positive_movie_ids: Mapped[list[int]] = mapped_column(JSONB, nullable=False)
    file_name: Mapped[str] = mapped_column(Text, nullable=False)
    job_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("jobs.id", ondelete="SET NULL")
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    def __repr__(self) -> str:
        return f"<TasteProfile id={self.id} {self.source} active={self.is_active}>"
