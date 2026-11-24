"""Review: feedback with the deploy and the rebuild it calls for, in one commit."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from marquee.config import settings
from marquee.db.engine import create_session_factory
from marquee.db.models import Exemplar, Job, Movie, PosterRun, PreferenceEvent, RunCandidate
from marquee.jobs import review
from marquee.taste import exemplars, feedback

pytestmark = pytest.mark.db
NOW = datetime(2026, 11, 24, 12, 0, tzinfo=UTC)


@pytest.fixture
def sessions(db_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return create_session_factory(db_engine)


@pytest.fixture(autouse=True)
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path / "data")
    return tmp_path / "data"


async def _run(sessions: async_sessionmaker[AsyncSession], **movie: object) -> dict[str, int]:
    async with sessions() as session:
        row = Movie(
            radarr_id=1,
            title="Dunkirk",
            sort_title="dunkirk",
            year=2017,
            radarr_path="/radarr/Dunkirk (2017)",
            has_file=True,
            last_seen_at=NOW,
            **movie,
        )
        session.add(row)
        await session.flush()
        run = PosterRun(
            movie_id=row.id, state="completed", run_folder="00000001-dunkirk", started_at=NOW
        )
        session.add(run)
        await session.flush()
        folder = settings.runs_dir / run.run_folder
        folder.mkdir(parents=True)
        ids = {"movie": row.id, "run": run.id}
        ledger = []
        for n in (1, 2, 3):
            data = f"poster {n}".encode()
            (folder / f"r{n}.jpg").write_bytes(data)
            candidate = RunCandidate(
                run_id=run.id,
                source_file=f"r{n}.jpg",
                sha256=hashlib.sha256(data).hexdigest(),
                rank=n,
                final_score=1 - n / 10,
                is_pick=n == 1,
            )
            session.add(candidate)
            await session.flush()
            ids[f"r{n}"] = candidate.id
            ledger.append({"orig_filename": f"r{n}.jpg", "normalized_features": {"knn_sim": n}})
        (folder / "pipeline_run.json").write_text(
            json.dumps({"diagnostic_ledger": {"candidates": ledger}})
        )
        await session.commit()
    return ids


async def _count(sessions: async_sessionmaker[AsyncSession], model: type) -> int:
    async with sessions() as session:
        return int(await session.scalar(select(func.count()).select_from(model)) or 0)


async def test_a_pick_queues_its_deploy_and_a_rebuild(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    ids = await _run(sessions)
    async with sessions() as session:
        result = await review.submit(
            session,
            run_id=ids["run"],
            action="override",
            candidate_id=ids["r2"],
            idempotency_key="k",
        )
        revision = await exemplars.revision(session)
    assert result.created and result.deploy_note is None
    assert result.deploy_job is not None and result.rebuild_job is not None
    assert result.deploy_job.payload == {
        "candidate_id": ids["r2"],
        "initiated_by": "owner",
        "movie_id": ids["movie"],
        "event_id": result.outcome.event.id,
    }
    assert result.rebuild_job.payload == {
        "revision": revision,
        "force": False,
        "event_id": result.outcome.event.id,
    }
    assert result.rebuild_job.dedupe_key == f"taste_rebuild:{revision}"
    assert await _count(sessions, PreferenceEvent) == 1  # committed
    assert await _count(sessions, Job) == 2

    async with sessions() as session:
        again = await review.submit(
            session,
            run_id=ids["run"],
            action="override",
            candidate_id=ids["r2"],
            idempotency_key="k",
        )
    assert not again.created
    assert again.outcome.event.id == result.outcome.event.id
    assert (again.deploy_job.id, again.rebuild_job.id) == (  # type: ignore[union-attr]
        result.deploy_job.id,
        result.rebuild_job.id,
    )
    assert await _count(sessions, Job) == 2


async def test_no_deploy_and_no_rebuild_when_not_called_for(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    ids = await _run(sessions)
    async with sessions() as session:
        result = await review.submit(
            session, run_id=ids["run"], action="approval", idempotency_key="a", deploy=False
        )
    assert result.deploy_job is None and result.rebuild_job is not None
    async with sessions() as session:
        # A second approval (the movie now has a positive) changes no exemplar.
        await session.execute(PosterRun.__table__.update().values(state="completed"))
        again = await review.submit(
            session, run_id=ids["run"], action="approval", idempotency_key="b"
        )
    assert again.rebuild_job is None and again.deploy_job is not None


async def test_a_refused_deploy_keeps_the_feedback(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    ids = await _run(sessions, is_present=False)
    async with sessions() as session:
        result = await review.submit(
            session, run_id=ids["run"], action="rank", order=[ids["r3"]], idempotency_key="k"
        )
    assert result.deploy_job is None
    assert result.deploy_note is not None and "has no folder to write to" in result.deploy_note
    assert await _count(sessions, PreferenceEvent) == 1
    async with sessions() as session:
        positive = await exemplars.active_positive(session, ids["movie"])
    assert positive is not None and positive.source_file == "r3.jpg"


async def test_a_refusal_writes_nothing(sessions: async_sessionmaker[AsyncSession]) -> None:
    ids = await _run(sessions)
    async with sessions() as session:
        with pytest.raises(feedback.FeedbackRefusedError):
            await review.submit(session, run_id=ids["run"], action="override", idempotency_key="k")
    assert await _count(sessions, PreferenceEvent) == 0
    assert await _count(sessions, Job) == 0


async def test_a_revoke_queues_a_rebuild(sessions: async_sessionmaker[AsyncSession]) -> None:
    ids = await _run(sessions)
    async with sessions() as session:
        picked = await review.submit(
            session,
            run_id=ids["run"],
            action="override",
            candidate_id=ids["r3"],
            idempotency_key="k",
        )
    async with sessions() as session:
        undone = await review.revoke(session, event_id=picked.outcome.event.id)
        empty = await exemplars.revision(session)
    assert undone.created and undone.deploy_job is None
    assert undone.rebuild_job is not None and undone.rebuild_job.payload["revision"] == empty
    async with sessions() as session:
        states = list(await session.scalars(select(Exemplar.state)))
    assert states == ["revoked"]
    async with sessions() as session:
        again = await review.revoke(session, event_id=picked.outcome.event.id)
    assert not again.created and again.rebuild_job is not None
    assert again.rebuild_job.id == undone.rebuild_job.id
