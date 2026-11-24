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
