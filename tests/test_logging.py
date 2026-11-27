from __future__ import annotations

import json
import logging
import time
from collections.abc import Iterator

import pytest

from marquee.logging import JsonFormatter, UtcFormatter, setup_logging

# 2026-11-26 01:02:03.456 UTC
WHEN = 1795654923.456


@pytest.fixture(autouse=True)
def _restore_logging() -> Iterator[None]:
    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    converter = logging.Formatter.converter
    names = ("httpx", "httpcore", "httpx2", "httpcore2", "alembic.runtime.plugins", "psycopg")
    levels = {name: logging.getLogger(name).level for name in names}
    yield
    root.handlers[:] = handlers
    root.setLevel(level)
    logging.Formatter.converter = converter
    for name, value in levels.items():
        logging.getLogger(name).setLevel(value)


def _record(created: float = WHEN) -> logging.LogRecord:
    record = logging.LogRecord("marquee.test", logging.INFO, __file__, 1, "hello", None, None)
    record.created = created
    record.msecs = round((created - int(created)) * 1000)
    return record


@pytest.fixture
def far_from_utc(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """A machine whose local time is 10 hours behind UTC."""
    monkeypatch.setenv("TZ", "Pacific/Honolulu")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


def test_the_text_format_is_utc_and_says_so(far_from_utc: None) -> None:
    line = UtcFormatter("%(asctime)s %(message)s").format(_record())
    assert line == "2026-11-26T01:02:03.456Z hello"


def test_the_json_format_is_utc_and_uses_the_records_time(far_from_utc: None) -> None:
    payload = json.loads(JsonFormatter().format(_record()))
    assert payload["timestamp"] == "2026-11-26T01:02:03.456000+00:00"


def test_setup_makes_every_other_formatter_utc(far_from_utc: None) -> None:
    """The pipeline's run log builds its own plain formatter."""
    logging.Formatter.converter = time.localtime  # as in a process that never set up logging
    plain = logging.Formatter("%(asctime)s")
    assert plain.format(_record()).startswith("2026-11-25 15:02:03")
    setup_logging("INFO")
    assert plain.format(_record()).startswith("2026-11-26 01:02:03")


def test_the_root_handler_writes_utc(far_from_utc: None) -> None:
    setup_logging("INFO")
    (handler,) = logging.getLogger().handlers
    assert handler.format(_record()).startswith("2026-11-26T01:02:03.456Z [INFO ] marquee.test:")


def test_request_lines_from_httpx_stay_out_of_the_logs() -> None:
    setup_logging("DEBUG")
    seen: list[str] = []
    collector = logging.Handler()
    collector.emit = lambda record: seen.append(record.getMessage())  # type: ignore[method-assign]
    logging.getLogger().addHandler(collector)
    logging.getLogger("httpx").info('HTTP Request: GET http://radarr.example.lan/api "200 OK"')
    logging.getLogger("httpcore.http11").debug("send_request_headers.started")
    logging.getLogger("httpx").warning("a real problem")
    assert seen == ["a real problem"]


def test_the_second_httpx_and_alembic_plugins_stay_quiet() -> None:
    setup_logging("DEBUG")
    seen: list[str] = []
    collector = logging.Handler()
    collector.emit = lambda record: seen.append(record.getMessage())  # type: ignore[method-assign]
    logging.getLogger().addHandler(collector)
    logging.getLogger("httpx2").info('HTTP Request: GET https://huggingface.co/x "200 OK"')
    logging.getLogger("httpcore2.http11").debug("receive_response_headers.complete")
    logging.getLogger("alembic.runtime.plugins").info("setup plugin alembic.autogenerate.types")
    logging.getLogger("alembic.runtime.migration").info("Running upgrade 0006 -> 0007")
    logging.getLogger("httpx2").warning("a real problem")
    assert seen == ["Running upgrade 0006 -> 0007", "a real problem"]
