"""Structured logging setup for Marquee.

Provides two formatters:
  - ``text``  — human-readable for development (default)
  - ``json``  — machine-parseable for production

Usage::

    from marquee.logging import setup_logging
    setup_logging(level="INFO", fmt="text")
"""

from __future__ import annotations

import json
import logging
import sys
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
            "timestamp": datetime.now(UTC).isoformat(),
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

TEXT_FORMAT = (
    "%(asctime)s [%(levelname)-5s] %(name)s: %(message)s"
)


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------


def setup_logging(level: str = "INFO", fmt: str = "text") -> None:
    """Configure the root logger with the chosen format and level.

    Args:
        level: Log level name (DEBUG, INFO, WARNING, ERROR).
        fmt: ``"text"`` (default) or ``"json"``.
    """
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    # Remove any existing handlers (idempotent)
    root.handlers.clear()

    handler = logging.StreamHandler(sys.stderr)
    handler.setLevel(root.level)

    if fmt == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter(TEXT_FORMAT))

    root.addHandler(handler)

    # Silence noisy third-party loggers
    for name in ("asyncpg", "psycopg", "sqlalchemy.engine", "watchfiles"):
        logging.getLogger(name).setLevel(logging.ERROR)
