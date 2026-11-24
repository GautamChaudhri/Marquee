"""The poster_deploy and poster_restore handlers, and the deploy policy, against a real
PostgreSQL server and a movie folder in tmp_path. TMDB is faked."""

from __future__ import annotations

import io
import threading
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

import pytest
from PIL import Image
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from marquee.artwork import backup, deploy
from marquee.config import settings
from marquee.db import engine as db_engine_module
from marquee.db.engine import create_session_factory
from marquee.db.models import Artwork, Deployment, Job, Movie, PosterRun, RunCandidate
from marquee.jobs.handlers import poster_write as handler
from marquee.jobs.registry import JobContext, PermanentJobError
from marquee.library.sync import stat_poster

pytestmark = pytest.mark.db
RADARR_ROOT = PurePosixPath("/radarr/movies")


def jpeg(colour: tuple[int, int, int]) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (500, 750), colour).save(out, format="JPEG")
    return out.getvalue()


OWNERS = jpeg((10, 10, 10))
PICK = jpeg((250, 0, 0))
OTHER = jpeg((0, 0, 250))


@pytest.fixture
def library(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, database_url: str) -> Path:
    root = tmp_path / "movies"
    folder = root / "4K" / "Heat (1995)"
    folder.mkdir(parents=True)
    monkeypatch.setattr(settings, "MOVIE_ROOTS", [root])
    monkeypatch.setattr(settings, "RADARR_MOVIE_ROOTS", [RADARR_ROOT])
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path / "data")
    real = db_engine_module.create_engine
    monkeypatch.setattr(handler, "create_engine", lambda url=None: real(database_url))
    return folder


