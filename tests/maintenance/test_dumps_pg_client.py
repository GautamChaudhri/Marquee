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
