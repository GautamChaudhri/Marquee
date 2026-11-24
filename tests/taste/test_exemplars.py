"""Exemplar records: the kept-image store, the rules, and the revision."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from marquee.config import settings
from marquee.db.models import Exemplar, Movie, PreferenceEvent
from marquee.taste import exemplars

SHA = {n: f"{n:x}" * 64 for n in range(1, 15)}


# ── The store ─────────────────────────────────────────────────────────────


def test_keep_writes_once_and_reads_back(tmp_path: Path) -> None:
    name = exemplars.keep(b"poster", ".JPG", tmp_path)
    digest = exemplars.sha256_of(b"poster")
    assert name == f"{digest}.jpg"
    assert (tmp_path / name).read_bytes() == b"poster"
    assert exemplars.keep(b"poster", ".jpg", tmp_path) == name  # kept, not written again
    assert exemplars.read_kept(name, digest, tmp_path) == b"poster"
    assert not list(tmp_path.glob(".*"))


def test_a_kept_image_is_checked_by_its_hash(tmp_path: Path) -> None:
    name = exemplars.keep(b"poster", ".png", tmp_path)
    digest = exemplars.sha256_of(b"poster")
    (tmp_path / name).write_bytes(b"other")
    with pytest.raises(exemplars.ExemplarError, match="holds bytes with hash"):
        exemplars.read_kept(name, digest, tmp_path)
    with pytest.raises(exemplars.ExemplarError, match="holds bytes with hash"):
        exemplars.keep(b"poster", ".png", tmp_path)  # never overwritten
    (tmp_path / name).unlink()
    with pytest.raises(exemplars.ExemplarError, match="missing"):
        exemplars.read_kept(name, digest, tmp_path)
    (tmp_path / name).symlink_to(tmp_path / "elsewhere")
    with pytest.raises(exemplars.ExemplarError):
        exemplars.read_kept(name, digest, tmp_path)


@pytest.mark.parametrize("name", ["../x.png", "a.png", f"{'a' * 64}.gif", f"{'a' * 64}"])
def test_only_kept_names_are_paths(name: str, tmp_path: Path) -> None:
    with pytest.raises(exemplars.ExemplarError):
        exemplars.kept_path(name, tmp_path)


def test_keep_file_needs_the_bytes_its_record_names(tmp_path: Path) -> None:
    source = tmp_path / "candidate.jpg"
    source.write_bytes(b"poster")
    store = tmp_path / "store"
    assert exemplars.keep_file(source, exemplars.sha256_of(b"poster"), store).endswith(".jpg")
    with pytest.raises(exemplars.ExemplarError, match="no longer holds"):
        exemplars.keep_file(source, "0" * 64, store)
    with pytest.raises(exemplars.ExemplarError, match="missing"):
        exemplars.keep_file(tmp_path / "gone.jpg", "0" * 64, store)


def test_the_store_lives_under_the_data_directory() -> None:
    assert exemplars.kept_path(f"{'a' * 64}.png") == settings.ml_dir / "exemplars" / (
        f"{'a' * 64}.png"
    )


@pytest.mark.parametrize(
    ("title", "year", "suffix", "name"),
    [
        ("Die Hard", 1988, ".jpg", "Die Hard (1988).jpg"),
        ("Alien: Covenant", 2017, ".JPG", "Alien: Covenant (2017).jpg"),
        ("Face/Off", 1997, ".jpg", "Face Off (1997).jpg"),
        ("Untitled", None, ".png", "Untitled.png"),
        ("/", 2000, ".jpg", "Untitled (2000).jpg"),
    ],
)
def test_staged_names_come_from_the_title(
    title: str, year: int | None, suffix: str, name: str
) -> None:
    assert exemplars.staged_name_for(title, year, suffix) == name


# ── The rules ─────────────────────────────────────────────────────────────

NOW = datetime(2026, 11, 24, 12, 0, tzinfo=UTC)


async def _movie(session: AsyncSession, radarr_id: int = 1) -> int:
    movie = Movie(
        radarr_id=radarr_id,
        title=f"Movie {radarr_id}",
        sort_title=f"movie {radarr_id}",
        radarr_path=f"/radarr/Movie {radarr_id}",
        has_file=True,
        last_seen_at=NOW,
    )
    session.add(movie)
    await session.flush()
    return movie.id


async def _event(session: AsyncSession, movie_id: int, key: str) -> int:
    event = PreferenceEvent(
        movie_id=movie_id, action="rank", exposed=[], context={}, idempotency_key=key
    )
    session.add(event)
    await session.flush()
    return event.id


def _seed(movie_id: int, sha: str) -> Exemplar:
    return Exemplar(
        movie_id=movie_id,
        polarity="positive",
        weight=1.0,
        source="seed",
        state="active",
        sha256=sha,
        file_name=f"{sha}.png",
        staged_name=f"Movie {movie_id}.png",
        created_at=NOW,
    )


async def _positive(
    session: AsyncSession, movie_id: int, event_id: int, sha: str, changes: exemplars.Changes
) -> Exemplar:
    return await exemplars.set_positive(
        session,
        changes,
        movie_id=movie_id,
        weight=1.0,
        sha256=sha,
        file_name=f"{sha}.jpg",
        staged_name=f"Movie {movie_id}.jpg",
        event_id=event_id,
        source_file=f"{sha[:8]}.jpg",
        now=NOW,
    )


async def _negative(
    session: AsyncSession,
    movie_id: int,
    event_id: int,
    sha: str,
    changes: exemplars.Changes,
    now: datetime = NOW,
) -> Exemplar | None:
    return await exemplars.add_negative(
        session,
        changes,
        movie_id=movie_id,
        sha256=sha,
        file_name=f"{sha}.jpg",
        event_id=event_id,
        source_file=f"{sha[:8]}.jpg",
        now=now,
    )


@pytest.mark.db
async def test_a_new_positive_supersedes_the_one_before(db_session: AsyncSession) -> None:
    movie_id = await _movie(db_session)
    seed = _seed(movie_id, SHA[1])
    db_session.add(seed)
    await db_session.flush()
    changes = exemplars.Changes()

    first = await _positive(
        db_session, movie_id, await _event(db_session, movie_id, "a"), SHA[2], changes
    )
    second = await _positive(
        db_session, movie_id, await _event(db_session, movie_id, "b"), SHA[3], changes
    )

    assert (seed.state, seed.ended_at) == ("superseded", NOW)
    assert first.replaced_exemplar_id == seed.id and first.state == "superseded"
    assert second.replaced_exemplar_id == first.id and second.state == "active"
    assert changes.created == [first, second] and changes.ended == [seed, first]
    assert await exemplars.active_positive(db_session, movie_id) is second


@pytest.mark.db
async def test_a_ninth_negative_ends_the_oldest_and_an_image_is_one_negative(
    db_session: AsyncSession,
) -> None:
    movie_id = await _movie(db_session)
    event_id = await _event(db_session, movie_id, "a")
    changes = exemplars.Changes()
    made = [
        await _negative(db_session, movie_id, event_id, SHA[n], changes, NOW + timedelta(minutes=n))
        for n in range(1, 10)
    ]
    assert all(row is not None for row in made)
    active = await exemplars.active_negatives(db_session, movie_id)
    assert len(active) == exemplars.MAX_ACTIVE_NEGATIVES
    assert made[0].state == "superseded" and made[0] not in active  # type: ignore[union-attr]
    assert changes.ended == [made[0]]
    assert await _negative(db_session, movie_id, event_id, SHA[9], changes) is None
    assert len(await exemplars.active_negatives(db_session, movie_id)) == 8


@pytest.mark.db
async def test_hating_the_active_positive_ends_it(db_session: AsyncSession) -> None:
    movie_id = await _movie(db_session)
    seed = _seed(movie_id, SHA[1])
    db_session.add(seed)
    await db_session.flush()
    event_id = await _event(db_session, movie_id, "a")
    changes = exemplars.Changes()
    assert (
        await exemplars.end_positive_with_image(
            db_session, changes, movie_id=movie_id, sha256=SHA[2], event_id=event_id, now=NOW
        )
        is None
    )
    ended = await exemplars.end_positive_with_image(
        db_session, changes, movie_id=movie_id, sha256=SHA[1], event_id=event_id, now=NOW
    )
    assert ended is seed and seed.state == "superseded" and seed.ended_by_event_id == event_id
    assert await exemplars.active_positive(db_session, movie_id) is None


@pytest.mark.db
async def test_revoking_brings_back_what_the_positive_replaced(db_session: AsyncSession) -> None:
    movie_id = await _movie(db_session)
    seed = _seed(movie_id, SHA[1])
    db_session.add(seed)
    await db_session.flush()
    event_id = await _event(db_session, movie_id, "a")
    changes = exemplars.Changes()
    positive = await _positive(db_session, movie_id, event_id, SHA[2], changes)
    negative = await _negative(db_session, movie_id, event_id, SHA[3], changes)

    undone = exemplars.Changes()
    await exemplars.revoke_event(db_session, undone, event_id=event_id, now=NOW)

    assert (positive.state, negative.state) == ("revoked", "revoked")  # type: ignore[union-attr]
    assert seed.state == "active" and seed.ended_at is None
    assert undone.ended == [positive, negative] and undone.reactivated == [seed]


@pytest.mark.db
async def test_revoking_an_event_whose_positive_was_superseded_brings_nothing_back(
    db_session: AsyncSession,
) -> None:
    movie_id = await _movie(db_session)
    seed = _seed(movie_id, SHA[1])
    db_session.add(seed)
    await db_session.flush()
    first_event = await _event(db_session, movie_id, "a")
    changes = exemplars.Changes()
    first = await _positive(db_session, movie_id, first_event, SHA[2], changes)
    second = await _positive(
        db_session, movie_id, await _event(db_session, movie_id, "b"), SHA[3], changes
    )

    undone = exemplars.Changes()
    await exemplars.revoke_event(db_session, undone, event_id=first_event, now=NOW)

    assert first.state == "revoked" and first.ended_at == NOW
    assert seed.state == "superseded"
    assert second.state == "active" and undone.reactivated == []


@pytest.mark.db
async def test_the_partial_indexes_back_the_rules(db_session: AsyncSession) -> None:
    movie_id = await _movie(db_session)
    db_session.add_all([_seed(movie_id, SHA[1]), _seed(movie_id, SHA[2])])
    with pytest.raises(IntegrityError):
        await db_session.flush()


def test_the_revision_is_the_sorted_active_set() -> None:
    def row(polarity: str, sha: str, weight: float, staged: str | None, state: str = "active"):
        return Exemplar(
            polarity=polarity, sha256=sha, weight=weight, staged_name=staged, state=state
        )

    a = row("positive", SHA[1], 1.0, "A (2001).png")
    b = row("negative", SHA[2], 1.0, None)
    c = row("positive", SHA[3], 0.75, "C (2003).jpg", state="superseded")
    base = exemplars.revision_of([a, b, c])
    assert base == exemplars.revision_of([b, a])  # order and ended rows do not count
    assert len(base) == 64
    assert base != exemplars.revision_of([a])
    assert base != exemplars.revision_of([row("positive", SHA[1], 0.75, "A (2001).png"), b])
    assert base != exemplars.revision_of([row("positive", SHA[1], 1.0, "A.png"), b])
    assert base == exemplars.revision_of([row("positive", SHA[1], 1.0000001, "A (2001).png"), b])


@pytest.mark.db
async def test_revision_reads_the_database(db_session: AsyncSession) -> None:
    movie_id = await _movie(db_session)
    empty = await exemplars.revision(db_session)
    seed = _seed(movie_id, SHA[1])
    db_session.add(seed)
    await db_session.flush()
    assert await exemplars.revision(db_session) == exemplars.revision_of([seed]) != empty
    assert (await db_session.scalars(select(Exemplar))).all() == [seed]
