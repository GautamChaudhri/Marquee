"""Job kinds, and how many jobs of each class run at once.

A job kind is a name, a class, the work it does, how many times a job may start, and how
long one attempt may take. A ``gpu`` kind either names a function the GPU child process runs
(``"module:function"``, imported only in the child, so the worker never loads a model), or
has an async handler the worker runs as a task, which calls the child through
:meth:`JobContext.gpu` for the part that needs the GPU. Every other kind has a handler, and
its handler may not call the child.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, Literal

from marquee.cancellation import CancellationSignal

JobClass = Literal["gpu", "network", "media_read", "media_write", "maintenance"]

# One GPU; the network class is mostly waiting; the media share is slow and shared; the
# database backup and retention run one at a time, beside everything else.
CLASS_LIMITS: Mapping[str, int] = {
    "gpu": 1,
    "network": 4,
    "media_read": 2,
    "media_write": 1,
    "maintenance": 1,
}
BATCH = "batch"
"""The kind and class of a batch's parent row, which the worker never runs."""


class JobError(Exception):
    """A failure whose message already says what happened; it is stored as it is."""


class PermanentJobError(JobError):
    """A failure no retry can fix: the job fails without using its remaining attempts."""


class GpuNotAllowedError(PermanentJobError):
    """A handler whose kind is not of class ``gpu`` asked for the GPU child."""


ProgressFn = Callable[[int | None, int | None, str | None], None]
"""``progress(current, total, message)``; any of the three may be ``None``."""

GpuRunner = Callable[[str, dict[str, Any]], Awaitable[Any]]
"""``await runner("module:function", payload)``: run a function in the GPU child."""


@dataclass(frozen=True)
class JobContext:
    """What a handler gets: its job, its payload, a cancel signal, and a progress function.

    A handler of a ``gpu`` kind also gets ``gpu_runner``, which :meth:`gpu` calls.
    """

    job_id: int
    payload: dict[str, Any]
    cancel: CancellationSignal
    progress: ProgressFn
    gpu_runner: GpuRunner | None = None

    async def gpu(self, function: str, payload: dict[str, Any]) -> Any:
        """Run ``function(payload, cancel, progress)`` in the GPU child and return its result.

        It goes through the worker's GPU lock, with this job's cancel signal and progress
        function. Only a handler of a ``gpu`` kind may call it.
        """
        if self.gpu_runner is None:
            raise GpuNotAllowedError(
                f"job {self.job_id} is not of class gpu, so it cannot use the GPU child"
            )
        return await self.gpu_runner(function, payload)


Handler = Callable[[JobContext], Awaitable[Any]]


@dataclass(frozen=True)
class JobKind:
    name: str
    job_class: JobClass
    handler: Handler | None = None
    """Run by the worker; its return value is the result. A ``gpu`` kind's handler calls
    the child through ``ctx.gpu``."""
    gpu_function: str | None = None
    """For ``gpu`` only, instead of a handler: ``"module:function"``, called in the child as
    ``function(payload, cancel, progress)``; it returns the result."""
    max_attempts: int = 1
    timeout_s: float = 600.0
    dedupe_key: str | None = None
    """Set for kinds that must never have two jobs queued or running at once under one key.
    It may name payload fields in braces (``poster_run:{movie_id}``): one key per value."""

    def __post_init__(self) -> None:
        if self.job_class not in CLASS_LIMITS:
            raise ValueError(f"job kind {self.name!r}: unknown class {self.job_class!r}")
        if self.job_class == "gpu" and (self.gpu_function is None) == (self.handler is None):
            raise ValueError(
                f"job kind {self.name!r}: a gpu kind has either a gpu_function or a handler"
            )
        if self.job_class != "gpu" and (self.handler is None or self.gpu_function is not None):
            raise ValueError(f"job kind {self.name!r}: a {self.job_class} kind needs a handler")
        if self.max_attempts < 1 or self.timeout_s <= 0:
            raise ValueError(f"job kind {self.name!r}: attempts and timeout must be positive")

    def dedupe_key_for(self, payload: Mapping[str, Any]) -> str | None:
        """The dedupe key of a job of this kind with this payload, if the kind has one."""
        if self.dedupe_key is None:
            return None
        try:
            return self.dedupe_key.format_map(payload)
        except (KeyError, IndexError, AttributeError, ValueError) as exc:
            raise ValueError(
                f"job kind {self.name!r}: the payload does not fill its dedupe key "
                f"{self.dedupe_key!r} ({exc})"
            ) from None


Registry = Mapping[str, JobKind]


def free_classes(running: Mapping[str, int], limits: Mapping[str, int] = CLASS_LIMITS) -> list[str]:
    """The classes with a free slot, given how many jobs of each are running."""
    return [name for name, limit in limits.items() if running.get(name, 0) < limit]


def default_registry() -> dict[str, JobKind]:
    """Every job kind Marquee has, by name."""
    from marquee.jobs.handlers import KINDS

    return {kind.name: kind for kind in KINDS}
