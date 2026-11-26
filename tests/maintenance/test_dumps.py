"""Database dumps with the PostgreSQL client tools faked by small scripts: what is named,
kept, and deleted, what reaches a child process, and what a failure says. The tests that
run the real tools are in test_dumps_pg_client.py."""

from __future__ import annotations

import json
import os
import re
import stat
import sys
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr

from marquee.cancellation import JobCancelledError
from marquee.config import settings
from marquee.maintenance import dumps

SECRET = "s3cret-db-password"
FAKE_DUMP = b"PGDMP fake custom-format dump"
_TOOL = """#!{python}
import json, os, sys, time
here = os.path.dirname(os.path.abspath(__file__))
name = os.path.basename(__file__)
with open(os.path.join(here, "calls.jsonl"), "a") as log:
    log.write(json.dumps({{"tool": name, "argv": sys.argv[1:], "env": dict(os.environ)}}) + "\\n")
if sys.argv[1:] == ["--version"]:
    print(name + " (PostgreSQL) " + open(os.path.join(here, name + ".version")).read().strip())
    sys.exit(0)
mode = open(os.path.join(here, name + ".mode")).read().strip()
if mode == "fail":
    sys.stderr.write(open(os.path.join(here, name + ".stderr")).read())
    sys.exit(1)
if mode == "slow":
    time.sleep(30)
if name == "pg_dump":
    sys.stdout.buffer.write({dump!r})
elif name == "pg_restore" and "--list" in sys.argv:
    path = sys.argv[-1]
    data = open(path, "rb").read()
    if not data.startswith(b"PGDMP"):
        sys.stderr.write("pg_restore: error: input file does not appear to be a valid archive\\n")
        sys.exit(1)
    print(";\\n; Archive created at 2026-11-26\\n;\\n225; 1259 1 TABLE public alembic_version marquee")
elif name == "pg_restore" and "--table=alembic_version" in sys.argv:
    revision = open(os.path.join(here, "pg_restore.revision")).read().strip()
    print("SET client_encoding = 'UTF8';")
    print("COPY public.alembic_version (version_num) FROM stdin;")
    print(revision)
    print("\\\\.")
elif name == "pg_restore":
    print("-- the whole script")
elif name == "psql":
    script = [a for a in sys.argv if a.startswith("--file=")][0][len("--file="):]
    with open(os.path.join(here, "psql.script"), "w") as copy:
        copy.write(open(script).read())
"""


class FakeTools:
    def __init__(self, folder: Path) -> None:
        self.folder = folder
        folder.mkdir()
        for name in dumps.TOOLS:
            path = folder / name
            path.write_text(_TOOL.format(python=sys.executable, dump=FAKE_DUMP))
            path.chmod(path.stat().st_mode | stat.S_IXUSR)
            self.set(name, version="19.0", mode="ok")
        (folder / "pg_restore.revision").write_text("0006")

    def set(self, name: str, *, version: str | None = None, mode: str | None = None) -> None:
        if version is not None:
            (self.folder / f"{name}.version").write_text(version)
        if mode is not None:
            (self.folder / f"{name}.mode").write_text(mode)

    def fail(self, name: str, stderr: str) -> None:
        self.set(name, mode="fail")
        (self.folder / f"{name}.stderr").write_text(stderr)

    def calls(self, tool: str | None = None) -> list[dict[str, Any]]:
        log = self.folder / "calls.jsonl"
        if not log.exists():
            return []
        rows = [json.loads(line) for line in log.read_text().splitlines()]
        return [
            row
            for row in rows
            if (tool is None or row["tool"] == tool) and row["argv"] != ["--version"]
        ]


