"""Agreement, the review list, and the taste status, against a real PostgreSQL server."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from marquee.db.models import Deployment, Movie, PosterRun, PreferenceEvent, TasteProfile
from marquee.taste import agreement

pytestmark = pytest.mark.db
NOW = datetime(2026, 11, 24, 12, 0, tzinfo=UTC)
_keys = iter(range(10_000))


async def _movie(session: AsyncSession, n: int) -> int:
    movie = Movie(
        radarr_id=n,
        title=f"Movie {n}",
        sort_title=f"movie {n:03d}",
        radarr_path=f"/radarr/Movie {n}",
        has_file=True,
        last_seen_at=NOW,
    )
    session.add(movie)
    await session.flush()
    return movie.id


async def _run(
    session: AsyncSession, movie_id: int, state: str = "completed", profile: str = "a" * 64
) -> int:
    run = PosterRun(
        movie_id=movie_id,
        state=state,
        run_folder=f"run-{movie_id}-{next(_keys)}",
        started_at=NOW,
        finished_at=NOW,
        profile_sha256=profile,
    )
    session.add(run)
    await session.flush()
    return run.id


async def _event(
    session: AsyncSession,
    movie_id: int,
    run_id: int,
    *,
    chosen_rank: int | None,
    blind: bool | None = True,
    action: str | None = None,
    profile: str = "a" * 64,
    when: datetime = NOW,
) -> PreferenceEvent:
    exposed = [{"candidate_id": f"r{n}.jpg", "baseline_rank": n} for n in range(1, 6)]
    chosen = f"r{chosen_rank}.jpg" if chosen_rank else None
    event = PreferenceEvent(
        movie_id=movie_id,
        run_id=run_id,
        action=action or ("reject" if chosen is None else "rank"),
        chosen_source_file=chosen,
        pick_source_file="r1.jpg",
        profile_sha256=profile,
        blind=blind,
        exposed=exposed,
        context={},
        idempotency_key=f"k{next(_keys)}",
        created_at=when,
    )
    session.add(event)
    await session.flush()
    return event


async def _revoke(session: AsyncSession, event: PreferenceEvent) -> None:
    session.add(
        PreferenceEvent(
            movie_id=event.movie_id,
            run_id=event.run_id,
            action="revoke",
            exposed=[],
            context={},
            revokes_event_id=event.id,
            idempotency_key=f"revoke:{event.id}",
        )
    )
    await session.flush()


async def test_agreement_counts_the_first_unrevoked_review_of_each_blind_run(
    db_session: AsyncSession,
) -> None:
    session = db_session
    session.add(
        TasteProfile(
            sha256="a" * 64,
            revision="b" * 64,
            source="seed",
            positives=1,
            negatives=0,
            positive_movie_ids=[],
            file_name=f"{'a' * 64}.npz",
        )
    )
    m = [await _movie(session, n) for n in range(1, 8)]
    runs = [await _run(session, movie) for movie in m]
    await _event(session, m[0], runs[0], chosen_rank=1)  # agreed, top 3
    await _event(session, m[0], runs[0], chosen_rank=4)  # a second review of run 0: not counted
    await _event(session, m[1], runs[1], chosen_rank=3)  # top 3 only
    await _event(session, m[2], runs[2], chosen_rank=None)  # a reject: reviewed only
    revoked = await _event(session, m[3], runs[3], chosen_rank=1)
    await _revoke(session, revoked)
    await _event(session, m[3], runs[3], chosen_rank=5)  # now the first unrevoked of run 3
    await _event(session, m[4], runs[4], chosen_rank=1, blind=False)
    await _event(session, m[5], runs[5], chosen_rank=1, blind=None)
    await _event(
        session,
        m[6],
        runs[6],
        chosen_rank=1,
        profile="c" * 64,
        when=datetime(2026, 10, 3, tzinfo=UTC),
    )

    result = await agreement.agreement(session)

    assert result.blind.to_dict() == {"reviewed": 5, "agreed": 2, "top3": 3, "percent": None}
    assert result.blind.text() == "2 of 5 agreed, 3 of 5 in the top 3"
    assert result.not_blind.reviewed == 1 and result.unknown.reviewed == 1
    profile_id = next(iter(k for k in result.by_profile if isinstance(k, int)))
    assert result.by_profile[profile_id].reviewed == 4
    assert result.by_profile["cccccccccccc"].reviewed == 1  # an unregistered hash, shortened
    assert {k: v.reviewed for k, v in result.by_month.items()} == {"2026-11": 4, "2026-10": 1}


def test_a_percentage_only_from_ten_reviews() -> None:
    assert agreement.Tally(9, 9, 9).percent is None
    assert agreement.Tally(10, 7, 9).percent == 70.0
    assert agreement.Tally(10, 7, 9).text() == "7 of 10 agreed (70%), 9 of 10 in the top 3"
    assert agreement.Tally().text() == "0 reviewed"


async def test_the_review_list(db_session: AsyncSession) -> None:
    session = db_session
    reviewed, waiting, deployed, revoked_only, failed = [
        await _movie(session, n) for n in range(1, 6)
    ]
    old = await _run(session, reviewed)
    await _event(session, reviewed, old, chosen_rank=1)
    await _run(session, reviewed)  # a newer run of a reviewed movie waits again
    await _run(session, waiting)
    deployed_run = await _run(session, deployed)
    session.add(
        Deployment(
            movie_id=deployed,
            artwork_kind="poster",
            action="deploy",
            initiated_by="policy",
            run_id=deployed_run,
            file_name="poster.jpg",
            new_sha256="d" * 64,
            new_size_bytes=1,
        )
    )
    run = await _run(session, revoked_only)
    await _revoke(session, await _event(session, revoked_only, run, chosen_rank=2))
    await _run(session, failed, state="failed")
    await session.flush()

    pending = await agreement.pending_reviews(session)

    assert pending[0].movie_id == deployed and pending[0].policy_deployed
    assert {p.movie_id for p in pending} == {reviewed, waiting, deployed, revoked_only}
    assert await agreement.run_reviewed(session, old)
    assert not await agreement.run_reviewed(session, run)
    status = await agreement.status(session)
    assert status.pending_reviews == 4
    assert status.profile is None and not status.rebuild_due
    assert status.residual_pairs == 0 and status.residual_min_subjects == 25


async def test_a_masked_run_never_enters_the_review_queue(db_session: AsyncSession) -> None:
    movie_id = await _movie(db_session, 1)
    normal = await _run(db_session, movie_id)
    masked = await _run(db_session, movie_id)
    row = await db_session.get(PosterRun, masked)
    assert row is not None
    row.masked = True
    await db_session.flush()

    pending = await agreement.pending_reviews(db_session)
    assert [p.run_id for p in pending] == [normal]
    await _event(db_session, movie_id, normal, chosen_rank=1)
    assert await agreement.pending_reviews(db_session) == []
