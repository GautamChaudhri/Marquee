from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import SecretStr

from marquee import cli
from marquee.config import settings


def test_run_arguments() -> None:
    args = cli._build_parser().parse_args(["run", "Die Hard", "--year", "1988", "--json"])

    assert (args.command, args.title, args.year, args.movie_id, args.no_wait, args.json) == (
        "run",
        "Die Hard",
        1988,
        None,
        False,
        True,
    )


def test_run_with_a_movie_id_and_no_wait() -> None:
    args = cli._build_parser().parse_args(["run", "Die Hard", "--movie-id", "12", "--no-wait"])

    assert (args.movie_id, args.no_wait) == (12, True)


@pytest.mark.parametrize(
    "argv",
    [["run"], ["run", "Die Hard", "--year", "eighty-eight"], ["run", "X", "--tmdb-id", "562"]],
)
def test_run_rejects_bad_arguments(argv: list[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        cli._build_parser().parse_args(argv)
    assert exc.value.code == 2


# ── marquee db ────────────────────────────────────────────────────────────


def test_db_revision_needs_a_message() -> None:
    with pytest.raises(SystemExit) as exc:
        cli._build_parser().parse_args(["db", "revision"])
    assert exc.value.code == 2


@pytest.mark.parametrize(("current", "code"), [("0001", 0), (None, 1)])
def test_db_status_exits_1_unless_at_head(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    current: str | None,
    code: int,
) -> None:
    from marquee.db import migrate

    monkeypatch.setattr(
        migrate, "status", lambda: migrate.MigrationStatus(current=current, head="0001")
    )
    assert cli.main(["db", "status"]) == code
    out = capsys.readouterr().out
    assert f"revision  {current or 'none'}" in out
    assert "head      0001" in out


def test_db_errors_never_show_the_password(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from sqlalchemy.exc import OperationalError

    from marquee.db import migrate

    monkeypatch.setattr(settings, "POSTGRES_PASSWORD", SecretStr("db-password-value"))

    def failing_status() -> migrate.MigrationStatus:
        raise OperationalError("select 1", {}, Exception("auth failed for db-password-value"))

    monkeypatch.setattr(migrate, "status", failing_status)
    assert cli.main(["db", "status"]) == 1
    captured = capsys.readouterr()
    assert "db-password-value" not in captured.out + captured.err
    assert "marquee db status:" in captured.err


# ── marquee sync ──────────────────────────────────────────────────────────


def _sync_returns(monkeypatch: pytest.MonkeyPatch, outcome: object) -> None:
    from marquee.library import sync

    async def fake(*, dry_run: bool = False, cancel: object = None) -> object:
        if isinstance(outcome, Exception):
            raise outcome
        outcome.dry_run = dry_run  # type: ignore[attr-defined]
        return outcome

    monkeypatch.setattr(sync, "sync_from_settings", fake)


def test_sync_prints_the_report_and_exits_0(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from marquee.library.sync import SyncReport

    report = SyncReport(dry_run=False, movies_listed=3, created=3, with_file=2, posters_found=2)
    report.folders_without_movie = ["4K/Extra (2020)"]
    _sync_returns(monkeypatch, report)
    assert cli.main(["sync", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "3 created" in out
    assert "4K/Extra (2020)" in out
    assert "nothing was written" in out


def test_sync_json(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    import json

    from marquee.library.sync import SyncReport

    _sync_returns(monkeypatch, SyncReport(dry_run=False, created=1))
    assert cli.main(["sync", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["created"] == 1


def test_sync_exits_1_when_a_movie_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    from marquee.library.sync import SyncReport

    _sync_returns(monkeypatch, SyncReport(dry_run=False, failed=1, failed_radarr_ids=[7]))
    assert cli.main(["sync"]) == 1


def test_sync_exits_1_when_the_path_check_fails(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from marquee.library.sync import SyncPathError

    _sync_returns(monkeypatch, SyncPathError("Radarr's root folder /data/movies/4K is missing"))
    assert cli.main(["sync"]) == 1
    assert "marquee sync: Radarr's root folder /data/movies/4K" in capsys.readouterr().err


# ── marquee api ───────────────────────────────────────────────────────────


def test_api_defaults_to_the_configured_address(monkeypatch: pytest.MonkeyPatch) -> None:
    from marquee.api import app

    served: list[tuple[str, int]] = []
    monkeypatch.setattr(app, "serve", lambda host, port: served.append((host, port)))
    monkeypatch.setattr(settings, "API_KEY", SecretStr("k"))
    monkeypatch.setattr(settings, "API_HOST", "127.0.0.1")
    monkeypatch.setattr(settings, "API_PORT", 8000)
    assert cli.main(["api"]) == 0
    assert cli.main(["api", "--host", "0.0.0.0", "--port", "9000"]) == 0
    assert served == [("127.0.0.1", 8000), ("0.0.0.0", 9000)]


def test_api_warns_when_no_key_is_configured(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from marquee.api import app

    monkeypatch.setattr(app, "serve", lambda host, port: None)
    monkeypatch.setattr(settings, "API_KEY", None)
    assert cli.main(["api"]) == 0
    assert "API_KEY is not set" in capsys.readouterr().err


# ── marquee worker and marquee jobs ───────────────────────────────────────


def test_worker_and_jobs_arguments() -> None:
    parse = cli._build_parser().parse_args
    assert parse(["worker"]).drain is False
    assert parse(["worker", "--drain"]).drain is True
    listed = parse(["jobs", "list", "--state", "failed", "--kind", "library_sync", "--json"])
    assert (listed.jobs_command, listed.state, listed.kind, listed.json) == (
        "list",
        "failed",
        "library_sync",
        True,
    )
    queued = parse(["jobs", "enqueue", "gpu_selftest", "--payload", '{"title": "Die Hard"}'])
    assert (queued.kind, queued.payload, queued.priority) == (
        "gpu_selftest",
        '{"title": "Die Hard"}',
        0,
    )
    assert parse(["jobs", "cancel", "7"]).id == 7
    assert parse(["jobs", "retry", "7"]).id == 7
    assert parse(["jobs", "show", "7"]).id == 7


@pytest.mark.parametrize(
    "argv",
    [["jobs"], ["jobs", "list", "--state", "lost"], ["jobs", "cancel", "x"], ["jobs", "show"]],
)
def test_jobs_rejects_bad_arguments(argv: list[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        cli._build_parser().parse_args(argv)
    assert exc.value.code == 2


@pytest.mark.parametrize(
    ("argv", "message"),
    [
        (["jobs", "enqueue", "nope"], "unknown job kind 'nope'; one of library_sync"),
        (["jobs", "enqueue", "gpu_selftest", "--payload", "{"], "--payload is not JSON"),
        (["jobs", "enqueue", "gpu_selftest", "--payload", "[1]"], "must be a JSON object"),
    ],
)
def test_jobs_enqueue_checks_before_it_connects(
    argv: list[str], message: str, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(argv) == 1
    assert message in capsys.readouterr().err


@pytest.fixture
def jobs_database(monkeypatch: pytest.MonkeyPatch, database_url: str) -> object:
    """Settings pointed at the test database; the job tables are emptied afterwards."""
    import psycopg
    from sqlalchemy.engine import make_url

    parsed = make_url(database_url)
    monkeypatch.setattr(settings, "POSTGRES_USER", parsed.username)
    monkeypatch.setattr(settings, "POSTGRES_PASSWORD", SecretStr(parsed.password or ""))
    monkeypatch.setattr(settings, "POSTGRES_HOST", parsed.host)
    monkeypatch.setattr(settings, "POSTGRES_PORT", parsed.port)
    monkeypatch.setattr(settings, "POSTGRES_DB", parsed.database)
    yield
    from marquee.db.base import Base

    with psycopg.connect(settings.database_url_sync, autocommit=True) as conn:
        conn.execute(f"TRUNCATE {', '.join(Base.metadata.tables)} RESTART IDENTITY CASCADE")


@pytest.mark.db
def test_jobs_commands_end_to_end(
    jobs_database: object, capsys: pytest.CaptureFixture[str]
) -> None:
    import json

    def run(*argv: str) -> tuple[int, str, str]:
        code = cli.main(list(argv))
        captured = capsys.readouterr()
        return code, captured.out, captured.err

    code, out, err = run("jobs", "enqueue", "library_sync")
    assert (code, out, err) == (0, "job 1 library_sync queued\n", "")
    assert run("jobs", "enqueue", "library_sync")[1] == (
        "job 1 library_sync already queued; not queued again\n"
    )
    code, out, _ = run("jobs", "enqueue", "gpu_selftest", "--payload", '{"title": "Die Hard"}')
    assert code == 0 and out.endswith(" gpu_selftest queued\n")
    probe = out.split()[1]

    code, out, _ = run("jobs", "list")
    assert code == 0
    assert out.splitlines()[1].split()[:4] == [probe, "gpu_selftest", "queued", "0/1"]
    assert out.splitlines()[-1] == "2 of 2 job(s)"
    listed = json.loads(run("jobs", "list", "--kind", "gpu_selftest", "--json")[1])
    assert [(job["id"], job["payload"]) for job in listed] == [(int(probe), {"title": "Die Hard"})]

    assert run("jobs", "cancel", probe)[1] == f"job {probe} gpu_selftest cancelled\n"
    assert run("jobs", "retry", probe)[1] == f"job {probe} gpu_selftest queued again\n"

    code, out, _ = run("jobs", "show", probe)
    assert code == 0
    assert "state     queued" in out
    assert [line.split()[3] for line in out.split("events\n")[1].splitlines()] == [
        "queued",
        "cancelled",
        "retried",
    ]

    assert run("jobs", "show", "999") == (1, "", "marquee jobs show: no job 999\n")
    code, _, err = run("jobs", "retry", "1")
    assert code == 1
    assert "job 1 is queued; only a failed or cancelled job can be retried" in err


# ── marquee run ───────────────────────────────────────────────────────────


def _add_movie(radarr_id: int, title: str, year: int, **values: object) -> int:
    import psycopg

    columns = {
        "radarr_id": radarr_id,
        "title": title,
        "sort_title": title.lower(),
        "year": year,
        "radarr_path": f"/radarr/{title} ({year})",
        "has_file": True,
        "tmdb_id": 500 + radarr_id,
        **values,
    }
    names = ", ".join(columns)
    marks = ", ".join(["%s"] * len(columns))
    with psycopg.connect(settings.database_url_sync, autocommit=True) as conn:
        row = conn.execute(
            f"insert into movies ({names}, tmdb_enriched_at, last_seen_at) "
            f"values ({marks}, now(), now()) returning id",
            list(columns.values()),
        ).fetchone()
    assert row is not None
    return int(row[0])


def _jobs() -> list[tuple]:
    import psycopg

    with psycopg.connect(settings.database_url_sync, autocommit=True) as conn:
        return conn.execute("select id, kind, payload from jobs order by id").fetchall()


def _worker_running(monkeypatch: pytest.MonkeyPatch, running: bool) -> None:
    from marquee.jobs import queries

    async def status(session: object) -> object:
        return queries.WorkerStatus(running=running, since=None, queued=0, running_jobs=0, failed=0)

    monkeypatch.setattr(queries, "worker_status", status)


@pytest.mark.db
def test_run_needs_one_movie(
    jobs_database: object, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _worker_running(monkeypatch, True)
    first = _add_movie(1, "Dune", 1984)
    second = _add_movie(2, "Dune", 2021)

    assert cli.main(["run", "Dun"]) == 1
    assert "no movie titled 'Dun' in the database" in capsys.readouterr().err
    assert cli.main(["run", "dune"]) == 1
    err = capsys.readouterr().err
    assert "2 movies are titled 'dune'" in err
    assert f"Dune (1984), movie {first}" in err and f"Dune (2021), movie {second}" in err
    assert "Give --year or --movie-id" in err
    assert cli.main(["run", "Dune", "--year", "1999"]) == 1
    assert "no movie titled 'Dune' (1999)" in capsys.readouterr().err
    assert _jobs() == []

    assert cli.main(["run", "Dune", "--movie-id", str(second), "--no-wait"]) == 0
    assert f"movie {second}" in capsys.readouterr().out
    assert [(kind, payload) for _, kind, payload in _jobs()] == [
        ("poster_run", {"movie_id": second})
    ]


@pytest.mark.db
def test_run_refuses_without_a_worker_unless_told_not_to_wait(
    jobs_database: object, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _worker_running(monkeypatch, False)
    movie_id = _add_movie(1, "Heat", 1995)

    assert cli.main(["run", "Heat"]) == 1
    assert "no worker is running, so nothing was queued" in capsys.readouterr().err
    assert _jobs() == []

    assert cli.main(["run", "Heat", "--no-wait", "--json"]) == 0
    import json

    printed = json.loads(capsys.readouterr().out)
    assert printed["movie_id"] == movie_id and printed["created"] is True
    assert cli.main(["run", "Heat", "--no-wait"]) == 0
    assert "already queued; following it" in capsys.readouterr().out
    assert len(_jobs()) == 1


@pytest.mark.db
def test_run_refuses_a_movie_it_cannot_run(
    jobs_database: object, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _worker_running(monkeypatch, True)
    _add_movie(1, "Ash", 2025, has_file=False)

    assert cli.main(["run", "Ash"]) == 1
    assert "has no file" in capsys.readouterr().err
    assert _jobs() == []


@pytest.mark.db
def test_run_follows_the_job_and_prints_the_summary(
    jobs_database: object, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import json
    import threading
    import time

    import psycopg

    _worker_running(monkeypatch, True)
    movie_id = _add_movie(1, "Heat", 1995)
    monkeypatch.setattr(cli, "FOLLOW_POLL_S", 0.05)
    result = {
        "run_id": 3,
        "movie_id": movie_id,
        "title": "Heat",
        "run_folder": "00000003-heat",
        "status": "completed",
        "counts": {"posters_found": 2, "ranked": 1},
        "stages": [{"stage": "fetch", "survivors": 2, "seconds": 0.5}],
        "ranked_total": 1,
        "ranked": [{"rank": 1, "stack": "1A", "score": 0.9, "file": "a.jpg"}],
        "pick": {"candidate_id": 8, "rank": 1, "stack": "1A", "score": 0.9, "file": "a.jpg"},
        "seconds": 20.0,
    }

    def pretend_worker() -> None:
        deadline = time.monotonic() + 10
        with psycopg.connect(settings.database_url_sync, autocommit=True) as conn:
            while time.monotonic() < deadline:
                done = conn.execute(
                    "update jobs set state = 'succeeded', attempt = 1, result = %s "
                    "where kind = 'poster_run' returning id",
                    [json.dumps(result)],
                ).fetchone()
                if done:
                    return
                time.sleep(0.05)

    thread = threading.Thread(target=pretend_worker)
    thread.start()
    try:
        assert cli.main(["run", "heat"]) == 0
    finally:
        thread.join()
    out = capsys.readouterr().out
    assert f"movie     Heat (1995), movie {movie_id}, TMDB 501" in out
    assert "  fetch               2 left     0.50 s" in out
    assert "pick      1A a.jpg (score 0.9000; candidate 8)" in out
    assert f"run       3, in {settings.runs_dir / '00000003-heat'}" in out


@pytest.mark.db
def test_run_reports_a_failed_job(
    jobs_database: object, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import threading
    import time

    import psycopg

    _worker_running(monkeypatch, True)
    _add_movie(1, "Heat", 1995)
    monkeypatch.setattr(cli, "FOLLOW_POLL_S", 0.05)

    def pretend_worker() -> None:
        deadline = time.monotonic() + 10
        with psycopg.connect(settings.database_url_sync, autocommit=True) as conn:
            while time.monotonic() < deadline:
                if conn.execute(
                    "update jobs set state = 'failed', error = 'TMDB: 503' "
                    "where kind = 'poster_run' returning id"
                ).fetchone():
                    return
                time.sleep(0.05)

    thread = threading.Thread(target=pretend_worker)
    thread.start()
    try:
        assert cli.main(["run", "Heat"]) == 1
    finally:
        thread.join()
    assert "failed: TMDB: 503" in capsys.readouterr().err


# ── marquee taste seed --rescan ───────────────────────────────────────────


@pytest.mark.db
def test_rescan_is_refused_once_marquee_has_written_a_poster(
    jobs_database: object, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import psycopg

    from marquee.taste import seed

    ran: list[str] = []
    monkeypatch.setattr(seed, "run_seed", lambda mode, dry_run: ran.append(mode) or 0)

    assert cli.main(["taste", "seed", "--rescan"]) == 0
    assert ran == ["rescan"]

    movie_id = _add_movie(1, "Heat", 1995)
    with psycopg.connect(settings.database_url_sync, autocommit=True) as conn:
        conn.execute(
            "insert into deployments (movie_id, artwork_kind, action, initiated_by, file_name, "
            "new_sha256, new_size_bytes) values (%s, 'poster', 'deploy', 'owner', 'poster.jpg', "
            "%s, 1)",
            [movie_id, "a" * 64],
        )
    assert cli.main(["taste", "seed", "--rescan"]) == 1
    assert "--rescan is refused: Marquee has written to the library" in capsys.readouterr().err
    assert ran == ["rescan"]
    assert cli.main(["taste", "seed", "--reuse"]) == 0
    assert ran == ["rescan", "reuse"]


def test_rescan_is_refused_when_the_database_cannot_be_asked(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from marquee.taste import seed

    monkeypatch.setattr(settings, "POSTGRES_HOST", "127.0.0.1")
    monkeypatch.setattr(settings, "POSTGRES_PORT", 1)
    monkeypatch.setattr(seed, "run_seed", lambda mode, dry_run: pytest.fail("must not run"))

    assert cli.main(["taste", "seed", "--rescan"]) == 1
    assert "--rescan needs the database" in capsys.readouterr().err


@pytest.mark.db
def test_the_seed_is_refused_in_every_form_once_exemplar_records_exist(
    jobs_database: object, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import psycopg

    from marquee.taste import seed

    ran: list[tuple[str, bool]] = []
    monkeypatch.setattr(seed, "run_seed", lambda mode, dry_run: ran.append((mode, dry_run)) or 0)
    assert cli.main(["taste", "seed", "--reuse"]) == 0
    movie_id = _add_movie(1, "Heat", 1995)
    with psycopg.connect(settings.database_url_sync, autocommit=True) as conn:
        conn.execute(
            "insert into exemplars (movie_id, polarity, weight, source, state, sha256, "
            "file_name, staged_name) values (%s, 'positive', 1.0, 'seed', 'active', %s, %s, "
            "'Heat (1995).png')",
            [movie_id, "a" * 64, f"{'a' * 64}.png"],
        )
    for argv in ([], ["--reuse"], ["--rescan"], ["--dry-run"]):
        assert cli.main(["taste", "seed", *argv]) == 1
        err = capsys.readouterr().err
        assert "built from exemplar records now (1 of them)" in err, argv
        assert "use `marquee taste rebuild` instead" in err
    assert ran == [("reuse", False)]


def test_the_seed_is_refused_when_the_database_cannot_be_asked(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from marquee.taste import seed

    monkeypatch.setattr(settings, "POSTGRES_HOST", "127.0.0.1")
    monkeypatch.setattr(settings, "POSTGRES_PORT", 1)
    monkeypatch.setattr(seed, "run_seed", lambda mode, dry_run: pytest.fail("must not run"))

    assert cli.main(["taste", "seed", "--reuse"]) == 1
    assert "the seed needs the database to confirm" in capsys.readouterr().err


@pytest.mark.db
def test_import_seed_reports_its_problems_and_exits_1(
    jobs_database: object,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from marquee.taste import seed

    monkeypatch.setattr(
        seed.SeedPaths,
        "from_settings",
        classmethod(lambda cls: cls(seed_dir=tmp_path / "seed", profile=tmp_path / "p.npz")),
    )
    assert cli.main(["taste", "import-seed"]) == 1
    out = capsys.readouterr().out
    assert out.startswith("marquee taste import-seed: nothing imported; 1 problem(s):")
    assert "movies.json cannot be read" in out


@pytest.mark.db
def test_taste_rebuild_queues_the_current_revision(
    jobs_database: object, capsys: pytest.CaptureFixture[str]
) -> None:
    import psycopg

    from marquee.taste.exemplars import revision_of

    assert cli.main(["taste", "rebuild"]) == 1
    assert "no worker is running, so nothing was queued" in capsys.readouterr().err
    assert cli.main(["taste", "rebuild", "--force", "--no-wait"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("job       ") and out.endswith(" taste_rebuild queued\n")
    with psycopg.connect(settings.database_url_sync, autocommit=True) as conn:
        kind, payload, key = conn.execute("select kind, payload, dedupe_key from jobs").fetchone()
    assert kind == "taste_rebuild"
    assert payload == {"revision": revision_of([]), "force": True}
    assert key == f"taste_rebuild:{revision_of([])}"
    assert cli.main(["taste", "rebuild", "--no-wait"]) == 0
    assert "already queued; following it" in capsys.readouterr().out


# ── marquee runs, deploy, deployments, restore ────────────────────────────


@pytest.mark.db
def test_run_and_deployment_commands(
    jobs_database: object, capsys: pytest.CaptureFixture[str]
) -> None:
    import json

    import psycopg

    def run(*argv: str) -> tuple[int, str, str]:
        code = cli.main(list(argv))
        captured = capsys.readouterr()
        return code, captured.out, captured.err

    movie_id = _add_movie(1, "Heat", 1995)
    other_id = _add_movie(2, "Ronin", 1998)
    with psycopg.connect(settings.database_url_sync, autocommit=True) as conn:
        run_id = conn.execute(
            "insert into poster_runs (movie_id, state, run_folder, started_at, ranked_count, "
            "engine_status) values (%s, 'completed', '00000001-heat', now(), 1, 'completed') "
            "returning id",
            [movie_id],
        ).fetchone()[0]
        pick = conn.execute(
            "insert into run_candidates (run_id, source_file, sha256, rank, final_score, "
            "stack_rank, stack_label, is_pick) values (%s, 'pick.jpg', %s, 1, 0.9, 1, 'A', true) "
            "returning id",
            [run_id, "1" * 64],
        ).fetchone()[0]
        other = conn.execute(
            "insert into run_candidates (run_id, source_file, rejection_reason) "
            "values (%s, 'other.jpg', 'text_heavy') returning id",
            [run_id],
        ).fetchone()[0]

    code, out, _ = run("runs", "list")
    assert code == 0 and out.splitlines()[-1] == "1 of 1 run(s)"
    assert json.loads(run("runs", "list", "--json")[1])[0]["run_folder"] == "00000001-heat"
    code, out, _ = run("runs", "show", str(run_id))
    assert code == 0 and "pick.jpg" in out and "pick" in out and "text_heavy" in out
    assert (
        json.loads(run("runs", "show", str(run_id), "--json")[1])["candidates"][0]["stack"] == "1A"
    )
    assert run("runs", "show", "999") == (1, "", "marquee runs show: no run 999\n")

    code, out, _ = run("runs", "batch", "--scope", "never-run")
    assert code == 0
    assert f"job {out.split()[1]} poster_run queued (movie {other_id})" in out
    assert out.splitlines()[-1].endswith(
        "1 run(s) queued for 1 movie(s) in scope never-run; 0 already active"
    )

    code, out, err = run("deploy", str(run_id), "--candidate", str(other))
    assert code == 1 and "never downloaded" in err
    code, out, _ = run("deploy", str(run_id))
    assert code == 0 and out.endswith(" poster_deploy queued\n")
    code, out, _ = run("deploy", str(run_id))
    assert code == 0 and "already queued for this movie; not queued again" in out
    assert "is not one of run" in run("deploy", str(run_id), "--candidate", "999")[2]

    assert run("deployments", "list")[1].splitlines()[-1] == "0 of 0 deployment(s)"
    assert json.loads(run("deployments", "list", "--json")[1]) == []
    code, _, err = run("restore", "999")
    assert code == 1 and "no deployment 999" in err
    assert pick

    # The list says, for each row, what a restore of it would put back.
    with psycopg.connect(settings.database_url_sync, autocommit=True) as conn:
        conn.execute(
            "insert into deployments (movie_id, artwork_kind, action, initiated_by, file_name, "
            "new_sha256, new_size_bytes, previous_sha256, previous_origin, backup_file) values "
            "(%s, 'poster', 'deploy', 'owner', 'poster.jpg', %s, 4, %s, 'library', %s)",
            [movie_id, "2" * 64, "3" * 64, f"posters/{movie_id}/{'3' * 64}.jpg"],
        )
    code, out, _ = run("deployments", "list", "--movie-id", str(movie_id))
    assert code == 0 and "a restore of it puts back" in out
    assert "222222222222  333333333333 (library)" in out
    assert json.loads(run("deployments", "list", "--json")[1])[0]["restore_puts_back"] == "3" * 64