@pytest.fixture
def tools(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FakeTools:
    fake = FakeTools(tmp_path / "bin")
    monkeypatch.setenv("PATH", f"{fake.folder}{os.pathsep}{os.environ.get('PATH', '')}")
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(settings, "POSTGRES_PASSWORD", SecretStr(SECRET))
    monkeypatch.setattr(settings, "POSTGRES_HOST", "db.example")
    monkeypatch.setattr(settings, "POSTGRES_DB", "marquee_scratch")
    monkeypatch.setattr(
        dumps,
        "database_state",
        lambda: dumps.DatabaseState(
            server_major=19, revision="0007", has_tables=True, other_connections=0
        ),
    )
    return fake


NOON = datetime(2026, 11, 26, 12, 0, 0, tzinfo=UTC)


def _files(folder: Path) -> list[str]:
    return sorted(entry.name for entry in folder.iterdir()) if folder.exists() else []


def test_a_dump_is_named_checked_and_kept_private(tools: FakeTools) -> None:
    result = dumps.make_dump("scheduled", now=NOON)

    folder = settings.db_backups_dir
    assert result.file == "marquee-20261126T120000Z-0007.dump"
    assert _files(folder) == [result.file]
    path = folder / result.file
    assert path.read_bytes() == FAKE_DUMP
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(folder.stat().st_mode) == 0o700
    assert (result.size_bytes, result.revision) == (len(FAKE_DUMP), "0007")
    assert result.sha256 == dumps.sha256_of_file(path)
    assert (result.server_major, result.client_major) == (19, 19)
    # Read back with pg_restore --list before it was renamed into place.
    (listed,) = tools.calls("pg_restore")
    assert listed["argv"][0] == "--list"
    assert Path(listed["argv"][1]).name.startswith(".marquee-dump-")


def test_the_password_reaches_the_tools_only_through_their_environment(tools: FakeTools) -> None:
    dumps.make_dump("manual", now=NOON)

    for call in tools.calls():
        assert SECRET not in json.dumps(call["argv"])
        assert not any("://" in arg for arg in call["argv"])
    (dumped,) = tools.calls("pg_dump")[-1:]
    assert dumped["argv"] == ["--format=custom", "--no-password"]
    assert dumped["env"]["PGPASSWORD"] == SECRET
    assert (dumped["env"]["PGHOST"], dumped["env"]["PGDATABASE"]) == (
        "db.example",
        "marquee_scratch",
    )
    assert dumped["env"]["PGAPPNAME"] == "marquee-backup"
    # Nothing else of this process's environment is handed on.
    assert set(dumped["env"]) - {"PGAPPNAME", "PGCONNECT_TIMEOUT", "LC_CTYPE"} == {
        "PGHOST",
        "PGPORT",
        "PGUSER",
        "PGDATABASE",
        "PGPASSWORD",
    }


def test_the_kinds_are_named_apart(tools: FakeTools) -> None:
    assert dumps.make_dump("manual", now=NOON).file == "manual-20261126T120000Z-0007.dump"
    assert dumps.dump_name("pre-restore", NOON, "0007") == "pre-restore-20261126T120000Z.dump"
    assert dumps.parse_name("marquee-20261126T120000Z-0007.dump") == ("scheduled", NOON, "0007")
    assert dumps.parse_name("pre-restore-20261126T120000Z.dump") == ("pre-restore", NOON, None)
    assert dumps.parse_name(".marquee-dump-abc123") is None
    assert dumps.parse_name("marquee-20261126T120000Z-0007.dump.old") is None


def test_a_dump_pg_restore_cannot_read_is_not_kept(tools: FakeTools) -> None:
    tools.fail("pg_restore", "pg_restore: error: input file is too short\n")

    with pytest.raises(dumps.DumpError, match="is not a dump pg_restore can read"):
        dumps.make_dump("scheduled", now=NOON)

    assert _files(settings.db_backups_dir) == []


def test_a_failed_dump_says_why_without_the_password_and_leaves_nothing(tools: FakeTools) -> None:
    tools.fail(
        "pg_dump",
        'pg_dump: error: connection to server at "db.example" (10.0.0.9), port 5432 failed: '
        f"FATAL:  password authentication failed (tried {SECRET})\n"
        f"pg_dump: detail: postgresql://marquee:{SECRET}@db.example:5432/marquee_scratch\n",
    )

    with pytest.raises(dumps.DumpError) as raised:
        dumps.make_dump("scheduled", now=NOON)

    message = str(raised.value)
    assert message.startswith("pg_dump failed (exit 1) on marquee@db.example:")
    assert "password authentication failed" in message
    assert SECRET not in message
    assert "postgresql://***@db.example" in message
    assert _files(settings.db_backups_dir) == []


def test_missing_or_old_tools_are_named_in_plain_words(
    tools: FakeTools, monkeypatch: pytest.MonkeyPatch
) -> None:
    tools.set("pg_restore", version="17.4")
    with pytest.raises(dumps.DumpToolsError) as raised:
        dumps.make_dump("scheduled", now=NOON)
    assert str(raised.value) == (
        "pg_restore is version 17 and the database server is version 19; the client tools "
        "must be the server's version or newer"
    )

    (tools.folder / "psql").unlink()
    monkeypatch.setenv("PATH", str(tools.folder))
    with pytest.raises(dumps.DumpToolsError, match="psql is not installed"):
        dumps.find_tools(19)
    assert _files(settings.db_backups_dir) == []


def test_a_newer_client_is_accepted(tools: FakeTools) -> None:
    for name in dumps.TOOLS:
        tools.set(name, version="20.1")
    assert dumps.find_tools(19).major == 20


def test_an_existing_name_is_never_overwritten(tools: FakeTools) -> None:
    first = dumps.make_dump("scheduled", now=NOON)
    with pytest.raises(dumps.DumpError, match="already exists"):
        dumps.make_dump("scheduled", now=NOON)
    assert (settings.db_backups_dir / first.file).read_bytes() == FAKE_DUMP


def test_a_cancelled_dump_stops_and_leaves_nothing(tools: FakeTools) -> None:
    tools.set("pg_dump", mode="slow")
    cancel = threading.Event()
    threading.Timer(0.5, cancel.set).start()

    with pytest.raises(JobCancelledError, match="pg_dump stopped"):
        dumps.make_dump("scheduled", now=NOON, cancel=cancel)

    assert _files(settings.db_backups_dir) == []


def test_the_listing_is_newest_first_and_skips_what_is_not_a_dump(tools: FakeTools) -> None:
    for hours in (0, 2, 1):
        dumps.make_dump("scheduled", now=NOON + timedelta(hours=hours))
    dumps.make_dump("manual", now=NOON + timedelta(hours=3))
    folder = settings.db_backups_dir
    (folder / ".marquee-dump-leftover").write_bytes(b"partial")
    (folder / "notes.txt").write_text("not a dump")

    found = dumps.list_dumps()

    assert [(d.kind, d.made_at.hour) for d in found] == [
        ("manual", 15),
        ("scheduled", 14),
        ("scheduled", 13),
        ("scheduled", 12),
    ]
    assert found[0].to_dict()["made_at"] == "2026-11-26T15:00:00+00:00"


def test_only_scheduled_dumps_beyond_the_newest_few_are_deleted(tools: FakeTools) -> None:
    folder = settings.db_backups_dir
    names = [dumps.make_dump("scheduled", now=NOON + timedelta(hours=h)).file for h in range(4)]
    manual = dumps.make_dump("manual", now=NOON - timedelta(days=30)).file
    (folder / dumps.dump_name("pre-restore", NOON - timedelta(days=30), None)).write_bytes(b"x")

    deleted = dumps.prune(2, "scheduled")

    assert deleted == [names[1], names[0]]
    assert _files(folder) == sorted(
        [names[2], names[3], manual, "pre-restore-20261027T120000Z.dump"]
    )
    assert dumps.prune(2, "scheduled") == []
    with pytest.raises(ValueError, match="never deleted"):
        dumps.prune(0, "manual")


def test_the_newest_three_pre_restore_dumps_are_kept(tools: FakeTools) -> None:
    folder = settings.db_backups_dir
    folder.mkdir(parents=True)
    for day in range(5):
        (folder / dumps.dump_name("pre-restore", NOON + timedelta(days=day), None)).write_bytes(
            b"x"
        )

    deleted = dumps.prune(dumps.PRE_RESTORE_KEEP, "pre-restore")

    assert [dumps.parse_name(name)[1].day for name in deleted] == [27, 26]  # type: ignore[index]
    assert len(_files(folder)) == 3


def test_scrub_blanks_secrets_and_connection_strings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "POSTGRES_PASSWORD", SecretStr(SECRET))
    text = (
        f"password={SECRET} host=db postgresql+psycopg://user:other@db:5432/x "
        "postgres://u@h/d PASSWORD = hunter2"
    )
    assert dumps.scrub(text) == (
        "password=*** host=db postgresql+psycopg://***@db:5432/x postgres://***@h/d PASSWORD = ***"
    )


