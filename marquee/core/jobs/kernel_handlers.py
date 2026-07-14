"""Deterministic registration of every migrated read-only family handler.

Importing this module binds each family's ``execute(context)`` into ``EXECUTION_HANDLERS`` via
``register_execution_handler`` (import side effect).  It is imported at the end of ``delivery.py``
so registration happens whenever the delivery kernel is imported — worker, API, and tests — with
no circular-import hazard (``ExecutionContext`` and ``register_execution_handler`` are defined
before that import runs).  New JMC4B families add their handler module here.
"""

from __future__ import annotations

from marquee.core.jobs import (
    handlers_dovi,  # noqa: F401  (registration side effect)
    handlers_letterbox,  # noqa: F401  (registration side effect)
    handlers_library,  # noqa: F401  (registration side effect)
    handlers_subtitles,  # noqa: F401  (registration side effect)
)
