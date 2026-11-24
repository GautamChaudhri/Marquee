"""The taste_rebuild handler against a real PostgreSQL server, with a fake GPU child."""

from __future__ import annotations

import hashlib
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from marquee.config import pipeline_settings, settings
from marquee.db import engine as db_engine_module
from marquee.db.engine import create_session_factory
from marquee.db.models import Exemplar, Job, Movie, ResidualTraining, TasteProfile
from marquee.jobs.handlers import taste_rebuild as handler
from marquee.jobs.registry import JobContext, PermanentJobError
from marquee.ml.artifact_codec import unicode_array
from marquee.taste import exemplars, rebuild

pytestmark = pytest.mark.db
NOW = datetime(2026, 11, 24, 12, 0, tzinfo=UTC)


@pytest.fixture
def sessions(db_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return create_session_factory(db_engine)


@pytest.fixture(autouse=True)
def _scratch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, database_url: str) -> Path:
    real = db_engine_module.create_engine
    monkeypatch.setattr(handler, "create_engine", lambda url=None: real(database_url))
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path / "data")
    profile = tmp_path / "data" / "ml" / "taste_profile.clip-vit-b-32.npz"
    monkeypatch.setattr(pipeline_settings, "TASTE_PROFILE_PATH", profile)
    monkeypatch.setattr(pipeline_settings, "TASTE_SEEDING_MIN_POSTERS", 2)
    return profile


async def _records(
    sessions: async_sessionmaker[AsyncSession], positives: int = 3, negatives: int = 1
) -> dict[str, Any]:
    """Movies with one seed positive each (kept images in the store), a negative, and the
    seed profile registered and in place."""
    async with sessions() as session:
        movie_ids = []
        for n in range(positives):
            movie = Movie(
                radarr_id=n + 1,
                title=f"Movie {n}",
                sort_title=f"movie {n}",
                year=2000 + n,
                radarr_path=f"/radarr/Movie {n}",
                has_file=True,
                last_seen_at=NOW,
            )
            session.add(movie)
            await session.flush()
            movie_ids.append(movie.id)
            data = f"positive {n}".encode()
            session.add(
                Exemplar(
                    movie_id=movie.id,
                    polarity="positive",
                    weight=1.0 if n else 0.75,
                    source="seed",
                    state="active",
                    sha256=exemplars.sha256_of(data),
                    file_name=exemplars.keep(data, ".png"),
                    staged_name=f"Movie {n} ({2000 + n}).png",
                    created_at=NOW,
                )
            )
        for n in range(negatives):
            data = f"negative {n}".encode()
            event_id = await _event(session, movie_ids[0], f"neg{n}")
            session.add(
                Exemplar(
                    movie_id=movie_ids[0],
                    polarity="negative",
                    weight=1.0,
                    source="feedback",
                    event_id=event_id,
                    source_file=f"n{n}.jpg",
                    state="active",
                    sha256=exemplars.sha256_of(data),
                    file_name=exemplars.keep(data, ".jpg"),
                    created_at=NOW,
                )
            )
        await session.flush()
        revision = await exemplars.revision(session)
        await session.commit()
    return {"movies": movie_ids, "revision": revision}


async def _event(session: AsyncSession, movie_id: int, key: str) -> int:
    from marquee.db.models import PreferenceEvent

    event = PreferenceEvent(
        movie_id=movie_id, action="reject", exposed=[], context={}, idempotency_key=key
    )
    session.add(event)
    await session.flush()
    return event.id


async def _seed_profile(sessions: async_sessionmaker[AsyncSession], path: Path) -> TasteProfile:
    path.parent.mkdir(parents=True, exist_ok=True)
    np.savez(
        path, embeddings=np.ones((2, 512), dtype=np.float32), poster_names=unicode_array(["a", "b"])
    )
    sha = rebuild.file_sha256(path)
    name = rebuild.keep_copy(path, sha)
    async with sessions() as session:
        row = await rebuild.register(
            session,
            sha256=sha,
            revision="0" * 64,
            source="seed",
            positives=2,
            negatives=0,
            movie_ids=[1, 2],
            file_name=name,
        )
        await session.commit()
    return row


_next_job = iter(range(300, 1000))


async def _job(sessions: async_sessionmaker[AsyncSession]) -> int:
    job_id = next(_next_job)
    async with sessions() as session:
        session.add(Job(id=job_id, kind="taste_rebuild", job_class="gpu", state="running"))
        await session.commit()
    return job_id


def _context(job_id: int, payload: dict, gpu: Any) -> JobContext:
    return JobContext(job_id, payload, threading.Event(), lambda *_: None, gpu_runner=gpu)


