"""Structured logging setup for Marquee.

Provides two formatters:
  - ``text``  — human-readable for development (default)
  - ``json``  — machine-parseable for production

Every time in a log is UTC, on the host and in a container alike, and says so: the text
format ends its time with ``Z``. Setting up logging also makes every other formatter in the
process (the pipeline's run log, for one) write UTC.

Usage::

    from marquee.logging import setup_logging
    setup_logging(level="INFO", fmt="text")
"""

from __future__ import annotations

import json
import logging
import sys
import time
from datetime import UTC, datetime

# ---------------------------------------------------------------------------
# JSON Formatter
# ---------------------------------------------------------------------------


class JsonFormatter(logging.Formatter):
    """Emit log records as single-line JSON objects.

    Fields: timestamp, level, logger, message, and any extra keys
    passed via ``extra=`` dict (e.g. ``logger.info("msg", extra={"movie_id": 1})``).
    """

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        # Carry forward any extra fields added via logger.info(..., extra={...})
        for key in ("entity", "entity_id", "source", "count", "duration_ms"):
            val = getattr(record, key, None)
            if val is not None:
                payload[key] = val

        if record.exc_info and record.exc_info[1]:
            payload["error"] = str(record.exc_info[1])

        return json.dumps(payload, default=str)


# ---------------------------------------------------------------------------
# Text Formatter (development)
# ---------------------------------------------------------------------------

TEXT_FORMAT = "%(asctime)s [%(levelname)-5s] %(name)s: %(message)s"


class UtcFormatter(logging.Formatter):
    """``2026-11-26T01:02:03.456Z``: UTC, whatever the machine's time zone."""

    converter = time.gmtime
    default_time_format = "%Y-%m-%dT%H:%M:%S"
    default_msec_format = "%s.%03dZ"


# Loggers that would print what must stay out of the logs. httpx logs every request at
# INFO with its full URL: Radarr's address on the LAN, TMDB's URLs. huggingface_hub, which
# PaddleX downloads the OCR models through, carries a second copy under other names
# (httpx2, httpcore2). Alembic names each of its plugins at INFO whenever it loads.
_WARNING_ONLY = ("httpx", "httpcore", "httpx2", "httpcore2", "alembic.runtime.plugins")
_ERROR_ONLY = ("asyncpg", "psycopg", "sqlalchemy.engine", "watchfiles")


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------


def setup_logging(level: str = "INFO", fmt: str = "text") -> None:
    """Configure the root logger with the chosen format and level.

    Args:
        level: Log level name (DEBUG, INFO, WARNING, ERROR).
        fmt: ``"text"`` (default) or ``"json"``.
    """
    # Formatters made elsewhere (the pipeline's run log) take their time from this.
    logging.Formatter.converter = time.gmtime

    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    # Remove any existing handlers (idempotent)
    root.handlers.clear()

    handler = logging.StreamHandler(sys.stderr)
    handler.setLevel(root.level)

    if fmt == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(UtcFormatter(TEXT_FORMAT))

    root.addHandler(handler)

    for name in _WARNING_ONLY:
        logging.getLogger(name).setLevel(logging.WARNING)
    for name in _ERROR_ONLY:
        logging.getLogger(name).setLevel(logging.ERROR)