# ── Restore ───────────────────────────────────────────────────────────────


@pytest.fixture
def restorable(tools: FakeTools, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A dump file to restore; the migrations know 0006 and 0007, and head is 0007."""
    from marquee.db import migrate

    monkeypatch.setattr(migrate, "known_revisions", lambda: {"0006", "0007"})
    monkeypatch.setattr(migrate, "head_revision", lambda: "0007")
    path = tmp_path / "copied-off-the-machine.dump"
    path.write_bytes(FAKE_DUMP)
    return path


def _restore(path: Path, *, worker: bool = False, **options: Any) -> dumps.RestoreResult:
    return dumps.restore(path, worker_holds_lock=lambda: worker, **options)


def _upgrades(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    from marquee.db import migrate

    calls: list[str] = []

    def upgrade() -> migrate.MigrationStatus:
        calls.append("upgrade")
        return migrate.MigrationStatus(current="0007", head="0007")

    monkeypatch.setattr(migrate, "upgrade", upgrade)
    return calls


def test_a_restore_dumps_first_then_loads_in_one_transaction_then_migrates(
    tools: FakeTools, restorable: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    upgrades = _upgrades(monkeypatch)

    done = _restore(restorable, confirmed=True, now=NOON)

    assert done.pre_restore == "pre-restore-20261126T120000Z.dump"
    assert (settings.db_backups_dir / done.pre_restore).read_bytes() == FAKE_DUMP
    assert (done.restored_revision, done.revision_now, done.upgraded) == ("0006", "0007", True)
    assert upgrades == ["upgrade"]
    order = [(c["tool"], c["argv"][0]) for c in tools.calls()]
    # The pre-restore dump is made and read back before anything is dropped.
    assert order.index(("pg_dump", "--format=custom")) < order.index(("psql", "--no-psqlrc"))
    (load,) = tools.calls("psql")
    assert "--single-transaction" in load["argv"] and "--set=ON_ERROR_STOP=1" in load["argv"]
    drops = [a for a in load["argv"] if a.startswith("--command=")][0]
    assert "DROP %s IF EXISTS public.%I CASCADE" in drops
    assert load["argv"][-1].startswith("--file=")
    # psql ran the whole script pg_restore wrote, which is deleted afterwards.
    assert (tools.folder / "psql.script").read_text() == "-- the whole script\n"
    assert _files(settings.db_backups_dir) == [done.pre_restore]
    for call in tools.calls():
        assert SECRET not in json.dumps(call["argv"])


def test_a_restore_at_the_newest_revision_does_not_migrate(
    tools: FakeTools, restorable: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tools.folder / "pg_restore.revision").write_text("0007")
    upgrades = _upgrades(monkeypatch)

    done = _restore(restorable, confirmed=True, now=NOON)

    assert (done.restored_revision, done.upgraded, upgrades) == ("0007", False, [])


def test_an_empty_database_is_restored_without_a_pre_restore_dump(
    tools: FakeTools, restorable: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _upgrades(monkeypatch)
    monkeypatch.setattr(dumps, "database_state", lambda: dumps.DatabaseState(19, None, False, 0))

    done = _restore(restorable, confirmed=True, now=NOON)

    assert done.pre_restore is None and tools.calls("pg_dump") == []


@pytest.mark.parametrize(
    ("case", "message"),
    [
        ("no --yes", "add --yes to go ahead"),
        ("not a file", "is not a file"),
        ("not a dump", "is not a dump pg_restore can read"),
        ("a newer revision", "holds schema revision 0099, which this version of Marquee"),
        ("a worker", "a worker is running"),
        ("another connection", "2 other connection(s) are open to the database"),
    ],
)
def test_a_restore_refuses_when_it_is_not_safe_and_changes_nothing(
    tools: FakeTools,
    restorable: Path,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
    message: str,
) -> None:
    upgrades = _upgrades(monkeypatch)
    path, confirmed, worker = restorable, True, False
    if case == "no --yes":
        confirmed = False
    elif case == "not a file":
        path = restorable.parent / "nothing-here.dump"
    elif case == "not a dump":
        restorable.write_bytes(b"not a dump at all")
    elif case == "a newer revision":
        (tools.folder / "pg_restore.revision").write_text("0099")
    elif case == "a worker":
        worker = True
    elif case == "another connection":
        monkeypatch.setattr(
            dumps, "database_state", lambda: dumps.DatabaseState(19, "0007", True, 2)
        )

    with pytest.raises(dumps.RestoreRefusedError, match=re.escape(message)):
        _restore(path, confirmed=confirmed, worker=worker, now=NOON)

    assert tools.calls("pg_dump") == [] and tools.calls("psql") == []
    assert upgrades == []
    assert _files(settings.db_backups_dir) == []


def test_a_failed_load_names_the_pre_restore_dump_and_leaves_no_script(
    tools: FakeTools, restorable: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    upgrades = _upgrades(monkeypatch)
    tools.fail("psql", f"ERROR:  function public.f() already exists (password {SECRET})\n")

    with pytest.raises(dumps.DumpError) as raised:
        _restore(restorable, confirmed=True, now=NOON)

    message = str(raised.value)
    assert message.startswith("the restore failed and was rolled back; the database is as it was")
    assert message.endswith("(the database as it was is also in pre-restore-20261126T120000Z.dump)")
    assert SECRET not in message
    assert upgrades == []
    assert _files(settings.db_backups_dir) == ["pre-restore-20261126T120000Z.dump"]


def test_a_dump_pg_restore_cannot_read_to_the_end_changes_nothing(
    tools: FakeTools, restorable: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _upgrades(monkeypatch)
    calls = 0
    real_run = dumps._run

    def full_script_fails(args: list[str], **options: Any) -> tuple[int, bytes, str]:
        nonlocal calls
        if (
            Path(args[0]).name == "pg_restore"
            and args[-2] == "--file=-"
            and "--data-only" not in args
        ):
            calls += 1
            return 1, b"", "pg_restore: error: could not read input file: end of file"
        return real_run(args, **options)

    monkeypatch.setattr(dumps, "_run", full_script_fails)

    with pytest.raises(dumps.DumpError, match="cannot read all of copied-off-the-machine.dump"):
        _restore(restorable, confirmed=True, now=NOON)

    assert calls == 1 and tools.calls("psql") == []


def test_the_newest_three_pre_restore_dumps_are_kept_but_never_the_one_restored(
    tools: FakeTools, monkeypatch: pytest.MonkeyPatch
) -> None:
    from marquee.db import migrate

    monkeypatch.setattr(migrate, "known_revisions", lambda: {"0006", "0007"})
    monkeypatch.setattr(migrate, "head_revision", lambda: "0006")
    folder = settings.db_backups_dir
    folder.mkdir(parents=True)
    old = [dumps.dump_name("pre-restore", NOON - timedelta(days=d), None) for d in (4, 3, 2, 1)]
    for name in old:
        (folder / name).write_bytes(FAKE_DUMP)

    done = _restore(folder / old[0], confirmed=True, now=NOON)

    # Of five, the newest three stay; the oldest is the one restored, so it stays too.
    assert done.pre_restore_deleted == [old[1]]
    assert _files(folder) == sorted([old[0], old[2], old[3], done.pre_restore])


def test_a_dump_is_stale_only_while_the_schedule_is_on() -> None:
    newest = dumps.DumpFile("x", "scheduled", NOON, "0007", 1)
    assert dumps.stale(None, 24, NOON) is True
    assert dumps.stale(newest, 24, NOON + timedelta(hours=48)) is False
    assert dumps.stale(newest, 24, NOON + timedelta(hours=48, seconds=1)) is True
    assert dumps.stale(None, 0, NOON) is False
    assert dumps.stale(newest, 0, NOON + timedelta(days=30)) is False
