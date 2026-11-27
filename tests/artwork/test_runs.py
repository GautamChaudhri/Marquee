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


def test_removing_a_run_folder_never_follows_a_link_inside_it(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("not the run's")
    runs_dir = tmp_path / "runs"
    folder = runs_dir / "00000003-heat"
    (folder / "1-candidates").mkdir(parents=True)
    (folder / "1-candidates" / "a.jpg").write_bytes(b"jpeg")
    (folder / "link").symlink_to(outside)

    assert runs.remove_run_folder("00000003-heat", runs_dir) is True

    assert not folder.exists()
    assert (outside / "keep.txt").read_text() == "not the run's"


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
async def test_start_refuses_a_completed_run(db_session: AsyncSession) -> None:
    movie = await _with_movie(db_session)
    job_id = await _job(db_session, 12)
    run = await runs.start(db_session, job_id=job_id, movie=movie)
    folder = settings.runs_dir / run.run_folder
    _write_folder(folder, LEDGER, IMAGES)
    await runs.complete(db_session, run.id, _result(), runs.read_run_folder(folder))

    with pytest.raises(runs.RunRecordError, match="already completed"):
        await runs.start(db_session, job_id=job_id, movie=movie)

    assert folder.is_dir()
    assert await db_session.scalar(select(func.count()).select_from(RunCandidate)) == len(LEDGER)


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


# ── Masked runs and the scorer that ranked ────────────────────────────────


def _with_contributions(residual: list[bool]) -> list[dict]:
    """LEDGER with the residual's mark on the ranked candidates named by ``residual``."""
    rows = [dict(row) for row in LEDGER]
    ranked = [row for row in rows if row["rank"] is not None]
    for row, marked in zip(ranked, residual, strict=True):
        row["contributions"] = (
            {"baseline_score": 0.5, runs.RESIDUAL_MARK: 0.1} if marked else {"knn_sim": 0.3}
        )
    return rows


def test_the_folder_says_whether_the_residual_ranked_every_candidate(tmp_path: Path) -> None:
    _write_folder(tmp_path / "all", _with_contributions([True, True]), IMAGES)
    _write_folder(tmp_path / "some", _with_contributions([True, False]), IMAGES)
    _write_folder(tmp_path / "none", LEDGER, IMAGES)
    assert runs.read_run_folder(tmp_path / "all").residual_ranked is True
    assert runs.read_run_folder(tmp_path / "some").residual_ranked is False
    assert runs.read_run_folder(tmp_path / "none").residual_ranked is False


# ── What a score is made of ───────────────────────────────────────────────


def test_a_weighted_score_is_its_features_contributions() -> None:
    row = {
        "contributions": {"knn_sim": 0.25, "aesthetic": 0.05, "lang_match": 0.0},
        "normalized_features": {"knn_sim": 0.9, "aesthetic": 0.4},
    }
    assert runs.score_parts(row) == [
        runs.ScorePart("knn_sim", 0.25, 0.9),
        runs.ScorePart("aesthetic", 0.05, 0.4),
        runs.ScorePart("lang_match", 0.0, None),
    ]


def test_a_residual_score_is_the_weighted_parts_and_what_the_residual_changed() -> None:
    row = {
        "contributions": {
            "baseline:knn_sim": 0.3,
            "baseline:aesthetic": 0.2,
            "residual:knn_sim": 0.04,
            "baseline_score": 0.5,
            runs.RESIDUAL_MARK: 0.12,
            "final_score": 0.53,
        },
        "normalized_features": {"knn_sim": 1.0, "aesthetic": 0.5},
    }
    parts = runs.score_parts(row)
    assert [part.feature for part in parts] == ["knn_sim", "aesthetic", runs.RESIDUAL_PART]
    assert parts[0] == runs.ScorePart("knn_sim", 0.3, 1.0)
    assert parts[-1].value is None and parts[-1].contribution == pytest.approx(0.03)
    # The parts sum to the score the residual gave.
    assert sum(part.contribution for part in parts) == pytest.approx(0.53)


def test_parts_that_are_not_finite_numbers_are_left_out() -> None:
    row = {
        "contributions": {"knn_sim": float("nan"), "aesthetic": "high", "sharpness": True},
        "normalized_features": {"knn_sim": 0.5},
    }
    assert runs.score_parts(row) == []
    assert runs.score_parts({"contributions": None}) == []
    assert runs.score_parts({}) == []


def test_score_parts_are_read_for_ranked_candidates_only(tmp_path: Path) -> None:
    rows = [dict(row) for row in LEDGER]
    for row in rows:
        row["contributions"] = {"knn_sim": 0.3}
        row["normalized_features"] = {"knn_sim": 1.0}
    _write_folder(tmp_path / "run", rows, IMAGES)

    found = runs.read_score_parts(tmp_path / "run")

    ranked = {row["orig_filename"] for row in LEDGER if row["rank"] is not None}
    assert set(found) == ranked and len(ranked) < len(LEDGER)
    assert all(parts == [runs.ScorePart("knn_sim", 0.3, 1.0)] for parts in found.values())


def test_a_run_without_a_readable_archive_has_no_score_parts(tmp_path: Path) -> None:
    assert runs.read_score_parts(tmp_path / "gone") == {}
    (tmp_path / "bad").mkdir()
    (tmp_path / "bad" / runs.RUN_ARCHIVE).write_text("{not json")
    assert runs.read_score_parts(tmp_path / "bad") == {}
    (tmp_path / "bad" / runs.RUN_ARCHIVE).write_text('{"diagnostic_ledger": {"candidates": 3}}')
    assert runs.read_score_parts(tmp_path / "bad") == {}


@pytest.mark.db
@pytest.mark.parametrize(
    ("reported", "marked", "scorer", "residual_id"),
    [
        ("residual", [True, True], "residual", 7),
        ("residual", [True, False], "weighted", None),  # the engine fell back part way
        ("weighted", [False, False], "weighted", None),
    ],
)
async def test_complete_records_the_scorer_that_ranked(
    db_session: AsyncSession,
    reported: str,
    marked: list[bool],
    scorer: str,
    residual_id: int | None,
) -> None:
    from dataclasses import replace

    from marquee.db.models import ResidualTraining

    training = ResidualTraining(
        profile_sha256=SHA,
        outcome="activate",
        subjects=30,
        pairs=300,
        events=30,
        evidence_digest="b" * 64,
        report={},
        artifact_sha256="c" * 64,
        file_name=f"{'c' * 64}.npz",
    )
    db_session.add(training)
    await db_session.flush()
    movie = await _with_movie(db_session)
    run = await runs.start(db_session, job_id=await _job(db_session, 60), movie=movie)
    folder = settings.runs_dir / run.run_folder
    _write_folder(folder, _with_contributions(marked), IMAGES)
    result = replace(_result(), scorer=reported, residual_id=training.id)

    done = await runs.complete(db_session, run.id, result, runs.read_run_folder(folder))

    assert done.scorer_name == scorer
    assert done.residual_id == (training.id if residual_id else None)


@pytest.mark.db
async def test_masked_runs_are_listed_only_when_asked_and_never_count_as_run(
    db_session: AsyncSession,
) -> None:
    movie = await _with_movie(db_session)
    other = _movie(2, "Heat")
    db_session.add(other)
    await db_session.flush()
    normal = await runs.start(db_session, job_id=await _job(db_session, 61), movie=movie)
    masked = await runs.start(
        db_session, job_id=await _job(db_session, 62), movie=other, masked=True
    )
    assert masked.masked and not normal.masked

    assert [r.id for r in (await runs.list_runs(db_session)).items] == [normal.id]
    assert [r.id for r in (await runs.list_runs(db_session, masked=True)).items] == [masked.id]
    assert [r.id for r in (await runs.list_runs(db_session, masked=None)).items] == [
        masked.id,
        normal.id,
    ]
    assert await runs.scope_movie_ids(db_session, "never-run") == [other.id]


@pytest.mark.db
async def test_a_job_keeps_its_kind_of_run(db_session: AsyncSession) -> None:
    movie = await _with_movie(db_session)
    job_id = await _job(db_session, 63)
    await runs.start(db_session, job_id=job_id, movie=movie, masked=True)
    with pytest.raises(runs.RunRecordError, match="other kind"):
        await runs.start(db_session, job_id=job_id, movie=movie)


@pytest.mark.db
async def test_remove_older_masked_leaves_normal_and_running_runs(
    db_session: AsyncSession,
) -> None:
    movie = await _with_movie(db_session)
    normal = await runs.start(db_session, job_id=await _job(db_session, 64), movie=movie)
    await runs.complete(db_session, normal.id, _result(pick=None), _folder_of(normal, LEDGER[2:]))
    old = await runs.start(db_session, job_id=await _job(db_session, 65), movie=movie, masked=True)
    await runs.complete(db_session, old.id, _result(), _folder_of(old, LEDGER))
    running = await runs.start(
        db_session, job_id=await _job(db_session, 66), movie=movie, masked=True
    )
    newest = await runs.start(
        db_session, job_id=await _job(db_session, 67), movie=movie, masked=True
    )
    old_folder = settings.runs_dir / old.run_folder

    removed = await runs.remove_older_masked(db_session, movie.id, newest.id)

    assert removed == [old.id]
    assert not old_folder.exists()
    left = list(await db_session.scalars(select(PosterRun.id).order_by(PosterRun.id)))
    assert left == [normal.id, running.id, newest.id]


def _folder_of(run: PosterRun, rows: list[dict]) -> runs.RunFolder:
    folder = settings.runs_dir / run.run_folder
    _write_folder(
        folder,
        rows,
        {k: v for k, v in IMAGES.items() if any(r["orig_filename"] == k for r in rows)},
    )
    return runs.read_run_folder(folder)