@pytest.fixture
def fetched(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []
    files = {"pick.jpg": PICK, "other.jpg": OTHER}

    async def fake_fetch(source_file: str, client: object = None) -> bytes:
        calls.append(source_file)
        return files[source_file]

    monkeypatch.setattr(deploy, "fetch_original", fake_fetch)
    return calls


@pytest.fixture
def sessions(db_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return create_session_factory(db_engine)


async def _setup(
    sessions: async_sessionmaker[AsyncSession], folder: Path, *, owners_poster: bool
) -> dict[str, int]:
    """A movie, optionally its owner's poster and row, and a completed run of three
    candidates: the pick, another downloaded one, and one never downloaded."""
    now = datetime.now(UTC)
    async with sessions() as session:
        movie = Movie(
            radarr_id=1,
            title="Heat",
            sort_title="heat",
            year=1995,
            has_file=True,
            is_present=True,
            radarr_path=str(RADARR_ROOT / "4K" / "Heat (1995)"),
            last_seen_at=now,
        )
        session.add(movie)
        await session.flush()
        if owners_poster:
            (folder / "poster.jpg").write_bytes(OWNERS)
            found = stat_poster(folder / "poster.jpg")
            assert found is not None
            session.add(
                Artwork(
                    movie_id=movie.id,
                    artwork_kind="poster",
                    file_name="poster.jpg",
                    sha256=backup.sha256_of(OWNERS),
                    size_bytes=found.size_bytes,
                    file_modified_at=found.modified_at,
                    origin="library",
                    first_seen_at=now,
                    last_seen_at=now,
                )
            )
        run = PosterRun(
            movie_id=movie.id, state="completed", run_folder="00000001-heat", started_at=now
        )
        session.add(run)
        await session.flush()
        pick = RunCandidate(
            run_id=run.id, source_file="pick.jpg", sha256="1" * 64, rank=1, is_pick=True
        )
        other = RunCandidate(run_id=run.id, source_file="other.jpg", sha256="2" * 64, rank=2)
        never = RunCandidate(run_id=run.id, source_file="never.jpg", sha256=None)
        session.add_all([pick, other, never])
        await session.commit()
        return {
            "movie": movie.id,
            "run": run.id,
            "pick": pick.id,
            "other": other.id,
            "never": never.id,
        }


_next_job = iter(range(100, 1000))


async def _job(sessions: async_sessionmaker[AsyncSession], kind: str) -> int:
    job_id = next(_next_job)
    async with sessions() as session:
        session.add(Job(id=job_id, kind=kind, job_class="media_write", state="running"))
        await session.commit()
    return job_id


def _ctx(job_id: int, payload: dict[str, Any]) -> JobContext:
    return JobContext(job_id, payload, threading.Event(), lambda *_: None)


async def _deploy(
    sessions: async_sessionmaker[AsyncSession],
    ids: dict[str, int],
    which: str = "pick",
    by: str = "owner",
) -> dict[str, Any]:
    job_id = await _job(sessions, "poster_deploy")
    payload = {"candidate_id": ids[which], "initiated_by": by, "movie_id": ids["movie"]}
    return await handler.poster_deploy(_ctx(job_id, payload))


async def _restore(
    sessions: async_sessionmaker[AsyncSession], ids: dict[str, int], deployment_id: int
) -> dict[str, Any]:
    job_id = await _job(sessions, "poster_restore")
    return await handler.poster_restore(
        _ctx(job_id, {"deployment_id": deployment_id, "movie_id": ids["movie"]})
    )


async def _row(sessions: async_sessionmaker[AsyncSession]) -> Artwork | None:
    async with sessions() as session:
        return await session.scalar(select(Artwork))


async def _deployment_count(sessions: async_sessionmaker[AsyncSession]) -> int:
    async with sessions() as session:
        return int(await session.scalar(select(func.count()).select_from(Deployment)) or 0)


async def test_deploy_then_restore_puts_the_owners_poster_back_byte_for_byte(
    library: Path, sessions: async_sessionmaker[AsyncSession], fetched: list[str]
) -> None:
    ids = await _setup(sessions, library, owners_poster=True)

    deployed = await _deploy(sessions, ids)

    assert deployed["outcome"] == "written" and fetched == ["pick.jpg"]
    assert (library / "poster.jpg").read_bytes() == PICK
    assert (await _row(sessions)).origin == "marquee"  # type: ignore[union-attr]
    assert (settings.backups_dir / deployed["backup_file"]).read_bytes() == OWNERS

    restored = await _restore(sessions, ids, deployed["deployment_id"])

    assert restored["outcome"] == "written"
    assert (library / "poster.jpg").read_bytes() == OWNERS
    row = await _row(sessions)
    assert row is not None and (row.origin, row.sha256) == ("library", backup.sha256_of(OWNERS))
    # The deployed poster was backed up before the restore overwrote it.
    assert (settings.backups_dir / restored["backup_file"]).read_bytes() == PICK
    assert not list(settings.deploy_staging_dir.glob("*.jpg"))


async def test_the_same_deploy_twice_writes_once_and_fetches_once(
    library: Path, sessions: async_sessionmaker[AsyncSession], fetched: list[str]
) -> None:
    ids = await _setup(sessions, library, owners_poster=True)
    first = await _deploy(sessions, ids)

    second = await _deploy(sessions, ids)

    assert second["outcome"] == "already_deployed"
    assert second["deployment_id"] == first["deployment_id"]
    assert fetched == ["pick.jpg"]
    assert await _deployment_count(sessions) == 1
    assert backup.summary() == (1, len(OWNERS))


async def test_a_retry_writes_the_bytes_the_first_attempt_fetched(
    library: Path,
    sessions: async_sessionmaker[AsyncSession],
    fetched: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ids = await _setup(sessions, library, owners_poster=False)
    job_id = await _job(sessions, "poster_deploy")
    payload = {"candidate_id": ids["pick"], "initiated_by": "owner", "movie_id": ids["movie"]}
    real_write = deploy.write_poster

    async def dies(*args: Any, **kwargs: Any) -> None:
        raise OSError("the share went away")

    monkeypatch.setattr(deploy, "write_poster", dies)
    with pytest.raises(OSError):
        await handler.poster_deploy(_ctx(job_id, payload))
    assert (settings.deploy_staging_dir / f"{job_id}.jpg").read_bytes() == PICK

    # TMDB would now serve other bytes under the same name; the retry must not ask.
    async def other_bytes(source_file: str, client: object = None) -> bytes:
        raise AssertionError("a retry must reuse the bytes it already fetched")

    monkeypatch.setattr(deploy, "fetch_original", other_bytes)
    monkeypatch.setattr(deploy, "write_poster", real_write)
    result = await handler.poster_deploy(_ctx(job_id, payload))

    assert result["outcome"] == "written"
    assert (library / "poster.jpg").read_bytes() == PICK
    assert not (settings.deploy_staging_dir / f"{job_id}.jpg").exists()


async def test_the_owner_may_deploy_any_downloaded_candidate(
    library: Path, sessions: async_sessionmaker[AsyncSession], fetched: list[str]
) -> None:
    ids = await _setup(sessions, library, owners_poster=True)
    assert (await _deploy(sessions, ids, "other"))["outcome"] == "written"
    assert (library / "poster.jpg").read_bytes() == OTHER
    with pytest.raises(PermanentJobError, match="never downloaded"):
        await _deploy(sessions, ids, "never")


async def test_the_policy_deploys_only_the_pick_and_checks_the_policy_again(
    library: Path,
    sessions: async_sessionmaker[AsyncSession],
    fetched: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ids = await _setup(sessions, library, owners_poster=True)
    monkeypatch.setattr(settings, "DEPLOY_POLICY", "all")
    with pytest.raises(PermanentJobError, match="only the run's pick"):
        await _deploy(sessions, ids, "other", by="policy")

    monkeypatch.setattr(settings, "DEPLOY_POLICY", "off")
    off = await _deploy(sessions, ids, by="policy")
    assert off["skipped"] is True and "off" in off["note"]

    monkeypatch.setattr(settings, "DEPLOY_POLICY", "missing")
    owners = await _deploy(sessions, ids, by="policy")
    assert owners["skipped"] is True and "not Marquee's" in owners["note"]
    assert (library / "poster.jpg").read_bytes() == OWNERS
    assert await _deployment_count(sessions) == 0


async def test_missing_writes_into_an_empty_folder_but_not_over_a_late_poster(
    library: Path,
    sessions: async_sessionmaker[AsyncSession],
    fetched: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ids = await _setup(sessions, library, owners_poster=False)
    monkeypatch.setattr(settings, "DEPLOY_POLICY", "missing")
    (library / "poster.jpg").write_bytes(OWNERS)  # dropped in after the last sync

    late = await _deploy(sessions, ids, by="policy")
    assert late["skipped"] is True and (library / "poster.jpg").read_bytes() == OWNERS

    (library / "poster.jpg").unlink()
    assert (await _deploy(sessions, ids, by="policy"))["outcome"] == "written"
    # Marquee's own poster may be replaced under missing.
    assert (await _deploy(sessions, ids, "other", by="owner"))["outcome"] == "written"


async def test_any_deployment_can_be_restored_and_a_restore_can_be_undone(
    library: Path, sessions: async_sessionmaker[AsyncSession], fetched: list[str]
) -> None:
    ids = await _setup(sessions, library, owners_poster=True)
    first = await _deploy(sessions, ids, "other")
    second = await _deploy(sessions, ids, "pick")
    assert (library / "poster.jpg").read_bytes() == PICK

    # Restoring the older deployment puts back what was there before it: the owner's.
    restored = await _restore(sessions, ids, first["deployment_id"])
    assert restored["outcome"] == "written"
    assert (library / "poster.jpg").read_bytes() == OWNERS
    assert (await _row(sessions)).origin == "library"  # type: ignore[union-attr]
    async with sessions() as session:
        row = await session.get(Deployment, restored["deployment_id"])
    assert row is not None and row.restores_deployment_id == first["deployment_id"]
    assert row.previous_sha256 == backup.sha256_of(PICK) == second["new_sha256"]
    assert backup.exists(ids["movie"], backup.sha256_of(PICK))

    # The same restore again, in a row, writes nothing.
    count = await _deployment_count(sessions)
    again = await _restore(sessions, ids, first["deployment_id"])
    assert again["outcome"] == "already_deployed"
    assert again["deployment_id"] == restored["deployment_id"]
    assert await _deployment_count(sessions) == count

    # A restore is a deployment too: undoing it puts the pick back.
    undone = await _restore(sessions, ids, restored["deployment_id"])
    assert undone["outcome"] == "written"
    assert (library / "poster.jpg").read_bytes() == PICK
    assert (await _row(sessions)).origin == "marquee"  # type: ignore[union-attr]


async def test_restoring_a_deploy_into_an_empty_folder_leaves_no_poster(
    library: Path, sessions: async_sessionmaker[AsyncSession], fetched: list[str]
) -> None:
    ids = await _setup(sessions, library, owners_poster=False)
    deployed = await _deploy(sessions, ids)

    restored = await _restore(sessions, ids, deployed["deployment_id"])

    assert restored["outcome"] == "removed"
    assert not (library / "poster.jpg").exists()
    assert await _row(sessions) is None


async def _sync_posters(sessions: async_sessionmaker[AsyncSession]) -> None:
    """The poster part of a library sync, exactly as `marquee sync` runs it."""
    from marquee.fs.boundary import boundary_for_roots
    from marquee.fs.paths import PathMapper
    from marquee.library import sync
    from marquee.library.folders import scan_movie_roots

    roots = list(settings.MOVIE_ROOTS)
    async with sessions() as session:
        listed = {movie.id: movie for movie in await session.scalars(select(Movie))}
        await sync._sync_posters(
            session,
            listed,
            scan_movie_roots(roots),
            PathMapper.from_settings(),
            boundary_for_roots({f"movies{i}": r for i, r in enumerate(roots)}, access="read"),
            datetime.now(UTC),
            sync.SyncReport(dry_run=False),
            None,
        )
        await session.commit()


async def test_a_retry_after_a_sync_takes_the_previous_poster_from_the_intent(
    library: Path,
    sessions: async_sessionmaker[AsyncSession],
    fetched: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Attempt one stops after the rename, a sync runs, attempt two records the owner's
    poster as the previous one, with origin library and its backup. Restores likewise."""
    ids = await _setup(sessions, library, owners_poster=True)
    real_record = deploy._record

    async def died_after_the_rename(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("the worker died after the rename")

    monkeypatch.setattr(deploy, "_record", died_after_the_rename)
    job_id = await _job(sessions, "poster_deploy")
    payload = {"candidate_id": ids["pick"], "initiated_by": "owner", "movie_id": ids["movie"]}
    with pytest.raises(RuntimeError):
        await handler.poster_deploy(_ctx(job_id, payload))
    assert (library / "poster.jpg").read_bytes() == PICK
    assert deploy.read_intent(job_id) == deploy.Intent(
        job_id=job_id,
        movie_id=ids["movie"],
        new_sha256=backup.sha256_of(PICK),
        previous_sha256=backup.sha256_of(OWNERS),
        previous_origin="library",
        backup_file=backup.backup_key(ids["movie"], backup.sha256_of(OWNERS)),
    )

    await _sync_posters(sessions)  # it records what it finds: the new bytes, as the library's
    row = await _row(sessions)
    assert row is not None and (row.sha256, row.origin) == (backup.sha256_of(PICK), "library")

    monkeypatch.setattr(deploy, "_record", real_record)
    result = await handler.poster_deploy(_ctx(job_id, payload))

    assert result["outcome"] == "already_in_place"
    async with sessions() as session:
        deployed = await session.get(Deployment, result["deployment_id"])
    assert deployed is not None
    assert deployed.previous_sha256 == backup.sha256_of(OWNERS)
    assert deployed.previous_origin == "library"
    assert deployed.backup_file == backup.backup_key(ids["movie"], backup.sha256_of(OWNERS))
    assert backup.verify(deployed.backup_file, backup.sha256_of(OWNERS)) == OWNERS
    assert (await _row(sessions)).origin == "marquee"  # type: ignore[union-attr]
    assert not deploy.intent_path(job_id).exists()

    # A restore keeps its intent too: stopped after the rename, a sync, then the retry.
    monkeypatch.setattr(deploy, "_record", died_after_the_rename)
    restore_job = await _job(sessions, "poster_restore")
    restore = {"deployment_id": deployed.id, "movie_id": ids["movie"]}
    with pytest.raises(RuntimeError):
        await handler.poster_restore(_ctx(restore_job, restore))
    await _sync_posters(sessions)  # the owner's bytes over Marquee's: "replaced by hand"
    monkeypatch.setattr(deploy, "_record", real_record)
    restored = await handler.poster_restore(_ctx(restore_job, restore))

    assert (library / "poster.jpg").read_bytes() == OWNERS
    assert (restored["previous_sha256"], restored["previous_origin"]) == (
        backup.sha256_of(PICK),
        "marquee",
    )
    assert (await _row(sessions)).origin == "library"  # type: ignore[union-attr]
    assert not deploy.intent_path(restore_job).exists()


async def test_payloads_and_refusals(
    library: Path, sessions: async_sessionmaker[AsyncSession], fetched: list[str]
) -> None:
    ids = await _setup(sessions, library, owners_poster=False)
    with pytest.raises(PermanentJobError, match="candidate_id"):
        await handler.poster_deploy(_ctx(1, {"movie_id": 1, "initiated_by": "owner"}))
    with pytest.raises(PermanentJobError, match="initiated_by"):
        await handler.poster_deploy(
            _ctx(1, {"candidate_id": ids["pick"], "movie_id": ids["movie"]})
        )
    with pytest.raises(PermanentJobError, match="not movie 999's"):
        await handler.poster_deploy(
            _ctx(1, {"candidate_id": ids["pick"], "movie_id": 999, "initiated_by": "owner"})
        )
    with pytest.raises(PermanentJobError, match="no deployment 999"):
        await handler.poster_restore(_ctx(1, {"deployment_id": 999, "movie_id": ids["movie"]}))
    async with sessions() as session:
        await session.execute(PosterRun.__table__.update().values(state="failed"))
        await session.commit()
    with pytest.raises(PermanentJobError, match="only a completed run's candidate"):
        await _deploy(sessions, ids)


async def test_queueing_writes(
    library: Path, sessions: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    ids = await _setup(sessions, library, owners_poster=True)
    async with sessions() as session:
        job, created = await handler.enqueue_deploy(session, ids["other"])
        assert created and job.payload == {
            "candidate_id": ids["other"],
            "initiated_by": "owner",
            "movie_id": ids["movie"],
        }
        assert job.dedupe_key == f"poster_write:{ids['movie']}"
        same, created_again = await handler.enqueue_deploy(session, ids["pick"])
        assert not created_again and same.id == job.id  # one write per movie at a time
        with pytest.raises(handler.WriteRefusedError, match="never downloaded"):
            await handler.enqueue_deploy(session, ids["never"])
        with pytest.raises(handler.WriteRefusedError, match="no candidate"):
            await handler.enqueue_deploy(session, 999)
        with pytest.raises(handler.WriteRefusedError, match="only the policy|policy deploys only"):
            await handler.enqueue_deploy(session, ids["other"], initiated_by="policy")


@pytest.mark.parametrize(
    ("policy", "owners_poster", "queued"),
    [("off", False, None), ("missing", True, False), ("missing", False, True), ("all", True, True)],
)
async def test_the_policy_after_a_run(
    library: Path,
    sessions: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
    policy: str,
    owners_poster: bool,
    queued: bool | None,
) -> None:
    ids = await _setup(sessions, library, owners_poster=owners_poster)
    monkeypatch.setattr(settings, "DEPLOY_POLICY", policy)
    async with sessions() as session:
        result = await handler.queue_policy_deploy(session, ids["movie"], ids["pick"])
    if queued is None:
        assert result is None
    else:
        assert result is not None and result["queued"] is queued
    async with sessions() as session:
        kinds = list(await session.scalars(select(Job.payload).where(Job.kind == "poster_deploy")))
    assert kinds == (
        [{"candidate_id": ids["pick"], "initiated_by": "policy", "movie_id": ids["movie"]}]
        if queued
        else []
    )
