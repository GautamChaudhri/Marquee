"""Fail-closed facade over the removed custom job runtime (JMC2A).

The custom claim/reservation/schedule/recovery runtime and its transitional
schema were deleted with the canonical JMC2A model. Every legacy command
surface now fails closed as unmigrated; the API converts that into a stable
HTTP 503. ``system_noop`` — the only dispatch-enabled definition — is created
through :mod:`marquee.core.jobs.commands` and executed by the PgQueuer roles.
Feature families return on the canonical runtime in later chunks.
"""

from __future__ import annotations

from typing import Any, NoReturn

# Canonical phase vocabulary used by read paths that previously filtered on
# the deleted legacy ``Job.status`` column.
ACTIVE_PHASES: tuple[str, ...] = ("running", "stopping")
PENDING_PHASES: tuple[str, ...] = ("planned", "queued")
TERMINAL_PHASE = "terminal"


class UnmigratedJobPlatformError(RuntimeError):
    """A legacy job-platform command was invoked before its family migrated."""

    def __init__(self, operation: str) -> None:
        super().__init__(
            f"job platform operation {operation!r} is not available: this job "
            "family has not been migrated to the PgQueuer runtime yet"
        )
        self.operation = operation


class JobManager:
    """Every legacy entry point raises; nothing writes job rows here."""

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)

        def _fail_closed(*_args: Any, **_kwargs: Any) -> NoReturn:
            raise UnmigratedJobPlatformError(name)

        return _fail_closed


job_manager = JobManager()
