"""Deterministic registration of every poster-family handler.

Importing this module binds each family's ``execute(context)`` into ``EXECUTION_HANDLERS`` via
``register_execution_handler`` (import side effect).  It is imported at the end of ``delivery.py``
so registration happens whenever the delivery kernel is imported — worker, API, and tests — with
no circular-import hazard (``ExecutionContext`` and ``register_execution_handler`` are defined
before that import runs).

This module is the single place that decides which families the kernel can execute, so it is
also the scope boundary: anything not listed here is not part of the product.  Keep it limited
to poster analysis, poster artwork mutation, the ML/taste engine that feeds ranking, library
sync, and shared maintenance.
"""

from __future__ import annotations

from marquee.core.jobs import (
    handlers_library,  # noqa: F401  (registration side effect)
    handlers_maintenance,  # noqa: F401  (registration side effect)
    handlers_ml,  # noqa: F401  (registration side effect)
    handlers_poster_mutations,  # noqa: F401  (registration side effect)
    handlers_posters,  # noqa: F401  (registration side effect)
    handlers_rescan,  # noqa: F401  (registration side effect)
)
