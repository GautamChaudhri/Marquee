"""Every write Marquee made to a movie folder: an append-only audit table."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Integer,
    Text,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from marquee.db.base import Base
from marquee.db.models.artwork import ARTWORK_POSTER, ORIGINS, SUBJECT_MOVIE
from marquee.db.models.run import SHA256_CHECK

ACTION_DEPLOY = "deploy"
ACTION_RESTORE = "restore"
ACTIONS = (ACTION_DEPLOY, ACTION_RESTORE)
INITIATED_BY_POLICY = "policy"
INITIATED_BY_OWNER = "owner"
INITIATORS = (INITIATED_BY_POLICY, INITIATED_BY_OWNER)


def _one_of(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class Deployment(Base):
    """One write to a movie folder: a deploy of a run's candidate, or a restore.

    Rows are inserted, never updated or deleted, so the table has ``created_at`` only. The
    row is committed together with the ``artwork`` change, after the written file has been
    verified. ``backup_file`` is relative to the backups directory, never a path; it is null
    when there was nothing to back up. ``new_sha256`` and ``new_size_bytes`` are null when the
    write removed the poster (a restore of "no poster").
    """

    __tablename__ = "deployments"
    __table_args__ = (
        CheckConstraint(f"subject_kind = '{SUBJECT_MOVIE}'", name="subject_kind"),
        CheckConstraint(f"artwork_kind = '{ARTWORK_POSTER}'", name="artwork_kind"),
        CheckConstraint(_one_of("action", ACTIONS), name="action"),
        CheckConstraint(_one_of("initiated_by", INITIATORS), name="initiated_by"),
        CheckConstraint(_one_of("previous_origin", ORIGINS), name="previous_origin"),
        CheckConstraint(f"new_sha256 {SHA256_CHECK}", name="new_sha256"),
        CheckConstraint(f"previous_sha256 {SHA256_CHECK}", name="previous_sha256"),
        CheckConstraint("(new_sha256 IS NULL) = (new_size_bytes IS NULL)", name="new_file"),
        CheckConstraint(
            "file_name <> '' AND file_name NOT IN ('.', '..') AND strpos(file_name, '/') = 0",
            name="file_name",
        ),
        # Relative to the backups directory: no leading slash, no parent step.
        CheckConstraint(
            "backup_file <> '' AND left(backup_file, 1) <> '/' "
            "AND backup_file !~ '(^|/)\\.\\.(/|$)'",
            name="backup_file",
        ),
        # A restore names the row it undoes; a deploy does not.
        CheckConstraint(
            f"(action = '{ACTION_RESTORE}') = (restores_deployment_id IS NOT NULL)",
            name="restores",
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
    artwork_kind: Mapped[str] = mapped_column(Text, nullable=False)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    initiated_by: Mapped[str] = mapped_column(Text, nullable=False)
    run_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("poster_runs.id", ondelete="SET NULL")
    )
    candidate_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("run_candidates.id", ondelete="SET NULL")
    )
    source_file: Mapped[str | None] = mapped_column(Text)
    # A job writes at most one row, however often it runs.
    job_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("jobs.id", ondelete="SET NULL"), unique=True
    )
    file_name: Mapped[str] = mapped_column(Text, nullable=False)
    new_sha256: Mapped[str | None] = mapped_column(Text)
    new_size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    previous_sha256: Mapped[str | None] = mapped_column(Text)
    previous_origin: Mapped[str | None] = mapped_column(Text)
    backup_file: Mapped[str | None] = mapped_column(Text)
    restores_deployment_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("deployments.id")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    def __repr__(self) -> str:
        return (
            f"<Deployment id={self.id} movie_id={self.movie_id} action={self.action!r} "
            f"file_name={self.file_name!r}>"
        )
