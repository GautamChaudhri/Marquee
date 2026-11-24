"""Read queries over the job tables: the movie a job is about."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine

from marquee.db.engine import create_session_factory
from marquee.db.models import Movie
from marquee.jobs import queries, queue
from marquee.jobs.registry import JobContext, JobKind

pytestmark = pytest.mark.db
NOW = datetime(2026, 11, 24, 12, 0, tzinfo=UTC)


async def _noop(ctx: JobContext) -> None:
    return None


NET = JobKind("fake_net", "network", handler=_noop)


def test_payload_movie_id() -> None:
    assert queries.payload_movie_id({"movie_id": 3, "candidate_id": 9}) == 3
    for payload in (None, {}, {"movie_id": "3"}, {"movie_id": True}, {"movie_id": 0}):
        assert queries.payload_movie_id(payload) is None


async def test_each_job_names_its_movie_in_one_query(db_engine: AsyncEngine) -> None:
    sessions = create_session_factory(db_engine)
    async with sessions() as session:
        heat = Movie(
            radarr_id=1,
            title="Heat",
            sort_title="heat",
            year=1995,
            radarr_path="/radarr/Heat (1995)",
            has_file=True,
            last_seen_at=NOW,
        )
        session.add(heat)
        await session.commit()
        jobs = [
            (await queue.enqueue(session, NET, {"movie_id": heat.id}))[0],
            (await queue.enqueue(session, NET, {"movie_id": heat.id, "candidate_id": 4}))[0],
            (await queue.enqueue(session, NET, {"movie_id": heat.id + 1000}))[0],
            (await queue.enqueue(session, NET, {}))[0],
        ]
        statements: list[str] = []

        def count(conn: object, cursor: object, statement: str, *args: object) -> None:
            statements.append(statement)

        event.listen(db_engine.sync_engine, "before_cursor_execute", count)
        try:
            found = await queries.subjects(session, jobs)
        finally:
            event.remove(db_engine.sync_engine, "before_cursor_execute", count)

    assert len(statements) == 1
    assert found == {
        jobs[0].id: queries.JobSubject(heat.id, "Heat", 1995),
        jobs[1].id: queries.JobSubject(heat.id, "Heat", 1995),
    }
