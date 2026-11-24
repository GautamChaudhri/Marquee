"""Test isolation: no test reads the real .env or touches the real data directory.

Both settings classes are instantiated when ``marquee.config`` is first imported, so the
environment is set here, before any test module imports the package.

Database tests (marked ``db``) run against a throwaway database, ``marquee_test_<random>``,
created on a PostgreSQL server for the session and dropped afterwards. The server comes from,
in order:

1. ``MARQUEE_TEST_DATABASE_URL`` (CI sets it). If it is set and the server cannot be reached,
   the tests fail rather than skip.
2. The ``POSTGRES_*`` values in the repository's ``.env``, read here and only here, and only
   those values: the Compose database on a development machine.
3. Otherwise the database tests skip, saying why.
"""

from __future__ import annotations

import os
import re
import secrets
import shutil
import tempfile
from collections.abc import AsyncIterator, Callable, Iterator
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    import psycopg

_TEST_DATA_DIR = tempfile.mkdtemp(prefix="marquee-test-data-")
os.environ["MARQUEE_ENV_FILE"] = ""
os.environ["DATA_DIR"] = _TEST_DATA_DIR

_REPO_ROOT = Path(__file__).resolve().parents[1]
TEST_DATABASE_PREFIX = "marquee_test_"
_TEST_DATABASE_NAME = re.compile(r"marquee_test_[a-z0-9_]+")
_CONNECT_TIMEOUT_S = 5


def pytest_unconfigure(config: pytest.Config) -> None:
    shutil.rmtree(_TEST_DATA_DIR, ignore_errors=True)


def pytest_sessionstart(session: pytest.Session) -> None:
    from marquee.config import settings

    assert str(settings.DATA_DIR) == _TEST_DATA_DIR, "settings were loaded before conftest ran"


# ── The test database ─────────────────────────────────────────────────────


class UnsafeDatabaseNameError(ValueError):
    """A name the fixture must never create or drop."""


def checked_test_database_name(name: str) -> str:
    """``name`` if it is a throwaway test database's name; raise otherwise.

    The fixture creates and drops databases on a real server. This check is what keeps it
    away from every other database there, the development database included.
    """
    if not name.startswith(TEST_DATABASE_PREFIX) or not _TEST_DATABASE_NAME.fullmatch(name):
        raise UnsafeDatabaseNameError(
            f"refusing to create or drop {name!r}: not a {TEST_DATABASE_PREFIX}* name"
        )
    return name


def _server_from_env_file() -> str | None:
    """A server URL from the repository's .env POSTGRES_* values, or None."""
    from dotenv import dotenv_values
    from sqlalchemy.engine import URL

    env_file = _REPO_ROOT / ".env"
    if not env_file.is_file():
        return None
    values = dotenv_values(env_file)
    if not values.get("POSTGRES_PASSWORD"):
        return None
    return URL.create(
        "postgresql+psycopg",
        username=values.get("POSTGRES_USER") or "marquee",
        password=values["POSTGRES_PASSWORD"],
        host=values.get("POSTGRES_HOST") or "127.0.0.1",
        port=int(values.get("POSTGRES_PORT") or 5432),
        database=values.get("POSTGRES_DB") or "marquee",
    ).render_as_string(hide_password=False)


class _Server:
    """A PostgreSQL server the tests may create throwaway databases on."""

    def __init__(self, url: str) -> None:
        from sqlalchemy.engine import make_url

        self.url = make_url(url).set(drivername="postgresql+psycopg")

    def _admin(self) -> psycopg.Connection:
        import psycopg

        libpq = self.url.set(drivername="postgresql").render_as_string(hide_password=False)
        return psycopg.connect(libpq, autocommit=True, connect_timeout=_CONNECT_TIMEOUT_S)

    def check(self) -> None:
        with self._admin() as conn:
            conn.execute("select 1")

    def url_for(self, name: str) -> str:
        return self.url.set(database=name).render_as_string(hide_password=False)

    def create(self, name: str) -> str:
        from psycopg import sql

        with self._admin() as conn:
            conn.execute(
                sql.SQL("CREATE DATABASE {}").format(
                    sql.Identifier(checked_test_database_name(name))
                )
            )
        return self.url_for(name)

    def drop(self, name: str) -> None:
        from psycopg import sql

        with self._admin() as conn:
            conn.execute(
                sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(
                    sql.Identifier(checked_test_database_name(name))
                )
            )


@pytest.fixture(scope="session")
def database_server() -> _Server:
    explicit = os.environ.get("MARQUEE_TEST_DATABASE_URL")
    url = explicit or _server_from_env_file()
    if url is None:
        pytest.skip("no test database: set MARQUEE_TEST_DATABASE_URL or POSTGRES_* in .env")
    server = _Server(url)
    try:
        server.check()
    except Exception as exc:  # noqa: BLE001 - reported as a skip or a failure below
        reason = f"test database server unreachable: {type(exc).__name__}"
        if explicit:
            pytest.fail(f"MARQUEE_TEST_DATABASE_URL is set but the {reason}")
        pytest.skip(reason)
    return server


def _new_test_database_name() -> str:
    return f"{TEST_DATABASE_PREFIX}{secrets.token_hex(6)}"


@pytest.fixture(scope="session")
def database_url(database_server: _Server) -> Iterator[str]:
    """A migrated throwaway database for the whole session; dropped at the end."""
    from marquee.db import migrate

    name = _new_test_database_name()
    url = database_server.create(name)
    try:
        migrate.upgrade(url=url)
        yield url
    finally:
        database_server.drop(name)


@pytest.fixture
def empty_database_url(database_server: _Server) -> Iterator[str]:
    """A throwaway database no migration has touched, for one test."""
    name = _new_test_database_name()
    url = database_server.create(name)
    try:
        yield url
    finally:
        database_server.drop(name)


@pytest.fixture
async def db_engine(database_url: str) -> AsyncIterator[object]:
    """An async engine on the session's test database. Every row is gone after the test."""
    from sqlalchemy import text

    from marquee.db.base import Base
    from marquee.db.engine import create_engine

    engine = create_engine(database_url)
    try:
        yield engine
    finally:
        tables = ", ".join(Base.metadata.tables)
        async with engine.begin() as conn:
            await conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
        await engine.dispose()


@pytest.fixture
async def db_session(db_engine: object) -> AsyncIterator[object]:
    from marquee.db.engine import create_session_factory

    async with create_session_factory(db_engine)() as session:  # type: ignore[arg-type]
        yield session


@pytest.fixture
def test_database_name_check() -> Callable[[str], str]:
    return checked_test_database_name
