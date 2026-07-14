"""Fail-closed compatibility boundary for unmigrated child-process execution."""

from __future__ import annotations

from typing import NoReturn

from marquee.core.jobs.manager import UnmigratedJobPlatformError


def _unmigrated() -> NoReturn:
    raise UnmigratedJobPlatformError("child_process_tracking")


async def record_child_pid(*_args: object, **_kwargs: object) -> NoReturn:
    _unmigrated()


async def clear_child_pid(*_args: object, **_kwargs: object) -> NoReturn:
    _unmigrated()
