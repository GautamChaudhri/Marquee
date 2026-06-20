"""Server-side job handler registry.

Routes never choose arbitrary callables.  They enqueue a validated domain type
and workers resolve that type here.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from marquee.models import Job

JobHandler = Callable[[Job], Awaitable[dict[str, Any] | None]]
_handlers: dict[str, JobHandler] = {}


def register(job_type: str):
    def decorator(handler: JobHandler) -> JobHandler:
        if job_type in _handlers:
            raise RuntimeError(f"duplicate job handler: {job_type}")
        _handlers[job_type] = handler
        return handler

    return decorator


def resolve(job_type: str) -> JobHandler | None:
    return _handlers.get(job_type)


def registered_types() -> set[str]:
    return set(_handlers)


@register("system_noop")
async def system_noop(job: Job) -> dict[str, Any]:
    """Small diagnostic handler used to validate deployment and job plumbing."""
    return {"echo": job.payload}
