"""The run record against a real PostgreSQL server, and the run folder reader."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from marquee.artwork import runs
from marquee.config import settings
from marquee.db.models import Artwork, Movie, PosterRun, RunCandidate

SHA = "b" * 64


def _now() -> datetime:
    return datetime.now(UTC)


def _movie(radarr_id: int, title: str = "Die Hard", **values: object) -> Movie:
    defaults: dict[str, object] = {
        "radarr_id": radarr_id,
        "title": title,
        "sort_title": title.lower(),
        "radarr_path": f"/radarr/movies/{title} ({radarr_id})",
        "has_file": True,
        "is_present": True,
        "tmdb_id": 500 + radarr_id,
        "tmdb_enriched_at": _now(),
        "last_seen_at": _now(),
    }
    return Movie(**{**defaults, **values})


def _write_folder(folder: Path, rows: list[dict], images: dict[str, bytes]) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    for name, data in images.items():
        (folder / name).write_bytes(data)
    (folder / runs.RUN_ARCHIVE).write_text(
        json.dumps({"diagnostic_ledger": {"version": 1, "candidates": rows}})
    )


LEDGER = [
    {
        "orig_filename": "a.jpg",
        "stage_reached": "ranked",
        "gate_decision": "passed",
        "rejection_reason": None,
        "rank": 2,
        "final_score": 0.7,
        "stack_rank": 1,
        "stack_label": "B",
    },
    {
        "orig_filename": "b.jpg",
        "stage_reached": "ranked",
        "gate_decision": "passed",
        "rejection_reason": None,
        "rank": 1,
        "final_score": 0.9,
        "stack_rank": 1,
        "stack_label": "A",
    },
    {
        "orig_filename": "c.jpg",
        "stage_reached": "ocr",
        "gate_decision": None,
        "rejection_reason": "text_heavy",
        "rank": None,
        "final_score": None,
        "stack_rank": None,
        "stack_label": None,
    },
    {
        "orig_filename": "d.jpg",
        "stage_reached": "fetch",
        "gate_decision": "gated",
        "rejection_reason": "resolution",
        "rank": None,
        "final_score": None,
        "stack_rank": None,
        "stack_label": None,
    },
]
IMAGES = {"a.jpg": b"aaa", "b.jpg": b"bbb", "c.jpg": b"ccc"}


def _result(pick: str | None = "b.jpg") -> runs.ChildResult:
    return runs.ChildResult(
        status="completed",
        counts={"posters_found": 4, "ranked": 2},
        stages=[{"stage": "fetch", "survivors": 3, "seconds": 0.5}],
        scorer="weighted",
        engine_run_id="e" * 32,
        ai_model="clip-vit-b-32",
        profile_sha256=SHA,
        pick_file=pick,
    )


# ── Names, folders, and the archive ───────────────────────────────────────


def test_the_archive_name_is_the_engines() -> None:
    from marquee.pipeline.single_run import RUN_ARCHIVE

    assert runs.RUN_ARCHIVE == RUN_ARCHIVE


@pytest.mark.parametrize(
    ("title", "slug"),
    [
        ("Die Hard", "die-hard"),
        ("Amélie", "amelie"),
        ("Ocean's Eleven", "oceans-eleven"),
        ("Alien: Covenant", "alien-covenant"),
        ("???", "untitled"),
        ("x" * 80, "x" * 60),
    ],
)
def test_title_slug(title: str, slug: str) -> None:
    assert runs.title_slug(title) == slug


def test_a_run_folder_is_named_after_its_record() -> None:
    assert runs.run_folder_name(42, "Die Hard") == "00000042-die-hard"


@pytest.mark.parametrize("name", ["", ".", "..", "a/b", "a\0b"])
def test_run_dir_takes_only_a_plain_name(name: str) -> None:
    with pytest.raises(runs.RunRecordError):
        runs.run_dir(name)


def test_remove_run_folder_refuses_a_symlink(tmp_path: Path) -> None:
    target = tmp_path / "elsewhere"
    target.mkdir()
    (tmp_path / "00000001-x").symlink_to(target)
    with pytest.raises(runs.RunRecordError, match="symbolic link"):
        runs.remove_run_folder("00000001-x", tmp_path)
    assert target.is_dir()
    assert runs.remove_run_folder("00000002-missing", tmp_path) is False


def test_read_run_folder_hashes_top_level_downloads_only(tmp_path: Path) -> None:
    folder = tmp_path / "00000001-die-hard"
    _write_folder(folder, LEDGER, IMAGES)
    (folder / "2-ocr-rejected").mkdir()
    (folder / "2-ocr-rejected" / "c.jpg").write_bytes(b"a copy")

    read = runs.read_run_folder(folder)

    by_name = {c.source_file: c for c in read.candidates}
    assert by_name["a.jpg"].sha256 == runs.file_sha256(folder / "a.jpg")
    assert by_name["c.jpg"].sha256 == runs.file_sha256(folder / "c.jpg")
    assert by_name["d.jpg"].sha256 is None
    assert by_name["c.jpg"].rejection_reason == "text_heavy"
    assert read.ranked_total == 2
    assert read.ranked == [
        {"rank": 1, "stack": "1A", "score": 0.9, "file": "b.jpg"},
        {"rank": 2, "stack": "1B", "score": 0.7, "file": "a.jpg"},
    ]


def test_an_unreadable_archive_is_an_error(tmp_path: Path) -> None:
    (tmp_path / runs.RUN_ARCHIVE).write_text("{not json")
    with pytest.raises(runs.RunRecordError, match="cannot be read"):
        runs.read_run_folder(tmp_path)


def test_a_child_result_must_be_complete() -> None:
    with pytest.raises(runs.RunRecordError, match="incomplete"):
        runs.ChildResult.from_dict({"status": "completed"})
    with pytest.raises(runs.RunRecordError, match="not a dict"):
        runs.ChildResult.from_dict([])


def test_ineligible_reasons() -> None:
    assert runs.ineligible_reason(_movie(1)) is None
    assert "no longer listed" in runs.ineligible_reason(_movie(1, is_present=False))
    assert "has no file" in runs.ineligible_reason(_movie(1, has_file=False))
    assert "no TMDB id" in runs.ineligible_reason(_movie(1, tmdb_id=None))
    assert "no TMDB details" in runs.ineligible_reason(_movie(1, tmdb_enriched_at=None))


# ── The record ────────────────────────────────────────────────────────────


async def _job(session: AsyncSession, job_id: int, state: str = "running") -> int:
    await session.execute(
        text("insert into jobs (id, kind, job_class, state) values (:id, 'poster_run', 'gpu', :s)"),
        {"id": job_id, "s": state},
    )
    return job_id


async def _with_movie(session: AsyncSession, **values: object) -> Movie:
    movie = _movie(1, **values)
    session.add(movie)
    await session.flush()
    return movie


@pytest.mark.db
async def test_start_creates_one_row_named_after_itself(db_session: AsyncSession) -> None:
    movie = await _with_movie(db_session)
    job_id = await _job(db_session, 10)

    run = await runs.start(db_session, job_id=job_id, movie=movie)

    assert run.state == "running"
    assert run.job_id == job_id
    assert run.run_folder == f"{run.id:08d}-die-hard"
    assert not (settings.runs_dir / run.run_folder).exists()


@pytest.mark.db
async def test_a_second_attempt_starts_over_in_the_same_row(db_session: AsyncSession) -> None:
    movie = await _with_movie(db_session)
    job_id = await _job(db_session, 11)
    run = await runs.start(db_session, job_id=job_id, movie=movie)
    folder = settings.runs_dir / run.run_folder
    _write_folder(folder, LEDGER, IMAGES)
    await runs.complete(db_session, run.id, _result(), runs.read_run_folder(folder))
    # The job runs again, as after a worker restart: the row must be running for that.
    await db_session.execute(
        text("update poster_runs set state = 'running' where id = :id"), {"id": run.id}
    )
    await db_session.commit()

    again = await runs.start(db_session, job_id=job_id, movie=movie)

    assert again.id == run.id
    assert again.state == "running"
    assert again.counts is None and again.finished_at is None
    assert not folder.exists()
    assert await db_session.scalar(select(func.count()).select_from(PosterRun)) == 1
    assert await db_session.scalar(select(func.count()).select_from(RunCandidate)) == 0


@pytest.mark.db
async def test_start_fails_a_run_its_job_abandoned(db_session: AsyncSession) -> None:
    movie = await _with_movie(db_session)
    old_job = await _job(db_session, 12, state="failed")
    old = await runs.start(db_session, job_id=old_job, movie=movie)
    new_job = await _job(db_session, 13)

    await runs.start(db_session, job_id=new_job, movie=movie)

    await db_session.refresh(old)
    assert old.state == "failed"
    assert old.error == runs.ABANDONED


@pytest.mark.db
async def test_complete_stores_the_candidates_and_the_pick(db_session: AsyncSession) -> None:
    movie = await _with_movie(db_session)
    run = await runs.start(db_session, job_id=await _job(db_session, 14), movie=movie)
    folder = settings.runs_dir / run.run_folder
    _write_folder(folder, LEDGER, IMAGES)

    done = await runs.complete(db_session, run.id, _result(), runs.read_run_folder(folder))

    assert done.state == "completed"
    assert (done.candidate_count, done.ranked_count) == (4, 2)
    assert done.profile_sha256 == SHA and done.scorer_name == "weighted"
    rows = await runs.candidates_of(db_session, run.id)
    assert [r.source_file for r in rows] == ["b.jpg", "a.jpg", "c.jpg", "d.jpg"]
    pick = await runs.pick_of(db_session, run.id)
    assert pick is not None and pick.source_file == "b.jpg" and pick.rank == 1
    assert pick.sha256 == runs.file_sha256(folder / "b.jpg")
    with pytest.raises(runs.RunRecordError, match="not running"):
        await runs.complete(db_session, run.id, _result(), runs.read_run_folder(folder))


@pytest.mark.db
async def test_a_run_with_no_survivor_completes_with_no_pick(db_session: AsyncSession) -> None:
    movie = await _with_movie(db_session)
    run = await runs.start(db_session, job_id=await _job(db_session, 15), movie=movie)
    folder = settings.runs_dir / run.run_folder
    _write_folder(folder, LEDGER[2:], {"c.jpg": b"c"})

    await runs.complete(db_session, run.id, _result(pick=None), runs.read_run_folder(folder))

    assert await runs.pick_of(db_session, run.id) is None


@pytest.mark.db
async def test_a_pick_that_is_not_a_candidate_is_refused(db_session: AsyncSession) -> None:
    movie = await _with_movie(db_session)
    run = await runs.start(db_session, job_id=await _job(db_session, 16), movie=movie)
    folder = settings.runs_dir / run.run_folder
    _write_folder(folder, LEDGER, IMAGES)
    with pytest.raises(runs.RunRecordError, match="not among its candidates"):
        await runs.complete(db_session, run.id, _result("zz.jpg"), runs.read_run_folder(folder))


@pytest.mark.db
async def test_fail_keeps_the_folder_and_cancel_removes_it(db_session: AsyncSession) -> None:
    movie = await _with_movie(db_session)
    failed = await runs.start(db_session, job_id=await _job(db_session, 17), movie=movie)
    (settings.runs_dir / failed.run_folder).mkdir(parents=True)
    assert await runs.fail(db_session, failed.id, "TMDB: 503\nsecond line" + "x" * 600)
    await db_session.refresh(failed)
    assert failed.state == "failed" and len(failed.error or "") == 500
    assert (settings.runs_dir / failed.run_folder).is_dir()
    assert not await runs.fail(db_session, failed.id, "again")

    cancelled = await runs.start(db_session, job_id=await _job(db_session, 18), movie=movie)
    (settings.runs_dir / cancelled.run_folder).mkdir(parents=True)
    assert await runs.cancel(db_session, cancelled.id)
    await db_session.refresh(cancelled)
    assert cancelled.state == "cancelled"
    assert not (settings.runs_dir / cancelled.run_folder).exists()


@pytest.mark.db
async def test_scopes_pick_eligible_movies(db_session: AsyncSession) -> None:
    ran = _movie(1, "Alien")
    has_poster = _movie(2, "Brazil")
    plain = _movie(3, "Casablanca")
    not_enriched = _movie(4, "Dune", tmdb_enriched_at=None)
    no_file = _movie(5, "Enemy", has_file=False)
    db_session.add_all([ran, has_poster, plain, not_enriched, no_file])
    await db_session.flush()
    db_session.add(
        Artwork(
            movie_id=has_poster.id,
            artwork_kind="poster",
            file_name="poster.jpg",
            sha256=SHA,
            size_bytes=1,
            file_modified_at=_now(),
            origin="library",
            first_seen_at=_now(),
            last_seen_at=_now(),
        )
    )
    await runs.start(db_session, job_id=await _job(db_session, 19), movie=ran)

    assert await runs.scope_movie_ids(db_session, "all") == [ran.id, has_poster.id, plain.id]
    assert await runs.scope_movie_ids(db_session, "never-run") == [has_poster.id, plain.id]
    assert await runs.scope_movie_ids(db_session, "no-poster") == [ran.id, plain.id]
    assert await runs.scope_movie_ids(db_session, "all", limit=1) == [ran.id]


@pytest.mark.db
async def test_list_runs_and_find_by_title(db_session: AsyncSession) -> None:
    movie = await _with_movie(db_session)
    other = _movie(2, "die hard", year=2001)
    db_session.add(other)
    await db_session.flush()
    first = await runs.start(db_session, job_id=await _job(db_session, 20), movie=movie)
    second = await runs.start(db_session, job_id=await _job(db_session, 21), movie=other)

    page = await runs.list_runs(db_session)
    assert page.total == 2 and [r.id for r in page.items] == [second.id, first.id]
    assert [r.id for r in (await runs.list_runs(db_session, movie_id=movie.id)).items] == [first.id]
    with pytest.raises(ValueError, match="unknown run state"):
        await runs.list_runs(db_session, state="queued")
    assert len(await runs.find_movies_by_title(db_session, " DIE HARD ")) == 2
    assert [m.id for m in await runs.find_movies_by_title(db_session, "die hard", year=2001)] == [
        other.id
    ]
