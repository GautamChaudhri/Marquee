"""Exemplar records: the posters the taste profile is built from, and their kept images.

Every exemplar keeps a copy of its image in the store, ``data/ml/exemplars/<sha256>.<ext>``,
written under a temporary name, renamed, then read back and checked, the way a poster backup
is. A profile is rebuilt from those copies alone, so it never depends on a run folder or a
media folder still holding the file.

The rules (decisions 3 and 8 of the M5b brief):

- a movie has at most one active positive; a new one supersedes it and names it as the
  exemplar it replaced;
- a movie has at most :data:`MAX_ACTIVE_NEGATIVES` active negatives; one more ends the
  oldest;
- revoking an event revokes the exemplars it created, and when its positive was still
  active, the exemplar that positive replaced becomes active again if it is still only
  superseded.

:func:`revision` is a hash of the active set: what a profile built now would be built from.
Nothing here commits; the caller owns the transaction.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import stat
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from marquee.config import settings
from marquee.db.models import Exemplar, Movie
from marquee.db.models.taste import (
    POLARITY_NEGATIVE,
    POLARITY_POSITIVE,
    SOURCE_FEEDBACK,
    STATE_ACTIVE,
    STATE_REVOKED,
    STATE_SUPERSEDED,
)

logger = logging.getLogger(__name__)

MAX_ACTIVE_NEGATIVES = 8
APPROVAL_WEIGHT = 0.75
CHOICE_WEIGHT = 1.0
NEGATIVE_WEIGHT = 1.0
KEPT_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".webp"})
_KEPT = re.compile(r"[0-9a-f]{64}\.(png|jpg|jpeg|webp)")


class ExemplarError(Exception):
    """An exemplar's image is missing, cannot be kept, or is not the bytes its record names."""


# ── The kept images ───────────────────────────────────────────────────────


