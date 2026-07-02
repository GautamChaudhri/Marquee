"""Worker-local cancellation signals for running durable jobs."""

from __future__ import annotations

import threading


class JobCancelledError(Exception):
    """Raised by cooperative handlers when a durable job is being cancelled."""


_lock = threading.Lock()
_events: dict[str, threading.Event] = {}


def register(job_id: str) -> threading.Event:
    """Create and store the cancellation event for ``job_id``."""
    event = threading.Event()
    with _lock:
        _events[job_id] = event
    return event


def get(job_id: str) -> threading.Event | None:
    """Return the running job's cancellation event, if this worker owns one."""
    with _lock:
        return _events.get(job_id)


def set_cancelled(job_id: str) -> bool:
    """Set ``job_id``'s event if present. Returns whether an event existed."""
    with _lock:
        event = _events.get(job_id)
    if event is None:
        return False
    event.set()
    return True


def discard(job_id: str) -> None:
    """Forget a job event once the worker attempt is done."""
    with _lock:
        _events.pop(job_id, None)


def raise_if_cancelled(event: threading.Event | None, message: str = "job cancelled") -> None:
    """Raise ``JobCancelledError`` when ``event`` is set."""
    if event is not None and event.is_set():
        raise JobCancelledError(message)
