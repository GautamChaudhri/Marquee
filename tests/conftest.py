"""Test isolation: no test reads the real .env or touches the real data directory.

Both settings classes are instantiated when ``marquee.config`` is first imported, so the
environment is set here, before any test module imports the package.
"""

from __future__ import annotations

import os
import shutil
import tempfile

import pytest

_TEST_DATA_DIR = tempfile.mkdtemp(prefix="marquee-test-data-")
os.environ["MARQUEE_ENV_FILE"] = ""
os.environ["DATA_DIR"] = _TEST_DATA_DIR


def pytest_unconfigure(config: pytest.Config) -> None:
    shutil.rmtree(_TEST_DATA_DIR, ignore_errors=True)


def pytest_sessionstart(session: pytest.Session) -> None:
    from marquee.config import settings

    assert str(settings.DATA_DIR) == _TEST_DATA_DIR, "settings were loaded before conftest ran"
