"""Database dumps with the real PostgreSQL client tools, against a throwaway database of
the test server. Marked ``pg_client``: they skip when no client of the server's major version
is on the path, unless ``MARQUEE_TEST_PG_CLIENT`` is set (CI sets it), and then they fail."""

from __future__ import annotations

import os
import stat
from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest
from pydantic import SecretStr
from sqlalchemy.engine import make_url

from marquee.config import settings
from marquee.db import migrate
from marquee.maintenance import dumps

pytestmark = [pytest.mark.db, pytest.mark.pg_client]


@pytest.fixture
def database(empty_database_url: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> str:
    """A migrated database of its own, with the settings and the data folder pointed at it."""
    migrate.upgrade(url=empty_database_url)
    parsed = make_url(empty_database_url)
    monkeypatch.setattr(settings, "POSTGRES_USER", parsed.username)
    monkeypatch.setattr(settings, "POSTGRES_PASSWORD", SecretStr(parsed.password or ""))
    monkeypatch.setattr(settings, "POSTGRES_HOST", parsed.host)
    monkeypatch.setattr(settings, "POSTGRES_PORT", parsed.port)
    monkeypatch.setattr(settings, "POSTGRES_DB", parsed.database)
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path / "data")
    return parsed.set(drivername="postgresql").render_as_string(hide_password=False)


@pytest.fixture
def tools(database: str) -> Iterator[dumps.Tools]:
    state = dumps.database_state()
    try:
        found = dumps.find_tools(state.server_major)
    except dumps.DumpToolsError as exc:
        if os.environ.get("MARQUEE_TEST_PG_CLIENT"):
            pytest.fail(f"MARQUEE_TEST_PG_CLIENT is set but {exc}")
        pytest.skip(str(exc))
    yield found


def _sql(url: str, query: str) -> list[tuple]:
    with psycopg.connect(url, autocommit=True) as conn:
        cursor = conn.execute(query)
        return cursor.fetchall() if cursor.description else []


def _add_movies(url: str, count: int) -> None:
    _sql(
        url,
        "insert into movies (radarr_id, title, sort_title, radarr_path, has_file, last_seen_at) "
        f"select n, 'Movie ' || n, 'movie ' || n, '/r/' || n, true, now() "
        f"from generate_series(1, {count}) n",
    )


def test_a_real_dump_reads_back_and_records_the_revision(database: str, tools: dumps.Tools) -> None:
    _add_movies(database, 3)

    result = dumps.make_dump("scheduled")

    path = settings.db_backups_dir / result.file
    assert result.file.startswith("marquee-") and result.file.endswith(
        f"-{migrate.head_revision()}.dump"
    )
    assert path.read_bytes()[:5] == b"PGDMP"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert result.sha256 == dumps.sha256_of_file(path)
    assert result.client_major >= result.server_major
    dumps.check_dump(path, tools)
    assert dumps.dump_revision(path, tools) == migrate.head_revision()
    assert [d.name for d in dumps.list_dumps()] == [result.file]


def test_a_database_that_does_not_answer_fails_clearly_without_the_password(
    database: str, tools: dumps.Tools, monkeypatch: pytest.MonkeyPatch
) -> None:
    secret = settings.POSTGRES_PASSWORD.get_secret_value()  # type: ignore[union-attr]
    # pg_dump itself cannot connect (nothing listens there); the state was read before.
    state = dumps.database_state()
    monkeypatch.setattr(dumps, "database_state", lambda: state)
    monkeypatch.setattr(settings, "POSTGRES_PORT", 1)

    with pytest.raises(dumps.DumpError) as raised:
        dumps.make_dump("scheduled")

    message = str(raised.value)
    assert message.startswith("pg_dump failed (exit 1) on ")
    assert "port 1 failed" in message or "Connection refused" in message
    assert secret not in message
    assert dumps.list_dumps() == []
    assert not any(settings.db_backups_dir.iterdir())


# ── Restore, for real ─────────────────────────────────────────────────────


def _tables(url: str) -> list[str]:
    return [
        row[0]
        for row in _sql(
            url, "select tablename from pg_tables where schemaname = 'public' order by 1"
        )
    ]


def _snapshot(url: str) -> dict[str, tuple[int, str]]:
    """Every table's row count and a hash of its content, independent of row order."""
    found = {}
    for table in _tables(url):
        ((count, digest),) = _sql(
            url,
            f"select count(*), md5(coalesce(string_agg(t::text, '|' order by t::text), '')) "
            f'from public."{table}" t',
        )
        found[table] = (count, digest)
    return found


def _no_worker() -> bool:
    return False


