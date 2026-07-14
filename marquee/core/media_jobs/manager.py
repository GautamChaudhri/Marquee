"""Fail-closed facade over the removed MediaJob lifecycle (JMC2A).

The duplicate ``MediaJob``/``MediaJobEvent``/``MediaBatch`` lifecycle was
deleted with the canonical JMC2A schema. Media operations return as canonical
jobs with a strict 1:1 ``MediaOperationDetail`` in later chunks; until then,
every media-job command fails closed as unmigrated and the API converts that
into a stable HTTP 503.
"""

from __future__ import annotations

from typing import Any, NoReturn

from marquee.core.jobs.manager import UnmigratedJobPlatformError


class MediaJobManager:
    """Every legacy media-job entry point raises; nothing writes rows here."""

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)

        def _fail_closed(*_args: Any, **_kwargs: Any) -> NoReturn:
            raise UnmigratedJobPlatformError(f"media_job.{name}")

        return _fail_closed


media_job_manager = MediaJobManager()
