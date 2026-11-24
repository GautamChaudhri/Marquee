from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import SecretStr

from marquee import cli
from marquee.config import pipeline_settings, settings


def test_run_arguments() -> None:
    args = cli._build_parser().parse_args(["run", "Die Hard", "--year", "1988", "--json"])

    assert (args.command, args.title, args.year, args.tmdb_id, args.json) == (
        "run",
        "Die Hard",
        1988,
        None,
        True,
    )


def test_run_with_a_tmdb_id() -> None:
    args = cli._build_parser().parse_args(["run", "Die Hard", "--tmdb-id", "562"])

    assert args.tmdb_id == 562


@pytest.mark.parametrize("argv", [["run"], ["run", "Die Hard", "--year", "eighty-eight"]])
def test_run_rejects_bad_arguments(argv: list[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        cli._build_parser().parse_args(argv)
    assert exc.value.code == 2


@pytest.fixture
def ready(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """A configured token and a taste profile file, so the run reaches the lookup."""
    profile = tmp_path / "taste_profile.npz"
    profile.write_bytes(b"profile")
    monkeypatch.setattr(settings, "TMDB_READ_ACCESS_TOKEN", SecretStr("test-token"))
    monkeypatch.setattr(pipeline_settings, "TASTE_PROFILE_PATH", profile)
    return profile


def test_run_without_a_tmdb_token(
    monkeypatch: pytest.MonkeyPatch, ready: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(settings, "TMDB_READ_ACCESS_TOKEN", None)

    assert cli.main(["run", "Die Hard"]) == 1
    assert capsys.readouterr().err.strip() == (
        "marquee run: TMDB_READ_ACCESS_TOKEN is not set; add it to .env"
    )


def test_run_without_a_taste_profile(
    monkeypatch: pytest.MonkeyPatch,
    ready: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(pipeline_settings, "TASTE_PROFILE_PATH", tmp_path / "absent.npz")

    assert cli.main(["run", "Die Hard"]) == 1
    err = capsys.readouterr().err.strip().splitlines()
    assert len(err) == 1
    assert "no taste profile" in err[0]
    assert "marquee taste seed" in err[0]


class _NoNetworkTMDB:
    """Stands in for TMDBClient; a lookup that never reaches TMDB must not call it."""

    def __init__(self, read_access_token: str) -> None:
        self.token = read_access_token

    async def __aenter__(self) -> _NoNetworkTMDB:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        return None

    async def find_by_external_id(self, external_id: str, source: str) -> dict:
        raise AssertionError("no TMDB call expected")


def test_run_with_an_unknown_title(
    monkeypatch: pytest.MonkeyPatch,
    ready: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = tmp_path / "Movies"
    (root / "1080p" / "Die Hard (1988)").mkdir(parents=True)
    monkeypatch.setattr(settings, "MOVIE_ROOTS", [root])
    monkeypatch.setattr("marquee.integrations.tmdb.client.TMDBClient", _NoNetworkTMDB)

    assert cli.main(["run", "Die Hrd"]) == 1
    err = capsys.readouterr().err.strip().splitlines()
    assert len(err) == 1
    assert err[0].startswith("marquee run: no movie folder titled 'Die Hrd'")
    assert "Die Hard (1988)" in err[0]


def test_run_with_an_absent_library(
    monkeypatch: pytest.MonkeyPatch,
    ready: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(settings, "MOVIE_ROOTS", [tmp_path / "not-mounted"])

    assert cli.main(["run", "Die Hard"]) == 1
    assert "does not exist (is the share mounted?)" in capsys.readouterr().err


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
    queued = parse(["jobs", "enqueue", "engine_probe", "--payload", '{"title": "Die Hard"}'])
    assert (queued.kind, queued.payload, queued.priority) == (
        "engine_probe",
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
    with psycopg.connect(settings.database_url_sync, autocommit=True) as conn:
        conn.execute("TRUNCATE jobs, job_events, job_schedules RESTART IDENTITY CASCADE")


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
    code, out, _ = run("jobs", "enqueue", "engine_probe", "--payload", '{"title": "Die Hard"}')
    assert code == 0 and out.endswith(" engine_probe queued\n")
    probe = out.split()[1]

    code, out, _ = run("jobs", "list")
    assert code == 0
    assert out.splitlines()[1].split()[:4] == [probe, "engine_probe", "queued", "0/1"]
    assert out.splitlines()[-1] == "2 of 2 job(s)"
    listed = json.loads(run("jobs", "list", "--kind", "engine_probe", "--json")[1])
    assert [(job["id"], job["payload"]) for job in listed] == [(int(probe), {"title": "Die Hard"})]

    assert run("jobs", "cancel", probe)[1] == f"job {probe} engine_probe cancelled\n"
    assert run("jobs", "retry", probe)[1] == f"job {probe} engine_probe queued again\n"

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
