"""Review: feedback with the deploy and the rebuild it calls for, in one commit."""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

import pytest
from PIL import Image
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from marquee.artwork import deploy
from marquee.config import settings
from marquee.db import engine as db_engine_module
from marquee.db.engine import create_session_factory
from marquee.db.models import (
    Deployment,
    Exemplar,
    Job,
    Movie,
    PosterRun,
    PreferenceEvent,
    RunCandidate,
)
from marquee.jobs import review
from marquee.jobs.handlers import poster_write
from marquee.jobs.registry import JobContext, JobKind
from marquee.jobs.worker import Worker
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


# ── Two picks in quick succession ─────────────────────────────────────────


def _jpeg(colour: tuple[int, int, int]) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (500, 750), colour).save(out, format="JPEG")
    return out.getvalue()


ORIGINALS = {
    "r1.jpg": _jpeg((200, 0, 0)),
    "r2.jpg": _jpeg((0, 200, 0)),
    "r3.jpg": _jpeg((0, 0, 200)),
}


@pytest.fixture
def movie_folder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, database_url: str) -> Path:
    """The movie's folder in tmp_path, the write handlers on the test database, and TMDB
    faked: each candidate's original is a distinct JPEG."""
    root = tmp_path / "movies"
    folder = root / "Dunkirk (2017)"
    folder.mkdir(parents=True)
    monkeypatch.setattr(settings, "MOVIE_ROOTS", [root])
    monkeypatch.setattr(settings, "RADARR_MOVIE_ROOTS", [PurePosixPath("/radarr")])
    real = db_engine_module.create_engine
    monkeypatch.setattr(poster_write, "create_engine", lambda url=None: real(database_url))

    async def fake_fetch(source_file: str, client: object = None) -> bytes:
        return ORIGINALS[source_file]

    monkeypatch.setattr(deploy, "fetch_original", fake_fetch)
    return folder


async def _no_rebuild(ctx: JobContext) -> dict[str, Any]:
    return {"skipped": "not in this test"}


async def _drain_writes(database_url: str) -> None:
    """A worker that runs what is queued and exits; rebuilds are stubbed out."""
    kinds = (
        poster_write.POSTER_DEPLOY,
        poster_write.POSTER_RESTORE,
        JobKind("taste_rebuild", "gpu", handler=_no_rebuild),
    )
    worker = Worker(
        registry={kind.name: kind for kind in kinds},
        database_url=database_url,
        schedules=(),
        drain=True,
        poll_s=0.2,
        stop_grace_s=2.0,
        gpu_idle_s=60.0,
    )
    await asyncio.wait_for(worker.run(), 30)


async def test_an_owners_pick_beats_a_policy_deploy_queued_after_it(
    sessions: async_sessionmaker[AsyncSession],
    movie_folder: Path,
    database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The owner picks; a run's policy deploy of the pick is queued next. The worker runs
    the owner's first (the older job) and must write it; the policy's must then step aside
    rather than write the engine's pick over the owner's choice."""
    monkeypatch.setattr(settings, "DEPLOY_POLICY", "all")
    ids = await _run(sessions)
    async with sessions() as session:
        owners = await review.submit(
            session,
            run_id=ids["run"],
            action="override",
            candidate_id=ids["r2"],
            idempotency_key="a",
        )
    async with sessions() as session:
        policy, created = await poster_write.enqueue_deploy(
            session, ids["r1"], initiated_by="policy"
        )
    assert owners.deploy_job is not None and created and policy.id > owners.deploy_job.id

    await _drain_writes(database_url)

    assert (movie_folder / "poster.jpg").read_bytes() == ORIGINALS["r2.jpg"]
    async with sessions() as session:
        done = await session.get(Job, policy.id)
        written = list(await session.scalars(select(Deployment)))
    assert (
        done is not None and done.result["skipped"] == f"superseded by job {owners.deploy_job.id}"
    )
    assert [(row.candidate_id, row.initiated_by) for row in written] == [(ids["r2"], "owner")]


async def test_two_picks_while_nothing_runs_deploy_the_second(
    sessions: async_sessionmaker[AsyncSession], movie_folder: Path, database_url: str
) -> None:
    ids = await _run(sessions)
    async with sessions() as session:
        first = await review.submit(
            session,
            run_id=ids["run"],
            action="override",
            candidate_id=ids["r2"],
            idempotency_key="a",
        )
    async with sessions() as session:
        second = await review.submit(
            session,
            run_id=ids["run"],
            action="override",
            candidate_id=ids["r3"],
            idempotency_key="b",
        )
    assert first.deploy_job is not None and second.deploy_job is not None
    # Each event names its own deploy, never an older one.
    assert second.deploy_job.id > first.deploy_job.id
    assert second.deploy_job.payload["event_id"] == second.outcome.event.id
    assert second.deploy_job.payload["candidate_id"] == ids["r3"]

    await _drain_writes(database_url)

    assert (movie_folder / "poster.jpg").read_bytes() == ORIGINALS["r3.jpg"]
    async with sessions() as session:
        older = await session.get(Job, first.deploy_job.id)
        newer = await session.get(Job, second.deploy_job.id)
        written = list(await session.scalars(select(Deployment)))
    assert older is not None and newer is not None
    assert (older.state, newer.state) == ("succeeded", "succeeded")
    assert older.result["skipped"] == f"superseded by job {newer.id}"
    assert newer.result["outcome"] == "written"
    assert [row.candidate_id for row in written] == [ids["r3"]]
    async with sessions() as session:
        positive = await exemplars.active_positive(session, ids["movie"])
    assert positive is not None and positive.source_file == "r3.jpg"
