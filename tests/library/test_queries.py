from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from marquee.db.models import Movie
from marquee.library import queries


@pytest.mark.parametrize(
    ("text", "escaped"),
    [("plain", "plain"), ("100%", "100\\%"), ("a_b", "a\\_b"), ("back\\slash", "back\\\\slash")],
)
def test_escape_like(text: str, escaped: str) -> None:
    assert queries.escape_like(text) == escaped


@pytest.mark.db
async def test_list_and_get_leave_out_retired_movies(db_session: AsyncSession) -> None:
    now = datetime(2026, 11, 24, tzinfo=UTC)
    present = Movie(
        radarr_id=1,
        title="Here",
        sort_title="here",
        radarr_path="/m/Here",
        has_file=True,
        last_seen_at=now,
    )
    retired = Movie(
        radarr_id=2,
        title="Gone",
        sort_title="gone",
        radarr_path="/m/Gone",
        has_file=True,
        last_seen_at=now,
        is_present=False,
        retired_at=now,
    )
    db_session.add_all([present, retired])
    await db_session.commit()

    page = await queries.list_movies(db_session, include_unavailable=True)
    assert page.total == 1
    assert [row.movie.title for row in page.items] == ["Here"]
    assert (await queries.get_movie(db_session, present.id)).poster is None
    assert await queries.get_movie(db_session, retired.id) is None


@pytest.mark.db
async def test_a_masked_run_is_not_a_movies_latest_run(db_session: AsyncSession) -> None:
    from marquee.db.models import PosterRun

    now = datetime(2026, 11, 24, tzinfo=UTC)
    movie = Movie(
        radarr_id=1,
        title="Heat",
        sort_title="heat",
        radarr_path="/m/Heat",
        has_file=True,
        last_seen_at=now,
    )
    only_masked = Movie(
        radarr_id=2,
        title="Ran",
        sort_title="ran",
        radarr_path="/m/Ran",
        has_file=True,
        last_seen_at=now,
    )
    db_session.add_all([movie, only_masked])
    await db_session.flush()
    normal = PosterRun(movie_id=movie.id, state="completed", run_folder="a", started_at=now)
    masked = PosterRun(
        movie_id=movie.id, state="completed", run_folder="b", started_at=now, masked=True
    )
    alone = PosterRun(
        movie_id=only_masked.id, state="completed", run_folder="c", started_at=now, masked=True
    )
    db_session.add_all([normal, masked, alone])
    await db_session.commit()
    assert masked.id > normal.id

    found = await queries.get_movie(db_session, movie.id)
    assert found is not None and found.latest_run_id == normal.id
    page = await queries.list_movies(db_session)
    assert {row.movie.title: row.latest_run_id for row in page.items} == {
        "Heat": normal.id,
        "Ran": None,
    }
    with_run = await queries.list_movies(db_session, has_run=True)
    assert [row.movie.title for row in with_run.items] == ["Heat"]
    without = await queries.list_movies(db_session, has_run=False)
    assert [row.movie.title for row in without.items] == ["Ran"]
