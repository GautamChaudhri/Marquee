"""The poster_run handler against a real PostgreSQL server, with a fake GPU child."""

from __future__ import annotations

import asyncio
import json
import threading
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from marquee.artwork import runs
from marquee.cancellation import JobCancelledError
from marquee.config import settings
from marquee.db import engine as db_engine_module
from marquee.db.engine import create_session_factory
from marquee.db.models import Job, Movie, PosterRun, RunCandidate
from marquee.jobs.handlers import poster_run as handler
from marquee.jobs.registry import JobContext, PermanentJobError

pytestmark = pytest.mark.db

LEDGER = [
    {
        "orig_filename": "a.jpg",
        "stage_reached": "ranked",
        "gate_decision": "passed",
        "rank": 1,
        "final_score": 0.9,
        "stack_rank": 1,
        "stack_label": "A",
    },
    {"orig_filename": "b.jpg", "stage_reached": "ocr", "rejection_reason": "text_heavy"},
]


@pytest.fixture
def sessions(db_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return create_session_factory(db_engine)


@pytest.fixture(autouse=True)
def _test_database(database_url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """The handler builds its own engine; point it at the test database."""
    real = db_engine_module.create_engine
    monkeypatch.setattr(handler, "create_engine", lambda url=None: real(database_url))


async def _movie(sessions: async_sessionmaker[AsyncSession], **values: Any) -> int:
    now = datetime.now(UTC)
    defaults: dict[str, Any] = {
        "radarr_id": 1,
        "title": "Die Hard",
        "sort_title": "die hard",
        "radarr_path": "/radarr/movies/Die Hard (1988)",
        "has_file": True,
        "tmdb_id": 562,
        "director": "John McTiernan",
        "production_companies": ["Gordon Company"],
        "tagline": "Twelve terrorists.",
        "tmdb_enriched_at": now,
        "last_seen_at": now,
    }
    async with sessions() as session:
        movie = Movie(**{**defaults, **values})
        session.add(movie)
        await session.commit()
        return movie.id


async def _job(sessions: async_sessionmaker[AsyncSession], job_id: int, **values: Any) -> int:
    async with sessions() as session:
        session.add(Job(id=job_id, kind="poster_run", job_class="gpu", state="running", **values))
        await session.commit()
    return job_id


def _context(job_id: int, payload: dict, gpu: Any) -> JobContext:
    return JobContext(job_id, payload, threading.Event(), lambda *_: None, gpu_runner=gpu)


def _fake_child(calls: list[dict], *, pick: str | None = "a.jpg") -> Any:
    async def gpu(function: str, payload: dict) -> dict:
        assert function == handler.CHILD_FUNCTION
        calls.append(payload)
        folder = settings.runs_dir / payload["run_folder"]
        folder.mkdir(parents=True)
        (folder / "a.jpg").write_bytes(b"poster a")
        (folder / "b.jpg").write_bytes(b"poster b")
        (folder / runs.RUN_ARCHIVE).write_text(
            json.dumps({"diagnostic_ledger": {"candidates": LEDGER}})
        )
        return {
            "status": "completed",
            "counts": {"posters_found": 2, "ranked": 1},
            "stages": [{"stage": "fetch", "survivors": 2, "seconds": 0.1}],
            "scorer": "weighted",
            "engine_run_id": "f" * 32,
            "ai_model": "clip-vit-b-32",
            "profile_sha256": "c" * 64,
            "pick_file": pick,
        }

    return gpu


async def test_a_run_leaves_its_record_candidates_and_pick(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    movie_id = await _movie(sessions)
    job_id = await _job(sessions, 30)
    calls: list[dict] = []

    result = await handler.poster_run(_context(job_id, {"movie_id": movie_id}, _fake_child(calls)))

    (payload,) = calls
    assert payload == {
        "run_folder": result["run_folder"],
        "movie_id": movie_id,
        "title": "Die Hard",
        "tmdb_id": 562,
        "director": "John McTiernan",
        "production_companies": ["Gordon Company"],
        "tagline": "Twelve terrorists.",
    }
    async with sessions() as session:
        run = await runs.run_for_job(session, job_id)
        assert run is not None and run.state == "completed"
        assert result["run_folder"] == run.run_folder == f"{run.id:08d}-die-hard"
        pick = await runs.pick_of(session, run.id)
        assert pick is not None and pick.source_file == "a.jpg"
    assert result["pick"] == {
        "candidate_id": pick.id,
        "rank": 1,
        "stack": "1A",
        "score": 0.9,
        "file": "a.jpg",
    }
    assert result["ranked_total"] == 1 and result["status"] == "completed"


async def test_a_second_attempt_reuses_the_row_and_folder_name(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    movie_id = await _movie(sessions)
    job_id = await _job(sessions, 31)
    first = await handler.poster_run(_context(job_id, {"movie_id": movie_id}, _fake_child([])))
    async with sessions() as session:
        await session.execute(text("update poster_runs set state = 'running'"))
        await session.commit()

    second = await handler.poster_run(_context(job_id, {"movie_id": movie_id}, _fake_child([])))

    assert second["run_id"] == first["run_id"]
    assert second["run_folder"] == first["run_folder"]
    async with sessions() as session:
        assert await session.scalar(select(func.count()).select_from(PosterRun)) == 1
        assert await session.scalar(select(func.count()).select_from(RunCandidate)) == 2


async def test_a_completed_run_is_never_run_again(
    sessions: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A worker killed between completing the run and finishing the job: the next attempt
    returns the recorded result and changes nothing (no candidate deleted, no folder
    removed, the child not called, no second policy deploy)."""
    monkeypatch.setattr(settings, "DEPLOY_POLICY", "all")
    movie_id = await _movie(sessions)
    job_id = await _job(sessions, 32)
    calls: list[dict] = []
    first = await handler.poster_run(_context(job_id, {"movie_id": movie_id}, _fake_child(calls)))
    assert first["deploy"]["queued"] is True
    folder = settings.runs_dir / first["run_folder"]
    (folder / "feedback-was-here").write_text("kept")
    async with sessions() as session:
        before = list(await session.scalars(select(RunCandidate.id).order_by(RunCandidate.id)))
        # The policy's deploy ran meanwhile, so its dedupe key is free again.
        await session.execute(
            text("update jobs set state = 'succeeded' where kind = 'poster_deploy'")
        )
        await session.commit()

    again = await handler.poster_run(_context(job_id, {"movie_id": movie_id}, _fake_child(calls)))

    assert len(calls) == 1
    assert again["already_completed"] is True
    assert again["run_id"] == first["run_id"] and again["run_folder"] == first["run_folder"]
    assert again["pick"] == first["pick"]
    assert again["ranked"] == first["ranked"] and again["ranked_total"] == first["ranked_total"]
    assert again["deploy"] is None
    assert (folder / "feedback-was-here").read_text() == "kept"
    async with sessions() as session:
        after = list(await session.scalars(select(RunCandidate.id).order_by(RunCandidate.id)))
        deploys = await session.scalar(
            select(func.count()).select_from(Job).where(Job.kind == "poster_deploy")
        )
        run = await runs.get_run(session, first["run_id"])
    assert after == before
    assert deploys == 1
    assert run is not None and run.state == "completed"


@pytest.mark.parametrize(
    ("values", "message"),
    [
        ({"has_file": False}, "has no file"),
        ({"tmdb_enriched_at": None}, "no TMDB details"),
        ({"is_present": False}, "no longer listed"),
    ],
)
async def test_an_ineligible_movie_fails_for_good_without_a_run(
    sessions: async_sessionmaker[AsyncSession], values: dict, message: str
) -> None:
    movie_id = await _movie(sessions, **values)
    job_id = await _job(sessions, 32)

    with pytest.raises(PermanentJobError, match=message):
        await handler.poster_run(_context(job_id, {"movie_id": movie_id}, _fake_child([])))
    async with sessions() as session:
        assert await session.scalar(select(func.count()).select_from(PosterRun)) == 0


@pytest.mark.parametrize("payload", [{}, {"movie_id": "1"}, {"movie_id": True}, {"movie_id": 0}])
async def test_a_bad_payload_fails_for_good(payload: dict) -> None:
    with pytest.raises(PermanentJobError, match="movie_id"):
        await handler.poster_run(_context(1, payload, _fake_child([])))


async def test_an_unknown_movie_fails_for_good(sessions: async_sessionmaker[AsyncSession]) -> None:
    with pytest.raises(PermanentJobError, match="no movie 999"):
        await handler.poster_run(_context(1, {"movie_id": 999}, _fake_child([])))


async def test_a_child_failure_fails_the_run_and_keeps_its_folder(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    movie_id = await _movie(sessions)
    job_id = await _job(sessions, 33)

    async def broken(function: str, payload: dict) -> None:
        (settings.runs_dir / payload["run_folder"]).mkdir(parents=True)
        raise RuntimeError("TMDB: 503 Service Unavailable\nmore")

    with pytest.raises(RuntimeError):
        await handler.poster_run(_context(job_id, {"movie_id": movie_id}, broken))
    async with sessions() as session:
        run = await runs.run_for_job(session, job_id)
    assert run is not None and run.state == "failed"
    assert run.error == "RuntimeError: TMDB: 503 Service Unavailable"
    assert (settings.runs_dir / run.run_folder).is_dir()


async def test_a_cancel_that_was_asked_cancels_the_run_and_removes_its_folder(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    movie_id = await _movie(sessions)
    job_id = await _job(sessions, 34, cancel_requested=True)

    async def cancelled(function: str, payload: dict) -> None:
        (settings.runs_dir / payload["run_folder"]).mkdir(parents=True)
        raise JobCancelledError("poster pipeline cancelled")

    with pytest.raises(JobCancelledError):
        await handler.poster_run(_context(job_id, {"movie_id": movie_id}, cancelled))
    async with sessions() as session:
        run = await runs.run_for_job(session, job_id)
    assert run is not None and run.state == "cancelled"
    assert not (settings.runs_dir / run.run_folder).exists()


async def test_a_stopping_worker_leaves_the_run_for_the_next_attempt(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    movie_id = await _movie(sessions)
    job_id = await _job(sessions, 35)  # no cancel asked: the worker is stopping

    async def stopped(function: str, payload: dict) -> None:
        (settings.runs_dir / payload["run_folder"]).mkdir(parents=True)
        raise JobCancelledError("poster pipeline cancelled")

    with pytest.raises(JobCancelledError):
        await handler.poster_run(_context(job_id, {"movie_id": movie_id}, stopped))
    async with sessions() as session:
        run = await runs.run_for_job(session, job_id)
    assert run is not None and run.state == "running"


async def test_a_timeout_fails_the_run(sessions: async_sessionmaker[AsyncSession]) -> None:
    movie_id = await _movie(sessions)
    job_id = await _job(sessions, 36)

    async def slow(function: str, payload: dict) -> None:
        await asyncio.sleep(30)

    with pytest.raises(TimeoutError):
        async with asyncio.timeout(0.2):
            await handler.poster_run(_context(job_id, {"movie_id": movie_id}, slow))
    async with sessions() as session:
        run = await runs.run_for_job(session, job_id)
    assert run is not None and run.state == "failed" and run.error == handler.STOPPED


async def test_a_batch_leaves_out_movies_with_an_active_run(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    first = await _movie(sessions)
    second = await _movie(sessions, radarr_id=2, title="Heat", sort_title="heat")
    async with sessions() as session:
        active, created = await handler.enqueue_run(session, first)
        again, created_again = await handler.enqueue_run(session, first)
        assert created and not created_again and again.id == active.id
        parent, children, found = await handler.enqueue_scope(session, "all")
    assert found == 2
    assert [c.payload for c in children] == [{"movie_id": second}]
    assert parent.payload == {"scope": "all", "movies": 2}