def _fake_child(calls: list[dict], *, extra_positive: bool = False) -> Any:
    """Stands in for the trainer: a profile of the staged files, in their sorted order."""

    async def gpu(function: str, payload: dict) -> dict:
        assert function == handler.CHILD_FUNCTION
        build = settings.taste_build_dir / payload["build"]
        positives = sorted(p.name for p in (build / "positive").iterdir())
        negatives = sorted(p.name for p in (build / "negative").iterdir())
        calls.append({**payload, "positives": positives, "negatives": negatives})
        names = positives + (["Extra (1999).png"] if extra_positive else [])
        payload_arrays = {
            "embeddings": np.ones((len(names), 512), dtype=np.float32),
            "poster_names": unicode_array(names),
        }
        if negatives:
            payload_arrays["neg_embeddings"] = np.ones((len(negatives), 512), dtype=np.float32)
            payload_arrays["neg_poster_names"] = unicode_array(negatives)
        np.savez(build / "profile.npz", **payload_arrays)
        return {"sha256": rebuild.file_sha256(build / "profile.npz")}

    return gpu


async def test_a_rebuild_stages_builds_and_publishes(
    sessions: async_sessionmaker[AsyncSession], _scratch: Path
) -> None:
    ids = await _records(sessions)
    seed = await _seed_profile(sessions, _scratch)
    job_id = await _job(sessions)
    calls: list[dict] = []

    result = await handler.taste_rebuild(
        _context(job_id, {"revision": ids["revision"], "force": False}, _fake_child(calls))
    )

    (call,) = calls
    assert call["build"] == str(job_id)
    assert call["positives"] == ["Movie 0 (2000).png", "Movie 1 (2001).png", "Movie 2 (2002).png"]
    assert call["positive_weights"] == {
        "Movie 0 (2000).png": 0.75,
        "Movie 1 (2001).png": 1.0,
        "Movie 2 (2002).png": 1.0,
    }
    negative = f"{hashlib.sha256(b'negative 0').hexdigest()}.jpg"
    assert call["negatives"] == [negative] and call["negative_weights"] == {negative: 1.0}

    assert result["skipped"] is None and result["built"] is True
    assert (result["positives"], result["negatives"], result["movies"]) == (3, 1, 3)
    sha = rebuild.file_sha256(_scratch)
    assert result["sha256"] == sha and result["previous_profile_id"] == seed.id
    async with sessions() as session:
        active = await rebuild.active_profile(session)
        rows = (await session.scalars(select(TasteProfile).order_by(TasteProfile.id))).all()
    assert active is not None and active.id == result["profile_id"]
    assert (active.sha256, active.source, active.revision, active.job_id) == (
        sha,
        "rebuild",
        ids["revision"],
        job_id,
    )
    assert active.positive_movie_ids == ids["movies"]
    assert [row.is_active for row in rows] == [False, True]
    assert (settings.profiles_dir / f"{sha}.npz").is_file()
    assert not (settings.taste_build_dir / str(job_id)).exists()
    assert not list(_scratch.parent.glob(".*.part"))
    # It trained the residual for the new profile (with no feedback, too little evidence).
    assert result["residual"]["outcome"] == "no_change"
    assert result["residual"]["reason"] == "insufficient_evidence"
    assert result["residual"]["profile_id"] == active.id
    async with sessions() as session:
        training = await session.scalar(select(ResidualTraining))
    assert training is not None and training.job_id == job_id

    again = await handler.taste_rebuild(
        _context(await _job(sessions), {"revision": ids["revision"], "force": False}, None)
    )
    assert again["skipped"] == "up to date" and again["profile_id"] == active.id


async def test_a_superseded_revision_is_skipped(sessions: async_sessionmaker[AsyncSession]) -> None:
    await _records(sessions)
    result = await handler.taste_rebuild(
        _context(await _job(sessions), {"revision": "f" * 64, "force": True}, None)
    )
    assert result["skipped"] == "superseded"


async def test_force_builds_even_when_up_to_date(
    sessions: async_sessionmaker[AsyncSession], _scratch: Path
) -> None:
    ids = await _records(sessions)
    calls: list[dict] = []
    payload = {"revision": ids["revision"], "force": False}
    await handler.taste_rebuild(_context(await _job(sessions), payload, _fake_child(calls)))
    forced = await handler.taste_rebuild(
        _context(await _job(sessions), {**payload, "force": True}, _fake_child(calls))
    )
    assert len(calls) == 2 and forced["built"] is True


