"""The guard that keeps the database fixture away from every database but its own."""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest


@pytest.mark.parametrize(
    "name",
    [
        "marquee",
        "postgres",
        "template1",
        "marquee_test_",
        "marquee_test",
        "marquee_tests_abc",
        "xmarquee_test_abc",
        "marquee_test_ABC",
        "marquee_test_abc; drop database marquee",
        "marquee_test_abc\0",
    ],
)
def test_the_fixture_refuses_any_other_database(
    test_database_name_check: Callable[[str], str], name: str
) -> None:
    with pytest.raises(ValueError, match="refusing to create or drop"):
        test_database_name_check(name)


def test_the_fixture_accepts_its_own_names(test_database_name_check: Callable[[str], str]) -> None:
    assert test_database_name_check("marquee_test_0a1b2c3d4e5f") == "marquee_test_0a1b2c3d4e5f"


# ── No password in a failure report ─────────────────────────────────────────

_REPO_ROOT = Path(__file__).resolve().parents[1]

# Fails on purpose: connects to a port nothing listens on with a URL derived from the
# fixture's, so the password is in the test's argument, in a library frame's argument
# (psycopg's conninfo), and in the captured output.
_FAILS_ON_PURPOSE = """
import psycopg
from sqlalchemy.engine import make_url


def _connect(conninfo):
    return psycopg.connect(conninfo, connect_timeout=2)


def test_fails_on_purpose(database_url):
    conninfo = make_url(database_url).set(drivername="postgresql", port=1)
    conninfo = conninfo.render_as_string(hide_password=False)
    print("connecting with", conninfo)
    _connect(conninfo)
"""


@pytest.mark.db
@pytest.mark.parametrize(
    "style",
    [[], ["--tb=long", "--showlocals"], ["--tb=short"], ["--tb=native"], ["--tb=line"]],
    ids=["default", "long-showlocals", "short", "native", "line"],
)
def test_a_failing_database_test_prints_no_password(
    database_server: Any, tmp_path: Path, style: list[str]
) -> None:
    password = database_server.url.password
    assert password
    (tmp_path / "conftest.py").write_text(
        (_REPO_ROOT / "tests" / "conftest.py").read_text(encoding="utf-8"), encoding="utf-8"
    )
    (tmp_path / "test_fails_on_purpose.py").write_text(_FAILS_ON_PURPOSE, encoding="utf-8")
    environment = {
        **os.environ,
        "MARQUEE_TEST_DATABASE_URL": database_server.url.render_as_string(hide_password=False),
    }
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-p",
            "no:cacheprovider",
            "--import-mode=importlib",
            "-o",
            "asyncio_mode=auto",
            "--rootdir",
            str(tmp_path),
            "-rA",
            *style,
            str(tmp_path / "test_fails_on_purpose.py"),
        ],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        timeout=120,
    )
    output = result.stdout + result.stderr
    # The inner test failed, as it should, and its report was printed.
    assert result.returncode == 1
    assert "1 failed" in output
    assert "OperationalError" in output
    if style != ["--tb=line"]:
        # The URL was printed, its password hidden.
        assert ":***@127.0.0.1:1/marquee_test_" in output
    leaked = password in output
    assert not leaked, f"the password appears in the {style or 'default'} report"


def test_a_hidden_url_is_the_url_but_prints_without_its_password(
    secret_scrubbing: tuple[type[str], Callable[[str], str]],
) -> None:
    HiddenUrl, scrub_secrets = secret_scrubbing  # noqa: N806 - a class
    url = HiddenUrl("postgresql+psycopg://marquee:s3cret-value@db:5432/marquee_test_ab")
    assert url == "postgresql+psycopg://marquee:s3cret-value@db:5432/marquee_test_ab"
    assert f"{url}".endswith("s3cret-value@db:5432/marquee_test_ab")
    assert repr(url) == "'postgresql+psycopg://marquee:***@db:5432/marquee_test_ab'"
    assert scrub_secrets("x postgresql://u:p%40ss@h/d y") == "x postgresql://u:***@h/d y"
    assert scrub_secrets("http://host:3000/a@b and user@example.org") == (
        "http://host:3000/a@b and user@example.org"
    )
