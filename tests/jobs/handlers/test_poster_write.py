"""The poster_deploy and poster_restore handlers, and the deploy policy, against a real
PostgreSQL server and a movie folder in tmp_path. TMDB is faked."""

from __future__ import annotations

import io
import threading
from datetime import UTC, datetime, timedelta
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
LATER = jpeg((0, 250, 0))


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
    files = {"pick.jpg": PICK, "other.jpg": OTHER, "later.jpg": LATER}

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


def _poster_is_a_symlink(folder: Path) -> None:
    (folder.parent / "elsewhere.jpg").write_bytes(OWNERS)
    (folder / "poster.jpg").symlink_to(folder.parent / "elsewhere.jpg")


def _poster_is_a_folder(folder: Path) -> None:
    (folder / "poster.jpg").mkdir()


def _folder_is_missing(folder: Path) -> None:
    folder.rmdir()


def _folder_is_a_symlink(folder: Path) -> None:
    real = folder.parent.parent / "real-heat"
    real.mkdir()
    folder.rmdir()
    folder.symlink_to(real)


@pytest.mark.parametrize(
    ("break_it", "message"),
    [
        (_poster_is_a_symlink, "symbolic link"),
        (_poster_is_a_folder, "not a regular file"),
        (_folder_is_missing, "missing or not a plain folder"),
        (_folder_is_a_symlink, "missing or not a plain folder"),
    ],
)
async def test_a_refusal_no_retry_can_fix_fails_at_once(
    library: Path,
    sessions: async_sessionmaker[AsyncSession],
    fetched: list[str],
    break_it: Any,
    message: str,
) -> None:
    ids = await _setup(sessions, library, owners_poster=False)
    deployed = await _deploy(sessions, ids, "other")
    (library / "poster.jpg").unlink()
    break_it(library)

    with pytest.raises(PermanentJobError, match=message):
        await _deploy(sessions, ids, "pick")
    with pytest.raises(PermanentJobError, match=message):
        await _restore(sessions, ids, deployed["deployment_id"])


async def test_a_path_outside_the_roots_fails_at_once(
    library: Path, sessions: async_sessionmaker[AsyncSession], fetched: list[str]
) -> None:
    ids = await _setup(sessions, library, owners_poster=False)
    async with sessions() as session:
        await session.execute(Movie.__table__.update().values(radarr_path="/elsewhere/Heat (1995)"))
        await session.commit()
    with pytest.raises(PermanentJobError, match="no movie root covers"):
        await _deploy(sessions, ids)