async def test_a_later_attempt_publishes_what_an_earlier_one_built(
    sessions: async_sessionmaker[AsyncSession], _scratch: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Killed after the build was kept: the old profile stays complete in place, and the next
    attempt publishes the kept build without calling the child."""
    ids = await _records(sessions)
    seed = await _seed_profile(sessions, _scratch)
    old_sha = seed.sha256
    job_id = await _job(sessions)
    payload = {"revision": ids["revision"], "force": False}

    real_install = rebuild.install

    def killed(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("killed before the rename")

    monkeypatch.setattr(rebuild, "install", killed)
    with pytest.raises(RuntimeError):
        await handler.taste_rebuild(_context(job_id, payload, _fake_child([])))
    assert rebuild.file_sha256(_scratch) == old_sha  # still the old, complete profile
    monkeypatch.setattr(rebuild, "install", real_install)

    calls: list[dict] = []
    result = await handler.taste_rebuild(_context(job_id, payload, _fake_child(calls)))

    assert calls == [] and result["built"] is False
    assert rebuild.file_sha256(_scratch) == result["sha256"] != old_sha
    async with sessions() as session:
        active = await rebuild.active_profile(session)
    assert active is not None and active.sha256 == result["sha256"]


async def test_too_few_positives_fail_for_good_and_the_profile_stays(
    sessions: async_sessionmaker[AsyncSession], _scratch: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ids = await _records(sessions, positives=3)
    seed = await _seed_profile(sessions, _scratch)
    monkeypatch.setattr(pipeline_settings, "TASTE_SEEDING_MIN_POSTERS", 4)
    with pytest.raises(PermanentJobError, match="only 3 active positive exemplars"):
        await handler.taste_rebuild(
            _context(await _job(sessions), {"revision": ids["revision"]}, _fake_child([]))
        )
    assert rebuild.file_sha256(_scratch) == seed.sha256


async def test_two_positives_with_one_staged_name_fail_with_both_movies_named(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    ids = await _records(sessions)
    async with sessions() as session:
        row = await session.scalar(select(Exemplar).where(Exemplar.movie_id == ids["movies"][2]))
        assert row is not None
        row.staged_name = "Movie 1 (2001).png"
        revision = await exemplars.revision(session)
        await session.commit()
    with pytest.raises(PermanentJobError) as refused:
        await handler.taste_rebuild(_context(await _job(sessions), {"revision": revision}, None))
    assert "two active positives are staged as 'Movie 1 (2001).png'" in str(refused.value)
    assert f"Movie 1 (movie {ids['movies'][1]})" in str(refused.value)
    assert f"Movie 2 (movie {ids['movies'][2]})" in str(refused.value)


async def test_a_build_that_does_not_hold_what_was_staged_is_refused(
    sessions: async_sessionmaker[AsyncSession], _scratch: Path
) -> None:
    ids = await _records(sessions)
    seed = await _seed_profile(sessions, _scratch)
    with pytest.raises(PermanentJobError, match="4 positives and 1 negatives; 3 and 1"):
        await handler.taste_rebuild(
            _context(
                await _job(sessions),
                {"revision": ids["revision"]},
                _fake_child([], extra_positive=True),
            )
        )
    assert rebuild.file_sha256(_scratch) == seed.sha256


@pytest.mark.parametrize(
    "payload", [{}, {"revision": "abc"}, {"revision": "a" * 64, "force": "yes"}]
)
async def test_a_bad_payload_fails_for_good(payload: dict) -> None:
    with pytest.raises(PermanentJobError):
        await handler.taste_rebuild(_context(1, payload, None))


async def test_old_profile_copies_are_pruned(
    sessions: async_sessionmaker[AsyncSession], _scratch: Path
) -> None:
    ids = await _records(sessions)
    seed = await _seed_profile(sessions, _scratch)
    for n in range(6):
        async with sessions() as session:
            data = f"old {n}".encode()
            sha = hashlib.sha256(data).hexdigest()
            (settings.profiles_dir / f"{sha}.npz").write_bytes(data)
            session.add(
                TasteProfile(
                    sha256=sha,
                    revision="1" * 64,
                    source="rebuild",
                    positives=1,
                    negatives=0,
                    positive_movie_ids=[],
                    file_name=f"{sha}.npz",
                )
            )
            await session.commit()
    result = await handler.taste_rebuild(
        _context(await _job(sessions), {"revision": ids["revision"]}, _fake_child([]))
    )
    kept = sorted(p.name for p in settings.profiles_dir.iterdir())
    assert len(result["pruned"]) == 2  # the two oldest rebuilds
    assert f"{seed.sha256}.npz" in kept and f"{result['sha256']}.npz" in kept
    assert len(kept) == 6  # the seed and the five newest


async def test_a_training_that_fails_never_fails_the_rebuild(
    sessions: async_sessionmaker[AsyncSession], _scratch: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from marquee.taste import residuals

    ids = await _records(sessions)
    await _seed_profile(sessions, _scratch)

    async def broken(session: AsyncSession, *, job_id: int | None = None) -> Any:
        raise RuntimeError("the database went away")

    monkeypatch.setattr(residuals, "train", broken)
    result = await handler.taste_rebuild(
        _context(
            await _job(sessions), {"revision": ids["revision"], "force": False}, _fake_child([])
        )
    )
    assert result["skipped"] is None and result["built"] is True
    assert result["residual"]["outcome"] == "failed"
    assert "the database went away" in result["residual"]["error"]
    async with sessions() as session:
        active = await rebuild.active_profile(session)
    assert active is not None and active.id == result["profile_id"]


async def test_a_skipped_rebuild_trains_nothing(
    sessions: async_sessionmaker[AsyncSession], _scratch: Path
) -> None:
    ids = await _records(sessions)
    result = await handler.taste_rebuild(
        _context(await _job(sessions), {"revision": "f" * 64, "force": False}, None)
    )
    assert result["skipped"] == "superseded" and "residual" not in result
    async with sessions() as session:
        assert await session.scalar(select(ResidualTraining)) is None
    assert ids
