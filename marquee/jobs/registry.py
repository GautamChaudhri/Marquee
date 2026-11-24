"""Job kinds, and how many jobs of each class run at once.

A job kind is a name, a class, the work it does, how many times a job may start, and how
long one attempt may take. A ``gpu`` kind names a function the GPU child process runs
(``"module:function"``, imported only in the child, so the worker never loads a model);
every other kind has an async handler the worker runs as a task.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, Literal

from marquee.cancellation import CancellationSignal

JobClass = Literal["gpu", "network", "media_read", "media_write"]

# One GPU; the network class is mostly waiting; the media share is slow and shared.
CLASS_LIMITS: Mapping[str, int] = {"gpu": 1, "network": 4, "media_read": 2, "media_write": 1}
BATCH = "batch"
"""The kind and class of a batch's parent row, which the worker never runs."""


class JobError(Exception):
    """A failure whose message already says what happened; it is stored as it is."""


class PermanentJobError(JobError):
    """A failure no retry can fix: the job fails without using its remaining attempts."""


ProgressFn = Callable[[int | None, int | None, str | None], None]
"""``progress(current, total, message)``; any of the three may be ``None``."""


@dataclass(frozen=True)
class JobContext:
    """What a handler gets: its job, its payload, a cancel signal, and a progress function."""

    job_id: int
    payload: dict[str, Any]
    cancel: CancellationSignal
    progress: ProgressFn


Handler = Callable[[JobContext], Awaitable[Any]]


@dataclass(frozen=True)
class JobKind:
    name: str
    job_class: JobClass
    handler: Handler | None = None
    """For every class but ``gpu``: run by the worker; its return value is the result."""
    gpu_function: str | None = None
    """For ``gpu``: ``"module:function"``, called in the child as
    ``function(payload, cancel, progress)``; it returns the result."""
    max_attempts: int = 1
    timeout_s: float = 600.0
    dedupe_key: str | None = None
    """Set for kinds that must never have two jobs queued or running at once."""

    def __post_init__(self) -> None:
        if self.job_class not in CLASS_LIMITS:
            raise ValueError(f"job kind {self.name!r}: unknown class {self.job_class!r}")
        if self.job_class == "gpu" and (self.gpu_function is None or self.handler is not None):
            raise ValueError(f"job kind {self.name!r}: a gpu kind names only a gpu_function")
        if self.job_class != "gpu" and (self.handler is None or self.gpu_function is not None):
            raise ValueError(f"job kind {self.name!r}: a {self.job_class} kind needs a handler")
        if self.max_attempts < 1 or self.timeout_s <= 0:
            raise ValueError(f"job kind {self.name!r}: attempts and timeout must be positive")


Registry = Mapping[str, JobKind]


def free_classes(running: Mapping[str, int], limits: Mapping[str, int] = CLASS_LIMITS) -> list[str]:
    """The classes with a free slot, given how many jobs of each are running."""
    return [name for name, limit in limits.items() if running.get(name, 0) < limit]