def sha256_of(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def kept_name(sha256: str, suffix: str) -> str:
    """The kept image's name: its hash and its original suffix."""
    name = f"{sha256}{suffix.lower()}"
    if not _KEPT.fullmatch(name):
        raise ExemplarError(f"no kept name for hash {sha256!r} and suffix {suffix!r}")
    return name


def kept_path(file_name: str, store: Path | None = None) -> Path:
    if not _KEPT.fullmatch(file_name):
        raise ExemplarError(f"{file_name!r} is not a kept image's name")
    return (store or settings.exemplars_dir) / file_name


def read_kept(file_name: str, sha256: str, store: Path | None = None) -> bytes:
    """The kept image's bytes, proven to be the ones whose hash is ``sha256``. Blocking.

    Checked by hash, never by decoding: the engine makes Pillow accept a cut-off image.
    """
    path = kept_path(file_name, store)
    try:
        if not stat.S_ISREG(os.lstat(path).st_mode):
            raise ExemplarError(f"kept image {file_name} is not a regular file")
        data = path.read_bytes()
    except FileNotFoundError as exc:
        raise ExemplarError(f"kept image {file_name} is missing") from exc
    actual = sha256_of(data)
    if actual != sha256 or not file_name.startswith(sha256):
        raise ExemplarError(f"kept image {file_name} holds bytes with hash {actual}")
    return data


def keep(data: bytes, suffix: str, store: Path | None = None) -> str:
    """Keep ``data`` in the store and return its name. Blocking.

    Written under a temporary name, synced, renamed, then read back and checked. A file
    already there with the right hash is kept; one with the wrong hash is an error.
    """
    base = store or settings.exemplars_dir
    digest = sha256_of(data)
    name = kept_name(digest, suffix)
    target = base / name
    if target.exists() or target.is_symlink():
        read_kept(name, digest, base)
        return name
    base.mkdir(parents=True, exist_ok=True)
    temporary = base / f".{name}.part"
    try:
        with temporary.open("wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    read_kept(name, digest, base)
    return name


def keep_file(path: Path, expected_sha256: str, store: Path | None = None) -> str:
    """Keep a copy of ``path``, which must hold the bytes whose hash is ``expected_sha256``."""
    try:
        if not stat.S_ISREG(os.lstat(path).st_mode):
            raise ExemplarError(f"{path.name} is not a regular file")
        data = path.read_bytes()
    except FileNotFoundError as exc:
        raise ExemplarError(f"{path.name} is missing") from exc
    if sha256_of(data) != expected_sha256:
        raise ExemplarError(f"{path.name} no longer holds the bytes its record names")
    return keep(data, path.suffix, store)


def staged_name_for(title: str, year: int | None, suffix: str) -> str:
    """The name the trainer sees for a feedback positive: ``<title> (<year>)<suffix>``.

    The trainer reads the title for OCR from it, so it is the title from the movies table,
    with ``/`` (which no file name may hold) replaced by a space.
    """
    name = " ".join(title.replace("/", " ").split()) or "Untitled"
    if year:
        name = f"{name} ({year})"
    return f"{name}{suffix.lower()}"


# ── The rules ─────────────────────────────────────────────────────────────


@dataclass
class Changes:
    """What one act of feedback did to the exemplars."""

    created: list[Exemplar] = field(default_factory=list)
    ended: list[Exemplar] = field(default_factory=list)
    reactivated: list[Exemplar] = field(default_factory=list)


async def lock_movie(session: AsyncSession, movie_id: int) -> Movie | None:
    """Lock the movie's row, so two acts of feedback on one movie happen one after the other."""
    return await session.scalar(select(Movie).where(Movie.id == movie_id).with_for_update())


async def active_positive(session: AsyncSession, movie_id: int) -> Exemplar | None:
    return await session.scalar(
        select(Exemplar).where(
            Exemplar.movie_id == movie_id,
            Exemplar.polarity == POLARITY_POSITIVE,
            Exemplar.state == STATE_ACTIVE,
        )
    )


async def active_negatives(session: AsyncSession, movie_id: int) -> list[Exemplar]:
    """The movie's active negatives, oldest first."""
    rows = await session.scalars(
        select(Exemplar)
        .where(
            Exemplar.movie_id == movie_id,
            Exemplar.polarity == POLARITY_NEGATIVE,
            Exemplar.state == STATE_ACTIVE,
        )
        .order_by(Exemplar.created_at, Exemplar.id)
    )
    return list(rows)


def _end(row: Exemplar, state: str, now: datetime) -> None:
    row.state = state
    row.ended_at = now


async def set_positive(
    session: AsyncSession,
    changes: Changes,
    *,
    movie_id: int,
    weight: float,
    sha256: str,
    file_name: str,
    staged_name: str,
    event_id: int,
    source_file: str,
    now: datetime,
) -> Exemplar:
    """The owner's newest choice becomes the movie's active positive; the one before it, if
    any, is superseded and named as the one it replaced."""
    old = await active_positive(session, movie_id)
    if old is not None:
        _end(old, STATE_SUPERSEDED, now)
        changes.ended.append(old)
        await session.flush()
    row = Exemplar(
        movie_id=movie_id,
        polarity=POLARITY_POSITIVE,
        weight=weight,
        source=SOURCE_FEEDBACK,
        event_id=event_id,
        state=STATE_ACTIVE,
        sha256=sha256,
        file_name=file_name,
        staged_name=staged_name,
        source_file=source_file,
        replaced_exemplar_id=old.id if old is not None else None,
        created_at=now,
    )
    session.add(row)
    await session.flush()
    changes.created.append(row)
    return row


async def add_negative(
    session: AsyncSession,
    changes: Changes,
    *,
    movie_id: int,
    sha256: str,
    file_name: str,
    event_id: int,
    source_file: str,
    now: datetime,
) -> Exemplar | None:
    """A disliked candidate becomes an active negative of the movie, unless that image
    already is one. Past :data:`MAX_ACTIVE_NEGATIVES`, the oldest ends."""
    negatives = await active_negatives(session, movie_id)
    if any(row.sha256 == sha256 for row in negatives):
        return None
    row = Exemplar(
        movie_id=movie_id,
        polarity=POLARITY_NEGATIVE,
        weight=NEGATIVE_WEIGHT,
        source=SOURCE_FEEDBACK,
        event_id=event_id,
        state=STATE_ACTIVE,
        sha256=sha256,
        file_name=file_name,
        source_file=source_file,
        created_at=now,
    )
    session.add(row)
    await session.flush()
    changes.created.append(row)
    for oldest in negatives[: max(0, len(negatives) + 1 - MAX_ACTIVE_NEGATIVES)]:
        _end(oldest, STATE_SUPERSEDED, now)
        changes.ended.append(oldest)
    return row


async def end_positive_with_image(
    session: AsyncSession, changes: Changes, *, movie_id: int, sha256: str, now: datetime
) -> Exemplar | None:
    """A hated candidate whose bytes are the movie's active positive ends that positive."""
    row = await active_positive(session, movie_id)
    if row is None or row.sha256 != sha256:
        return None
    _end(row, STATE_SUPERSEDED, now)
    changes.ended.append(row)
    await session.flush()
    return row


async def revoke_event(
    session: AsyncSession, changes: Changes, *, event_id: int, now: datetime
) -> None:
    """Revoke the exemplars an event created; bring back what its active positive replaced.

    The replaced exemplar comes back only if it is still superseded (not revoked by an event
    of its own) and the movie has no other active positive.
    """
    rows = list(
        await session.scalars(
            select(Exemplar).where(Exemplar.event_id == event_id).order_by(Exemplar.id)
        )
    )
    replaced_id = next(
        (
            row.replaced_exemplar_id
            for row in rows
            if row.polarity == POLARITY_POSITIVE and row.state == STATE_ACTIVE
        ),
        None,
    )
    movie_id = rows[0].movie_id if rows else None
    for row in rows:
        if row.state == STATE_REVOKED:
            continue
        row.state = STATE_REVOKED
        row.ended_at = row.ended_at or now
        changes.ended.append(row)
    await session.flush()
    if replaced_id is None or movie_id is None:
        return
    replaced = await session.get(Exemplar, replaced_id, with_for_update=True)
    if (
        replaced is not None
        and replaced.state == STATE_SUPERSEDED
        and await active_positive(session, movie_id) is None
    ):
        replaced.state = STATE_ACTIVE
        replaced.ended_at = None
        changes.reactivated.append(replaced)
        await session.flush()


# ── The active set ────────────────────────────────────────────────────────


async def active(session: AsyncSession) -> list[Exemplar]:
    """Every active exemplar, positives first, then by id."""
    rows = await session.scalars(
        select(Exemplar)
        .where(Exemplar.state == STATE_ACTIVE)
        .order_by(Exemplar.polarity.desc(), Exemplar.id)
    )
    return list(rows)


def revision_of(rows: Iterable[Exemplar]) -> str:
    """SHA-256 over the active set: polarity, image hash, weight to six places, and staged
    name, sorted. Two sets that would build the same profile have the same revision."""
    items = sorted(
        (row.polarity, row.sha256, f"{row.weight:.6f}", row.staged_name or "")
        for row in rows
        if row.state == STATE_ACTIVE
    )
    payload = json.dumps(items, separators=(",", ":"), ensure_ascii=True).encode()
    return hashlib.sha256(payload).hexdigest()


async def revision(session: AsyncSession) -> str:
    return revision_of(await active(session))