def test_a_round_trip_brings_back_every_table_as_it_was_at_the_dump(
    database: str, tools: dumps.Tools
) -> None:
    _add_movies(database, 5)
    _sql(
        database,
        "insert into jobs (kind, job_class, state) values ('library_sync', 'network', 'succeeded')",
    )
    at_dump = _snapshot(database)
    made = dumps.make_dump("scheduled")
    # Change more after the dump: add, delete, and change rows.
    _add_movies_from(database, 100, 3)
    _sql(database, "delete from movies where radarr_id = 2")
    _sql(database, "update jobs set state = 'failed'")
    changed = _snapshot(database)
    assert changed != at_dump

    done = dumps.restore(
        settings.db_backups_dir / made.file, confirmed=True, worker_holds_lock=_no_worker
    )

    assert _snapshot(database) == at_dump
    assert (done.restored_revision, done.revision_now, done.upgraded) == (
        migrate.head_revision(),
        migrate.head_revision(),
        False,
    )
    # Identity sequences came back too: a new row gets an id after the restored ones.
    ((new_id,),) = _sql(
        database,
        "insert into movies (radarr_id, title, sort_title, radarr_path, has_file, last_seen_at) "
        "values (999, 'New', 'new', '/r/new', true, now()) returning id",
    )
    assert new_id > 5
    _sql(database, "delete from movies where radarr_id = 999")
    # The pre-restore dump holds the changed state, and it restores as well.
    assert done.pre_restore is not None
    dumps.restore(
        settings.db_backups_dir / done.pre_restore, confirmed=True, worker_holds_lock=_no_worker
    )
    assert _snapshot(database) == changed


def _add_movies_from(url: str, first: int, count: int) -> None:
    _sql(
        url,
        "insert into movies (radarr_id, title, sort_title, radarr_path, has_file, last_seen_at) "
        f"select n, 'Movie ' || n, 'movie ' || n, '/r/' || n, true, now() "
        f"from generate_series({first}, {first + count - 1}) n",
    )


def test_an_older_dump_comes_back_at_the_newest_migration_and_nothing_else_stays(
    empty_database_url: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    migrate.upgrade("0006", url=empty_database_url)
    parsed = make_url(empty_database_url)
    url = parsed.set(drivername="postgresql").render_as_string(hide_password=False)
    for name, value in (
        ("POSTGRES_USER", parsed.username),
        ("POSTGRES_PASSWORD", SecretStr(parsed.password or "")),
        ("POSTGRES_HOST", parsed.host),
        ("POSTGRES_PORT", parsed.port),
        ("POSTGRES_DB", parsed.database),
        ("DATA_DIR", tmp_path / "data"),
    ):
        monkeypatch.setattr(settings, name, value)
    try:
        tools = dumps.find_tools(dumps.database_state().server_major)
    except dumps.DumpToolsError as exc:
        if os.environ.get("MARQUEE_TEST_PG_CLIENT"):
            pytest.fail(f"MARQUEE_TEST_PG_CLIENT is set but {exc}")
        pytest.skip(str(exc))
    _add_movies(url, 2)
    made = dumps.make_dump("scheduled")
    assert made.revision == "0006"
    assert dumps.dump_revision(settings.db_backups_dir / made.file, tools) == "0006"
    migrate.upgrade(url=empty_database_url)
    _sql(
        url,
        "insert into jobs (kind, job_class, state) values ('db_backup', 'maintenance', 'succeeded')",
    )
    _sql(url, "create table public.left_behind (x int)")

    done = dumps.restore(
        settings.db_backups_dir / made.file, confirmed=True, worker_holds_lock=_no_worker
    )

    assert (done.restored_revision, done.revision_now, done.upgraded) == (
        "0006",
        migrate.head_revision(),
        True,
    )
    assert "left_behind" not in _tables(url)
    assert _sql(url, "select count(*) from movies") == [(2,)]
    assert _sql(url, "select count(*) from jobs") == [(0,)]
    ((definition,),) = _sql(
        url,
        "select pg_get_constraintdef(oid) from pg_constraint where conname = 'ck_jobs_job_class'",
    )
    assert "maintenance" in definition


def test_a_load_that_fails_is_rolled_back_whole(database: str, tools: dumps.Tools) -> None:
    # A function the drops leave alone: the dump creates it again and that fails.
    _sql(database, "create function public.marquee_probe() returns int language sql as 'select 1'")
    _add_movies(database, 2)
    made = dumps.make_dump("scheduled")
    _add_movies_from(database, 10, 4)
    before = _snapshot(database)

    with pytest.raises(dumps.DumpError) as raised:
        dumps.restore(
            settings.db_backups_dir / made.file, confirmed=True, worker_holds_lock=_no_worker
        )

    assert "rolled back; the database is as it was" in str(raised.value)
    assert "already exists" in str(raised.value)
    assert _snapshot(database) == before
    assert not [p for p in settings.db_backups_dir.iterdir() if p.name.startswith(".")]


def test_a_dump_cut_short_changes_nothing(database: str, tools: dumps.Tools) -> None:
    _add_movies(database, 50)
    made = dumps.make_dump("scheduled")
    path = settings.db_backups_dir / made.file
    cut = path.parent / "cut.dump"
    cut.write_bytes(path.read_bytes()[: path.stat().st_size * 3 // 4])
    before = _snapshot(database)

    with pytest.raises(dumps.DumpError):
        dumps.restore(cut, confirmed=True, worker_holds_lock=_no_worker)

    assert _snapshot(database) == before


def test_a_restore_refuses_while_something_else_is_connected(
    database: str, tools: dumps.Tools
) -> None:
    made = dumps.make_dump("scheduled")
    with (
        psycopg.connect(database, autocommit=True, application_name="someone-else"),
        pytest.raises(dumps.RestoreRefusedError, match="1 other connection"),
    ):
        dumps.restore(
            settings.db_backups_dir / made.file, confirmed=True, worker_holds_lock=_no_worker
        )
    assert [d.kind for d in dumps.list_dumps()] == ["scheduled"]