async def test_a_read_back_mismatch_and_an_io_error_still_retry(
    library: Path,
    sessions: async_sessionmaker[AsyncSession],
    fetched: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ids = await _setup(sessions, library, owners_poster=True)
    real_write = deploy.write_file

    def wrong_bytes(target: deploy.Target, data: bytes, mode: int) -> None:
        real_write(target, OTHER if data == PICK else data, mode)

    monkeypatch.setattr(deploy, "write_file", wrong_bytes)
    with pytest.raises(deploy.VerificationError) as mismatch:
        await _deploy(sessions, ids)
    assert not isinstance(mismatch.value, PermanentJobError)
    assert (library / "poster.jpg").read_bytes() == OWNERS

    def disk_error(target: deploy.Target, data: bytes, mode: int) -> None:
        raise OSError(5, "Input/output error")

    monkeypatch.setattr(deploy, "write_file", disk_error)
    with pytest.raises(OSError, match="Input/output error"):
        await _deploy(sessions, ids)
    assert (library / "poster.jpg").read_bytes() == OWNERS


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
        assert job.dedupe_key is None
        with pytest.raises(handler.WriteRefusedError, match="never downloaded"):
            await handler.enqueue_deploy(session, ids["never"])
        with pytest.raises(handler.WriteRefusedError, match="no candidate"):
            await handler.enqueue_deploy(session, 999)
        with pytest.raises(handler.WriteRefusedError, match="only the policy|policy deploys only"):
            await handler.enqueue_deploy(session, ids["other"], initiated_by="policy")


async def test_an_identical_write_request_is_not_queued_twice(
    library: Path, sessions: async_sessionmaker[AsyncSession]
) -> None:
    ids = await _setup(sessions, library, owners_poster=True)
    async with sessions() as session:
        first, created = await handler.enqueue_deploy(session, ids["other"])
        same, created_again = await handler.enqueue_deploy(session, ids["other"])
        assert created and not created_again and same.id == first.id
        # Another request for the movie is always queued: the newest one wins.
        newer, newer_created = await handler.enqueue_deploy(session, ids["pick"])
        assert newer_created and newer.id > first.id
        # The first request again is newer than both, so it is queued again too.
        again, again_created = await handler.enqueue_deploy(session, ids["other"])
        assert again_created and again.id > newer.id
        repeated, repeated_created = await handler.enqueue_deploy(session, ids["other"])
        assert not repeated_created and repeated.id == again.id


async def _queued_write(
    sessions: async_sessionmaker[AsyncSession], ids: dict[str, int], which: str
) -> Job:
    async with sessions() as session:
        job, created = await handler.enqueue_deploy(session, ids[which])
    assert created
    return job


async def _run_write(job: Job) -> dict[str, Any]:
    run = handler.poster_deploy if job.kind == "poster_deploy" else handler.poster_restore
    return await run(_ctx(job.id, dict(job.payload)))


async def _set_state(sessions: async_sessionmaker[AsyncSession], job_id: int, state: str) -> None:
    async with sessions() as session:
        await session.execute(Job.__table__.update().where(Job.id == job_id).values(state=state))
        await session.commit()


async def test_an_older_write_retried_after_a_newer_one_succeeded_writes_nothing(
    library: Path, sessions: async_sessionmaker[AsyncSession], fetched: list[str]
) -> None:
    ids = await _setup(sessions, library, owners_poster=True)
    older = await _queued_write(sessions, ids, "other")
    newer = await _queued_write(sessions, ids, "pick")
    assert (await _run_write(newer))["outcome"] == "written"
    await _set_state(sessions, newer.id, "succeeded")

    result = await _run_write(older)  # its retry, after the newer one succeeded

    assert result["skipped"] == f"superseded by job {newer.id}"
    assert result["superseded_by"] == newer.id
    assert (library / "poster.jpg").read_bytes() == PICK
    assert fetched == ["pick.jpg"]
    assert await _deployment_count(sessions) == 1


async def test_a_newer_write_that_was_cancelled_does_not_supersede(
    library: Path, sessions: async_sessionmaker[AsyncSession], fetched: list[str]
) -> None:
    ids = await _setup(sessions, library, owners_poster=True)
    older = await _queued_write(sessions, ids, "other")
    newer = await _queued_write(sessions, ids, "pick")
    await _set_state(sessions, newer.id, "cancelled")

    assert (await _run_write(older))["outcome"] == "written"
    assert (library / "poster.jpg").read_bytes() == OTHER


async def test_a_write_that_stopped_after_its_rename_is_recorded_before_it_steps_aside(
    library: Path,
    sessions: async_sessionmaker[AsyncSession],
    fetched: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An attempt that renamed its file into place must record it, even when a newer write
    is waiting: otherwise the folder would hold a poster no row describes."""
    ids = await _setup(sessions, library, owners_poster=True)
    older = await _queued_write(sessions, ids, "other")
    real_record = deploy._record

    async def died_after_the_rename(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("the worker died after the rename")

    monkeypatch.setattr(deploy, "_record", died_after_the_rename)
    with pytest.raises(RuntimeError):
        await _run_write(older)
    monkeypatch.setattr(deploy, "_record", real_record)
    newer = await _queued_write(sessions, ids, "pick")

    assert (await _run_write(older))["outcome"] == "already_in_place"
    assert (await _run_write(newer))["outcome"] == "written"
    assert (library / "poster.jpg").read_bytes() == PICK
    assert await _deployment_count(sessions) == 2


# ── The owner beats the policy ────────────────────────────────────────────


async def _queued_policy(sessions: async_sessionmaker[AsyncSession], ids: dict[str, int]) -> Job:
    async with sessions() as session:
        job, created = await handler.enqueue_deploy(session, ids["pick"], initiated_by="policy")
    assert created
    return job


async def _finish(
    sessions: async_sessionmaker[AsyncSession], job_id: int, *, ago_s: float = 0
) -> None:
    """Mark a job succeeded, as the queue would, finished ``ago_s`` seconds ago."""
    async with sessions() as session:
        await session.execute(
            Job.__table__.update()
            .where(Job.id == job_id)
            .values(state="succeeded", finished_at=func.now() - timedelta(seconds=ago_s))
        )
        await session.commit()


async def _created_ago(
    sessions: async_sessionmaker[AsyncSession], job_id: int, ago_s: float
) -> None:
    async with sessions() as session:
        await session.execute(
            Job.__table__.update()
            .where(Job.id == job_id)
            .values(created_at=func.now() - timedelta(seconds=ago_s))
        )
        await session.commit()


async def test_an_owners_pick_queued_before_a_policy_deploy_is_written_and_kept(
    library: Path,
    sessions: async_sessionmaker[AsyncSession],
    fetched: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "DEPLOY_POLICY", "all")
    ids = await _setup(sessions, library, owners_poster=True)
    owners = await _queued_write(sessions, ids, "other")
    policy = await _queued_policy(sessions, ids)

    # The worker claims the older job first: the owner's, which must not step aside.
    assert (await _run_write(owners))["outcome"] == "written"
    await _finish(sessions, owners.id)
    # Then the policy's, which must not write over the owner's choice.
    result = await _run_write(policy)

    assert result["skipped"] == f"superseded by job {owners.id}"
    assert (library / "poster.jpg").read_bytes() == OTHER
    assert fetched == ["other.jpg"]
    assert await _deployment_count(sessions) == 1


async def test_an_owners_pick_queued_after_a_policy_deploy_wins(
    library: Path,
    sessions: async_sessionmaker[AsyncSession],
    fetched: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "DEPLOY_POLICY", "all")
    ids = await _setup(sessions, library, owners_poster=True)
    policy = await _queued_policy(sessions, ids)
    owners = await _queued_write(sessions, ids, "other")

    assert (await _run_write(policy))["skipped"] == f"superseded by job {owners.id}"
    assert (await _run_write(owners))["outcome"] == "written"
    assert (library / "poster.jpg").read_bytes() == OTHER
    assert fetched == ["other.jpg"]


async def test_of_two_owners_requests_the_newer_wins(
    library: Path, sessions: async_sessionmaker[AsyncSession], fetched: list[str]
) -> None:
    ids = await _setup(sessions, library, owners_poster=True)
    older = await _queued_write(sessions, ids, "other")
    newer = await _queued_write(sessions, ids, "pick")

    assert (await _run_write(older))["skipped"] == f"superseded by job {newer.id}"
    assert (await _run_write(newer))["outcome"] == "written"
    assert (library / "poster.jpg").read_bytes() == PICK


async def test_a_retried_policy_deploy_steps_aside_for_an_owners_waiting_write(
    library: Path,
    sessions: async_sessionmaker[AsyncSession],
    fetched: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An owner's write that is older but still queued (waiting for its own retry) or
    running beats a policy deploy, whatever their ids. So does a restore, always the
    owner's."""
    monkeypatch.setattr(settings, "DEPLOY_POLICY", "all")
    ids = await _setup(sessions, library, owners_poster=True)
    owners = await _queued_write(sessions, ids, "other")  # queued, waiting for a retry
    policy = await _queued_policy(sessions, ids)

    assert (await _run_write(policy))["skipped"] == f"superseded by job {owners.id}"
    await _set_state(sessions, owners.id, "running")
    assert (await _run_write(policy))["skipped"] == f"superseded by job {owners.id}"
    await _set_state(sessions, owners.id, "failed")
    # With the owner's write failed for good, the policy's retry goes ahead.
    assert (await _run_write(policy))["outcome"] == "written"
    assert (library / "poster.jpg").read_bytes() == PICK

    async with sessions() as session:
        deployment = await session.scalar(select(Deployment.id))
        restore, _ = await handler.enqueue_restore(session, deployment)
    again = await _queued_policy(sessions, ids)
    assert (await _run_write(again))["skipped"] == f"superseded by job {restore.id}"


async def test_a_policy_deploy_queued_after_the_owners_write_finished_still_deploys(
    library: Path,
    sessions: async_sessionmaker[AsyncSession],
    fetched: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The owner's write beats a policy deploy it overlapped, not every later one: a policy
    deploy queued after it had finished (a later run's) still deploys under the policy."""
    monkeypatch.setattr(settings, "DEPLOY_POLICY", "all")
    ids = await _setup(sessions, library, owners_poster=True)
    owners = await _queued_write(sessions, ids, "other")
    assert (await _run_write(owners))["outcome"] == "written"
    await _finish(sessions, owners.id, ago_s=60)
    policy = await _queued_policy(sessions, ids)

    assert (await _run_write(policy))["outcome"] == "written"
    assert (library / "poster.jpg").read_bytes() == PICK
    await _finish(sessions, policy.id)

    # A policy deploy queued before the owner's write finished, retried later, steps aside.
    late = await _queued_policy(sessions, ids)
    await _created_ago(sessions, late.id, 120)
    assert (await _run_write(late))["skipped"] == f"superseded by job {owners.id}"


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


async def _later_pick(sessions: async_sessionmaker[AsyncSession], ids: dict[str, int]) -> int:
    """A later completed run of the movie whose pick is later.jpg; returns the pick's id."""
    async with sessions() as session:
        run = PosterRun(
            movie_id=ids["movie"],
            state="completed",
            run_folder="00000002-heat",
            started_at=datetime.now(UTC),
        )
        session.add(run)
        await session.flush()
        pick = RunCandidate(
            run_id=run.id, source_file="later.jpg", sha256="3" * 64, rank=1, is_pick=True
        )
        session.add(pick)
        await session.commit()
        return pick.id


async def _as_the_sync_records(sessions: async_sessionmaker[AsyncSession], folder: Path) -> None:
    """What the next sync does to the poster row after the owner replaced the file by hand."""
    found = stat_poster(folder / "poster.jpg")
    assert found is not None
    async with sessions() as session:
        row = await session.scalar(select(Artwork))
        assert row is not None
        row.origin = "library"
        row.sha256 = backup.sha256_of((folder / "poster.jpg").read_bytes())
        row.size_bytes = found.size_bytes
        row.file_modified_at = found.modified_at
        await session.commit()


MISSING_CASES = [
    "no poster",
    "the owner's own poster",
    "a policy deploy",
    "an owner's deploy",
    "a policy deploy the owner replaced by hand, before the sync",
    "a policy deploy the owner replaced by hand, after the sync",
    "a restore of a policy deploy over the owner's poster",
    "a restore of a policy deploy into an empty folder",
    "a policy deploy that found the owner's own file in place",
]


@pytest.mark.parametrize("case", MISSING_CASES)
async def test_missing_replaces_only_what_the_policy_itself_deployed(
    library: Path,
    sessions: async_sessionmaker[AsyncSession],
    fetched: list[str],
    monkeypatch: pytest.MonkeyPatch,
    case: str,
) -> None:
    """DEPLOY_POLICY=missing writes into a folder with no poster, or over the file the
    policy itself deployed last, and nowhere else: checked when the deploy is queued and
    again at the write, against the folder itself."""
    poster = library / "poster.jpg"
    ids = await _setup(
        sessions,
        library,
        owners_poster=case.startswith(
            (
                "the owner's own",
                "an owner's",
                "a restore of a policy deploy over",
                "a policy deploy that found",
            )
        ),
    )
    monkeypatch.setattr(settings, "DEPLOY_POLICY", "missing")
    if case == "a policy deploy that found the owner's own file in place":
        fetched_files = {"pick.jpg": OWNERS, "later.jpg": LATER}

        async def owners_file_is_the_pick(source_file: str, client: object = None) -> bytes:
            return fetched_files[source_file]

        monkeypatch.setattr(deploy, "fetch_original", owners_file_is_the_pick)
        found = await _deploy(sessions, ids, by="policy")
        assert found["outcome"] == "already_in_place" and poster.read_bytes() == OWNERS
    elif case.startswith("a policy deploy") or case.endswith("into an empty folder"):
        assert (await _deploy(sessions, ids, by="policy"))["outcome"] == "written"
    elif case == "an owner's deploy":
        assert (await _deploy(sessions, ids, "other", by="owner"))["outcome"] == "written"
    elif case.startswith("a restore of a policy deploy over"):
        monkeypatch.setattr(settings, "DEPLOY_POLICY", "all")
        deployed = await _deploy(sessions, ids, by="policy")
        assert deployed["outcome"] == "written"
        monkeypatch.setattr(settings, "DEPLOY_POLICY", "missing")
    if case.startswith("a restore"):
        if case.endswith("into an empty folder"):
            deployed = {"deployment_id": await _newest_deployment_id(sessions)}
        assert (await _restore(sessions, ids, deployed["deployment_id"]))["outcome"] in (
            "written",
            "removed",
        )
    if case.startswith("a policy deploy the owner replaced by hand"):
        poster.write_bytes(OWNERS)
        if case.endswith("after the sync"):
            await _as_the_sync_records(sessions, library)
    before = poster.read_bytes() if poster.exists() else None
    may_replace = case in (
        "no poster",
        "a policy deploy",
        "a restore of a policy deploy into an empty folder",
    )
    # Queued after the next run, unless the folder visibly holds a poster the policy does not
    # own; replaced by hand but not yet synced, the row still says the policy's, so it is
    # queued and the write refuses.
    queued_too = may_replace or case.endswith("before the sync")
    later = await _later_pick(sessions, ids)

    async with sessions() as session:
        queued = await handler.queue_policy_deploy(session, ids["movie"], later)
    assert queued is not None and queued["queued"] is queued_too, queued

    job_id = await _job(sessions, "poster_deploy")
    payload = {"candidate_id": later, "initiated_by": "policy", "movie_id": ids["movie"]}
    written = await handler.poster_deploy(_ctx(job_id, payload))
    if may_replace:
        assert written["outcome"] == "written"
        assert poster.read_bytes() == LATER
    else:
        assert written["skipped"] is True and "not Marquee's to replace" in written["note"]
        assert (poster.read_bytes() if poster.exists() else None) == before
        assert before is not None


async def _newest_deployment_id(sessions: async_sessionmaker[AsyncSession]) -> int:
    async with sessions() as session:
        newest = await session.scalar(select(func.max(Deployment.id)))
    assert newest is not None
    return int(newest)


async def test_nothing_is_deployed_from_a_masked_run(
    library: Path,
    sessions: async_sessionmaker[AsyncSession],
    fetched: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ids = await _setup(sessions, library, owners_poster=False)
    async with sessions() as session:
        run = await session.get(PosterRun, ids["run"])
        assert run is not None
        run.masked = True
        await session.commit()
    async with sessions() as session:
        with pytest.raises(handler.WriteRefusedError, match="masked run of a blind evaluation"):
            await handler.enqueue_deploy(session, ids["pick"])
    monkeypatch.setattr(settings, "DEPLOY_POLICY", "all")
    async with sessions() as session:
        with pytest.raises(handler.WriteRefusedError, match="masked run"):
            await handler.queue_policy_deploy(session, ids["movie"], ids["pick"])
    # A job queued before the run was known to be masked refuses when it runs.
    with pytest.raises(PermanentJobError, match="masked run"):
        await _deploy(sessions, ids)
    assert not (library / "poster.jpg").exists()
    assert fetched == []
    assert await _deployment_count(sessions) == 0
