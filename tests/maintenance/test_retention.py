"""Retention, rule by rule, against the test database and a data folder in tmp_path: for
each rule, what goes and what stays. The tests give the code its time; nothing waits."""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from marquee.config import settings
from marquee.db.engine import create_session_factory
from marquee.db.models import Deployment, Job, JobEvent, JobSchedule, Movie, PosterRun, RunCandidate
from marquee.db.models.evaluation import Evaluation, EvaluationResult
from marquee.db.models.residual import ResidualTraining
from marquee.db.models.taste import PreferenceEvent, TasteProfile
from marquee.maintenance import retention

pytestmark = pytest.mark.db

NOW = datetime(2027, 3, 1, 12, 0, tzinfo=UTC)
OLD = NOW - timedelta(days=100)
RECENT = NOW - timedelta(days=10)
SHA = "a" * 64


@pytest.fixture
def data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    folder = tmp_path / "data"
    folder.mkdir()
    movies = tmp_path / "movies"
    movies.mkdir()
    monkeypatch.setattr(settings, "DATA_DIR", folder)
    monkeypatch.setattr(settings, "MOVIE_ROOTS", [movies])
    monkeypatch.setattr(settings, "RETENTION_DAYS", 90)
    yield folder


@pytest.fixture
def sessions(db_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return create_session_factory(db_engine)


def _file(path: Path, when: datetime, data: bytes = b"x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    os.utime(path, (when.timestamp(), when.timestamp()))
    return path


def _folder(path: Path, when: datetime, files: dict[str, bytes] | None = None) -> Path:
    for name, content in (files or {"a.jpg": b"jpeg"}).items():
        _file(path / name, when, content)
    os.utime(path, (when.timestamp(), when.timestamp()))
    return path


async def _plan(sessions: async_sessionmaker[AsyncSession]) -> retention.Report:
    async with sessions() as session:
        return await retention.plan(session, now=NOW)


async def _run(
    sessions: async_sessionmaker[AsyncSession], dry_run: bool = False
) -> retention.Report:
    async with sessions() as session:
        return await retention.run(session, now=NOW, dry_run=dry_run)


def _names(report: retention.Report, rule: str) -> list[str]:
    return [item.name for item in report.items if item.rule == rule]


async def _movie(session: AsyncSession, radarr_id: int) -> int:
    movie = Movie(
        radarr_id=radarr_id,
        title=f"Movie {radarr_id}",
        sort_title=f"movie {radarr_id}",
        has_file=True,
        is_present=True,
        radarr_path=f"/r/Movie {radarr_id}",
        last_seen_at=NOW,
    )
    session.add(movie)
    await session.flush()
    return movie.id


async def _job(
    session: AsyncSession,
    *,
    state: str = "succeeded",
    finished: datetime | None = OLD,
    parent: int | None = None,
    kind: str = "library_sync",
    job_class: str = "network",
    payload: dict[str, Any] | None = None,
) -> int:
    job = Job(
        kind=kind,
        job_class=job_class,
        state=state,
        finished_at=finished if state in retention.FINISHED else None,
        parent_id=parent,
        payload=payload or {},
    )
    session.add(job)
    await session.flush()
    session.add(JobEvent(job_id=job.id, type="queued"))
    await session.flush()
    return job.id


async def _run_row(
    session: AsyncSession,
    movie_id: int,
    *,
    when: datetime = OLD,
    state: str = "completed",
    masked: bool = False,
    candidates: int = 2,
) -> tuple[int, str]:
    run_id = await session.scalar(
        select(func.nextval(func.pg_get_serial_sequence("poster_runs", "id")))
    )
    folder = f"{run_id:08d}-movie-{movie_id}"
    session.add(
        PosterRun(
            id=run_id,
            movie_id=movie_id,
            state=state,
            run_folder=folder,
            started_at=when,
            finished_at=when if state != "running" else None,
            masked=masked,
        )
    )
    await session.flush()
    for n in range(candidates):
        session.add(RunCandidate(run_id=run_id, source_file=f"c{n}.jpg"))
    await session.flush()
    _folder(settings.runs_dir / folder, when)
    return int(run_id), folder  # type: ignore[arg-type]


# ── Where it may run ──────────────────────────────────────────────────────


def test_it_refuses_when_the_data_folder_and_a_movie_root_overlap(tmp_path: Path) -> None:
    library = tmp_path / "movies"
    (library / "data").mkdir(parents=True)
    with pytest.raises(retention.RetentionRefusedError, match="inside the movie root"):
        retention.check_folders(library / "data", [library])
    with pytest.raises(retention.RetentionRefusedError, match="is inside the data folder"):
        retention.check_folders(tmp_path, [library])
    with pytest.raises(retention.RetentionRefusedError, match="inside the movie root"):
        retention.check_folders(library, [library])
    assert retention.check_folders(library / "data", [tmp_path / "other"]) == library / "data"


async def test_a_refusal_deletes_nothing(
    data: Path, sessions: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    old = _file(data / "cache" / "thumbs" / "x-320.jpg", OLD)
    monkeypatch.setattr(settings, "MOVIE_ROOTS", [data.parent])
    with pytest.raises(retention.RetentionRefusedError):
        await _run(sessions)
    assert old.exists()


# ── Jobs ──────────────────────────────────────────────────────────────────


async def test_finished_jobs_go_with_their_events_after_the_retention_days(
    data: Path, sessions: async_sessionmaker[AsyncSession]
) -> None:
    async with sessions() as session:
        gone = [await _job(session, state=s) for s in ("succeeded", "failed", "cancelled")]
        recent = await _job(session, finished=RECENT)
        queued = await _job(session, state="queued")
        running = await _job(session, state="running")
        whole = await _job(session, kind="batch", job_class="batch")
        children = [await _job(session, parent=whole) for _ in range(2)]
        split = await _job(session, kind="batch", job_class="batch")
        old_child = await _job(session, parent=split)
        new_child = await _job(session, parent=split, finished=RECENT)
        evaluation_batch = await _job(session, kind="batch", job_class="batch")
        evaluation_child = await _job(session, parent=evaluation_batch)
        session.add(
            Evaluation(
                job_id=evaluation_batch, profile_sha256=SHA, scorer="weighted", movies=1, queued=1
            )
        )
        last_sync = await _job(session)
        session.add(JobSchedule(name="library_sync", next_due_at=NOW, last_job_id=last_sync))
        await session.commit()

    report = await _run(sessions)

    assert _names(report, "jobs") == [f"job {i}" for i in sorted([*gone, whole])]
    whole_item = next(item for item in report.items if item.name == f"job {whole}")
    assert whole_item.rows == 1 + 2 + 3  # the batch, its two children, three events
    async with sessions() as session:
        left = set(await session.scalars(select(Job.id)))
        events = set(await session.scalars(select(JobEvent.job_id)))
    kept = {
        recent,
        queued,
        running,
        split,
        old_child,
        new_child,
        evaluation_batch,
        evaluation_child,
        last_sync,
    }
    assert left == kept
    assert events == kept
    assert not set(children) & left


async def test_a_job_a_deployment_names_goes_and_the_deployment_stays(
    data: Path, sessions: async_sessionmaker[AsyncSession]
) -> None:
    async with sessions() as session:
        movie = await _movie(session, 1)
        job = await _job(session, kind="poster_deploy", job_class="media_write")
        session.add(
            Deployment(
                movie_id=movie,
                artwork_kind="poster",
                action="deploy",
                initiated_by="owner",
                job_id=job,
                file_name="poster.jpg",
                new_sha256=SHA,
                new_size_bytes=1,
            )
        )
        await session.commit()

    await _run(sessions)

    async with sessions() as session:
        deployment = await session.scalar(select(Deployment))
    assert deployment is not None and deployment.job_id is None


# ── Runs ──────────────────────────────────────────────────────────────────


async def test_old_runs_nothing_names_go_with_their_candidates_and_folders(
    data: Path, sessions: async_sessionmaker[AsyncSession]
) -> None:
    async with sessions() as session:
        movie = await _movie(session, 1)
        other = await _movie(session, 2)
        stale, stale_folder = await _run_row(session, movie)
        deployed, _ = await _run_row(session, movie)
        answered, _ = await _run_row(session, movie)
        newest_completed, _ = await _run_row(session, movie)
        newest_failed, _ = await _run_row(session, movie, state="failed")
        only_old, _ = await _run_row(session, other, state="cancelled", when=OLD)
        recent, _ = await _run_row(session, other, when=RECENT)
        session.add(
            Deployment(
                movie_id=movie,
                artwork_kind="poster",
                action="deploy",
                initiated_by="owner",
                run_id=deployed,
                file_name="poster.jpg",
                new_sha256=SHA,
                new_size_bytes=1,
            )
        )
        session.add(
            PreferenceEvent(
                movie_id=movie,
                run_id=answered,
                action="approval",
                exposed=[],
                context={},
                idempotency_key="answer-1",
            )
        )
        await session.commit()

    planned = await _plan(sessions)
    report = await _run(sessions)

    assert _names(planned, "runs") == [
        f"run {stale} ({stale_folder})",
        f"run {only_old} ({only_old:08d}-movie-{other})",
    ]
    # The other movie's old cancelled run is older than its newest (recent) run: it goes too.
    went = sorted(int(name.split()[1]) for name in _names(report, "runs"))
    assert went == sorted([stale, only_old])
    assert _names(report, "runs") == _names(planned, "runs")
    item = next(i for i in report.items if i.name.startswith(f"run {stale} "))
    assert (item.rows, item.size_bytes) == (3, 4)
    assert not (settings.runs_dir / stale_folder).exists()
    async with sessions() as session:
        left = set(await session.scalars(select(PosterRun.id)))
        candidates = set(await session.scalars(select(RunCandidate.run_id)))
    assert left == {deployed, answered, newest_completed, newest_failed, recent}
    assert candidates == left
    for run_id in left:
        assert any(p.name.startswith(f"{run_id:08d}-") for p in settings.runs_dir.iterdir())


async def test_a_running_run_and_a_movies_newest_runs_stay_however_old(
    data: Path, sessions: async_sessionmaker[AsyncSession]
) -> None:
    async with sessions() as session:
        movie = await _movie(session, 1)
        completed, _ = await _run_row(session, movie)
        running, _ = await _run_row(session, movie, state="running")
        await session.commit()

    report = await _run(sessions)

    assert _names(report, "runs") == []
    async with sessions() as session:
        assert set(await session.scalars(select(PosterRun.id))) == {completed, running}


async def test_masked_runs_stay_while_their_evaluation_is_the_newest(
    data: Path, sessions: async_sessionmaker[AsyncSession]
) -> None:
    async with sessions() as session:
        movie = await _movie(session, 1)
        product, _ = await _run_row(session, movie)
        older_masked, _ = await _run_row(session, movie, masked=True)
        current_masked, _ = await _run_row(session, movie, masked=True)
        batches = [await _job(session, kind="batch", job_class="batch") for _ in range(2)]
        evaluations = []
        for batch, run_id in zip(batches, (older_masked, current_masked), strict=True):
            evaluation = Evaluation(
                job_id=batch, profile_sha256=SHA, scorer="weighted", movies=1, queued=1
            )
            session.add(evaluation)
            await session.flush()
            session.add(
                EvaluationResult(
                    evaluation_id=evaluation.id, movie_id=movie, run_id=run_id, outcome="failed"
                )
            )
            evaluations.append(evaluation.id)
        await session.commit()

    report = await _run(sessions)

    assert [int(n.split()[1]) for n in _names(report, "runs")] == [older_masked]
    async with sessions() as session:
        assert set(await session.scalars(select(PosterRun.id))) == {product, current_masked}
        results = dict(
            (
                await session.execute(
                    select(EvaluationResult.evaluation_id, EvaluationResult.run_id)
                )
            ).all()
        )
    # The older evaluation keeps its result row; only its link to the run is gone.
    assert results == {evaluations[0]: None, evaluations[1]: current_masked}


# ── Folders and files ─────────────────────────────────────────────────────


async def test_run_folders_with_no_run_go_after_a_week(
    data: Path, sessions: async_sessionmaker[AsyncSession]
) -> None:
    runs_dir = settings.runs_dir
    orphan = _folder(runs_dir / "20261123T181115Z-die-hard", NOW - timedelta(days=8))
    fresh = _folder(runs_dir / "20261124T002257Z-die-hard", NOW - timedelta(days=6))
    # Old, but one file inside was touched three days ago: the folder counts as touched.
    touched = _folder(runs_dir / "20261120T000000Z-heat", NOW - timedelta(days=30))
    _file(touched / "1-candidates" / "late.jpg", NOW - timedelta(days=3))
    loose = _file(runs_dir / "notes.txt", OLD)
    (runs_dir / "a-link").symlink_to(data.parent)
    async with sessions() as session:
        movie = await _movie(session, 1)
        _, named = await _run_row(session, movie)  # the movie's newest: its folder stays
        await session.commit()

    report = await _run(sessions)

    assert _names(report, "run_folders") == ["runs/20261123T181115Z-die-hard"]
    assert not orphan.exists()
    assert fresh.exists() and touched.exists() and loose.exists() and (runs_dir / named).exists()
    assert (runs_dir / "a-link").is_symlink() and data.parent.exists()
    assert any("runs/a-link is a link" in note for note in report.kept)


async def test_caches_go_when_untouched_long_enough(
    data: Path, sessions: async_sessionmaker[AsyncSession]
) -> None:
    cache = data / "cache"
    thumbs = [
        _file(cache / "thumbs" / f"{d}-320.jpg", NOW - timedelta(days=d), b"thumb")
        for d in (31, 29)
    ]
    embeddings = [
        _file(cache / "embeddings" / "clip-vit-b-32" / f"{d}.npz", NOW - timedelta(days=d))
        for d in (91, 89)
    ]
    kernel = _file(cache / "cuda-jit" / "ComputeCache" / "index", OLD)

    report = await _run(sessions)

    assert _names(report, "thumbnails") == ["cache/thumbs/31-320.jpg"]
    assert _names(report, "embeddings") == ["cache/embeddings/clip-vit-b-32/91.npz"]
    assert report.totals()["thumbnails"] == {"count": 1, "bytes": 5, "rows": 0}
    assert [p.exists() for p in thumbs] == [False, True]
    assert [p.exists() for p in embeddings] == [False, True]
    assert kernel.exists()


async def test_leftovers_go_after_a_day_unless_a_job_still_owns_them(
    data: Path, sessions: async_sessionmaker[AsyncSession]
) -> None:
    day_old = NOW - timedelta(hours=25)
    async with sessions() as session:
        finished = await _job(session, kind="poster_deploy", job_class="media_write")
        waiting = await _job(session, kind="poster_deploy", job_class="media_write", state="queued")
        session.add(
            ResidualTraining(
                profile_sha256=SHA,
                outcome="activate",
                subjects=1,
                pairs=1,
                events=1,
                evidence_digest=SHA,
                report={},
                artifact_sha256="b" * 64,
                file_name="b" * 64 + ".npz",
            )
        )
        await session.commit()
    cache = data / "cache"
    gone = [
        _file(cache / "deploys" / f"{finished}.jpg", day_old),
        _file(cache / "deploys" / f"{finished}.json", day_old),
        _file(cache / "deploys" / f".{finished}.json.part", day_old),
        _folder(cache / "taste-build" / "123", day_old, {"staged/a.png": b"png"}),
        _file(cache / "blind-profiles" / f"{SHA}-7.npz", day_old),
        _file(data / "backups" / "db" / ".marquee-dump-abc", day_old),
        _file(data / "ml" / "profiles" / f".{SHA}.npz.part", day_old),
        _file(data / "ml" / "residuals" / f".pending-{'b' * 64}.json", day_old),
    ]
    stays = [
        _file(cache / "deploys" / f"{waiting}.jpg", day_old),  # its job is still queued
        _file(cache / "deploys" / "999.jpg", NOW - timedelta(hours=23)),  # not a day old
        _file(data / "ml" / "residuals" / f".pending-{'c' * 64}.json", OLD),  # not recorded
        _file(data / "backups" / "db" / "marquee-20261101T000000Z-0007.dump", OLD),
        _file(data / "backups" / "db" / "manual-20261101T000000Z-0006.dump", OLD),
    ]

    planned = await _plan(sessions)
    report = await _run(sessions)

    assert _names(report, "leftovers") == _names(planned, "leftovers")
    assert sorted(_names(report, "leftovers")) == sorted(
        p.relative_to(data).as_posix() for p in gone
    )
    assert not any(p.exists() for p in gone)
    assert all(p.exists() for p in stays)
    assert any(f"deploys/{waiting}.jpg is kept: a queued or running job" in n for n in report.kept)
    assert any(
        f".pending-{'c' * 64}.json is kept: the next taste rebuild" in n for n in report.kept
    )


async def test_old_profile_and_residual_copies_beyond_the_newest_twenty_go(
    data: Path, sessions: async_sessionmaker[AsyncSession]
) -> None:
    profiles = data / "ml" / "profiles"
    residuals = data / "ml" / "residuals"
    async with sessions() as session:
        for n in range(24):
            sha = f"{n:064x}"
            session.add(
                TasteProfile(
                    sha256=sha,
                    revision=SHA,
                    source="seed" if n == 0 else "rebuild",
                    positives=1,
                    negatives=0,
                    positive_movie_ids=[],
                    file_name=f"{sha}.npz",
                    is_active=n == 1,
                )
            )
            _file(profiles / f"{sha}.npz", OLD, b"profile")
            artifact = f"{n + 100:064x}"
            session.add(
                ResidualTraining(
                    profile_sha256=SHA,
                    outcome="activate",
                    subjects=1,
                    pairs=1,
                    events=1,
                    evidence_digest=SHA,
                    report={},
                    artifact_sha256=artifact,
                    file_name=f"{artifact}.npz",
                    is_active=n == 2,
                )
            )
            _file(residuals / f"{artifact}.npz", OLD, b"artifact")
            await session.flush()
        await session.commit()
    # A pending record names the oldest artifact: it stays.
    _file(residuals / f".pending-{100:064x}.json", NOW)
    _file(profiles / "notes.txt", OLD)

    report = await _run(sessions)

    # Newest 20 are ids 5..24 (n 4..23); the seed (n 0) and the active one (n 1) stay.
    assert _names(report, "profiles") == [f"ml/profiles/{n:064x}.npz" for n in (2, 3)]
    # Residuals: n 4..23 newest, n 2 active, n 0 named by a pending record.
    assert _names(report, "residuals") == [f"ml/residuals/{n + 100:064x}.npz" for n in (1, 3)]
    async with sessions() as session:
        assert await session.scalar(select(func.count()).select_from(TasteProfile)) == 24
    assert (profiles / "notes.txt").exists()


# ── What is never deleted, and the dry run ────────────────────────────────


async def test_what_is_never_deleted_stays_however_old(
    data: Path, sessions: async_sessionmaker[AsyncSession]
) -> None:
    never = [
        _file(data / "backups" / "posters" / "516" / f"{SHA}.jpg", OLD),
        _file(data / "backups" / "posters" / "516" / ".backup-abc", OLD),
        _file(data / "backups" / "db" / "pre-restore-20261101T000000Z.dump", OLD),
        _file(data / "ml" / "exemplars" / f"{SHA}.png", OLD),
        _file(data / "ml" / "exemplars" / f".{SHA}.png.part", OLD),
        _file(data / "ml" / "seed" / "Heat (1995).png", OLD),
        _file(data / "ml" / "taste_profile.clip-vit-b-32.npz", OLD),
        _file(data / "models" / "clip-vit-b-32.onnx", OLD),
        _file(data / "models" / "retired" / "clip-vit-b-32.onnx", OLD),
        _file(data / "cache" / "cuda-jit" / "index", OLD),
        _file(data / "cache" / "taste_thumbs" / "x.jpg", OLD),
        _file(data / "something-else" / "x", OLD),
        _file(data / "text_profiles.json", OLD),
        _file(data.parent / "movies" / "Heat (1995)" / "poster.jpg", OLD),
    ]

    report = await _run(sessions)

    assert report.items == []
    assert all(p.exists() for p in never)


async def test_a_dry_run_deletes_nothing_and_lists_exactly_what_then_goes(
    data: Path, sessions: async_sessionmaker[AsyncSession]
) -> None:
    async with sessions() as session:
        movie = await _movie(session, 1)
        await _run_row(session, movie)
        await _run_row(session, movie)
        await _job(session)
        await session.commit()
    _folder(settings.runs_dir / "20261101T000000Z-heat", OLD)
    _file(data / "cache" / "thumbs" / "x-320.jpg", OLD)
    before = sorted(p.relative_to(data).as_posix() for p in data.rglob("*"))

    dry = await _run(sessions, dry_run=True)
    after_dry = sorted(p.relative_to(data).as_posix() for p in data.rglob("*"))
    async with sessions() as session:
        jobs_after_dry = await session.scalar(select(func.count()).select_from(Job))
    real = await _run(sessions)

    assert dry.dry_run and not real.dry_run
    assert after_dry == before and jobs_after_dry == 1
    assert [(i.rule, i.name) for i in dry.items] == [(i.rule, i.name) for i in real.items]
    assert {i.rule for i in real.items} == {"jobs", "runs", "run_folders", "thumbnails"}
    assert real.to_result()["count"] == 4
    assert "items" not in real.to_result()
    assert all("/" not in str(v) for v in real.to_result()["rules"].values())
    # Nothing is left to delete.
    assert (await _run(sessions)).items == []


async def test_the_retention_schedule_is_not_due_when_first_seen(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    from marquee.jobs import schedules
    from marquee.jobs.registry import default_registry

    schedule = next(s for s in schedules.SCHEDULES if s.name == "retention")
    assert schedule.first_due == "after_one_interval"
    async with sessions() as session:
        queued = await schedules.run_due(session, [schedule], default_registry())
        row = await session.scalar(select(JobSchedule).where(JobSchedule.name == "retention"))
        now = await session.scalar(select(func.now()))
        jobs = await session.scalar(select(func.count()).select_from(Job))
        await session.execute(text("select 1"))
    assert queued == [] and jobs == 0
    assert row is not None and row.last_job_id is None
    assert row.next_due_at - now >= timedelta(hours=settings.RETENTION_INTERVAL_HOURS) - timedelta(
        seconds=5
    )
