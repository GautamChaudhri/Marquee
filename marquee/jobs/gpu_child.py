"""The GPU child process: where every GPU job runs, so no model loads in the worker.

The worker starts one child, with the spawn method (CUDA does not survive a fork), on the
first GPU job and keeps it between jobs, so whatever a job leaves loaded stays loaded. It is
not a daemon process: the pipeline starts OCR worker processes of its own, and a daemon
process may not have children.

The child leads a process group of its own, which its OCR workers join. So:

- when the child dies mid-job (a crash, an out-of-memory kill, ``kill -9``), the job fails
  with "the GPU process died", and the worker kills whatever is left of the group; the next
  GPU job starts a new child;
- when the worker itself dies, the child sees within a second that its parent changed and
  kills its whole group, itself included;
- the worker stops the child after ``GPU_IDLE_SECONDS`` without a job, which frees the GPU's
  memory, and whenever it stops itself.

Messages, parent to child: ``("run", function, payload)`` or ``("stop",)``. Child to
parent: any number of ``("progress", current, total, message)``, then one of
``("result", value)``, ``("cancelled", message)``, or ``("error", message, permanent)``.

A function that needs asyncio runs its coroutine with :func:`run_async`, on the one event
loop the child keeps for its whole life, never with ``asyncio.run``: the engine creates
asyncio semaphores when it is imported, a semaphore binds to the first loop that waits on
it, and a second loop in the same process then fails every download past the first few.
"""

from __future__ import annotations

import asyncio
import contextlib
import importlib
import json
import logging
import multiprocessing
import os
import signal
import threading
import time
from collections.abc import Coroutine
from multiprocessing.connection import Connection
from typing import Any

from marquee.cancellation import CancellationSignal, JobCancelledError
from marquee.jobs.registry import JobError, PermanentJobError, ProgressFn

logger = logging.getLogger(__name__)

CANCEL_CHECK_S = 0.25
PARENT_CHECK_S = 1.0
STOP_TIMEOUT_S = 15.0


class GpuProcessDiedError(JobError):
    """The child died during a job; the message says how."""


class GpuJobError(JobError):
    """The function raised in the child; the message is its error, already one line."""


def describe_error(exc: BaseException) -> str:
    """One line: the message of a :class:`JobError`, else the type and first line."""
    if isinstance(exc, JobError):
        text = str(exc)
    else:
        lines = str(exc).strip().splitlines()
        text = f"{type(exc).__name__}: {lines[0]}" if lines else type(exc).__name__
    return text[:500]


def _exit_description(code: int | None) -> str:
    if code is None:
        return "exit code unknown"
    if code < 0:
        with contextlib.suppress(ValueError):
            return f"signal {-code}, {signal.Signals(-code).name}"
        return f"signal {-code}"
    return f"exit code {code}"


class GpuChild:
    """The worker's handle on the child: start, run one job at a time, stop."""

    def __init__(self) -> None:
        self._context = multiprocessing.get_context("spawn")
        self._process: Any = None
        self._conn: Connection | None = None
        self._cancel: Any = None
        self._busy = False
        self._idle_since = time.monotonic()

    @property
    def pid(self) -> int | None:
        return self._process.pid if self.alive else None

    @property
    def alive(self) -> bool:
        return self._process is not None and self._process.is_alive()

    def idle_seconds(self) -> float | None:
        """How long the child has waited for a job; ``None`` if busy or not running."""
        if self._busy or not self.alive:
            return None
        return time.monotonic() - self._idle_since

    def _start(self) -> None:
        parent_conn, child_conn = self._context.Pipe(duplex=True)
        self._cancel = self._context.Event()
        self._process = self._context.Process(
            target=_child_main,
            args=(child_conn, self._cancel, os.getpid()),
            name="marquee-gpu",
            daemon=False,
        )
        self._process.start()
        child_conn.close()
        self._conn = parent_conn
        logger.info("GPU | child process %d started", self._process.pid)

    async def run(
        self,
        function: str,
        payload: dict[str, Any],
        cancel: CancellationSignal,
        progress: ProgressFn,
    ) -> Any:
        """Run ``function(payload, cancel, progress)`` in the child and return its result.

        If this coroutine is cancelled (a timeout, or a worker stopping), the job is
        abandoned and the child killed, so it cannot go on working for nobody.
        """
        if not self.alive:
            self._start()
        assert self._conn is not None
        self._busy = True
        try:
            self._cancel.clear()
            self._conn.send(("run", function, payload))
            message = await self._outcome(cancel, progress)
        except asyncio.CancelledError:
            self.kill()
            raise
        finally:
            self._busy = False
            self._idle_since = time.monotonic()
        if message[0] == "result":
            return message[1]
        if message[0] == "cancelled":
            raise JobCancelledError(message[1])
        _, text, permanent = message
        raise (PermanentJobError if permanent else GpuJobError)(text)

    async def _outcome(self, cancel: CancellationSignal, progress: ProgressFn) -> tuple:
        assert self._conn is not None
        loop = asyncio.get_running_loop()
        wake = asyncio.Event()
        fds = (self._conn.fileno(), self._process.sentinel)
        for fd in fds:
            loop.add_reader(fd, wake.set)
        try:
            while True:
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(wake.wait(), CANCEL_CHECK_S)
                wake.clear()
                if cancel.is_set():
                    self._cancel.set()
                while self._conn.poll():
                    try:
                        message = self._conn.recv()
                    except (EOFError, OSError):
                        raise self._died() from None
                    if message[0] != "progress":
                        return message
                    progress(*message[1:])
                if not self._process.is_alive():
                    raise self._died()
        finally:
            for fd in fds:
                loop.remove_reader(fd)

    def _died(self) -> GpuProcessDiedError:
        self._kill_group()
        self._process.join(5)
        code = self._process.exitcode
        self.kill()
        error = GpuProcessDiedError(f"the GPU process died ({_exit_description(code)})")
        logger.warning("GPU | %s", error)
        return error

    def _kill_group(self) -> None:
        # The child's pid is its group's id. While any member is left (a zombie leader
        # included) the kernel does not hand that id to another process.
        if self._process is not None and self._process.pid is not None:
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(self._process.pid, signal.SIGKILL)

    def kill(self) -> None:
        """Kill the child and everything in its process group, now."""
        if self._process is None:
            return
        self._kill_group()
        if self._process.is_alive():
            self._process.kill()
        self._process.join(5)
        if self._conn is not None:
            self._conn.close()
        self._process, self._conn, self._cancel = None, None, None

    def stop(self, timeout: float = STOP_TIMEOUT_S) -> None:
        """Ask the child to exit, wait for it, then kill whatever is left. Blocking."""
        if self._process is None:
            return
        if self._process.is_alive() and self._conn is not None:
            with contextlib.suppress(OSError, ValueError):
                self._conn.send(("stop",))
            self._process.join(timeout)
        pid = self._process.pid
        self.kill()
        logger.info("GPU | child process %s stopped", pid)


# ── In the child ──────────────────────────────────────────────────────────


def _child_main(conn: Connection, cancel: Any, parent_pid: int) -> None:
    os.setpgrp()
    # Ctrl-C reaches the worker, which stops the child itself.
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    threading.Thread(target=_watch_parent, args=(parent_pid,), daemon=True).start()
    _child_logging()
    send_lock = threading.Lock()

    def send(message: tuple) -> None:
        with send_lock:
            conn.send(message)

    def progress(
        current: int | None = None, total: int | None = None, message: str | None = None
    ) -> None:
        send(("progress", current, total, message))

    while True:
        try:
            request = conn.recv()
        except EOFError:
            break
        if request[0] == "stop":
            break
        _, function, payload = request
        send(_run_one(function, payload, cancel, progress))
    conn.close()


def _watch_parent(parent_pid: int) -> None:
    """If the worker dies, take the whole group down: nothing must outlive it."""
    while True:
        if os.getppid() != parent_pid:
            os.killpg(0, signal.SIGKILL)
        time.sleep(PARENT_CHECK_S)


def _child_logging() -> None:
    from marquee.logging import setup_logging

    # The console shows warnings; the pipeline's run log still records INFO lines, as it
    # does for `marquee run`.
    setup_logging(level="WARNING")
    logging.getLogger("marquee").setLevel(logging.INFO)
    logging.captureWarnings(True)


_loop: asyncio.AbstractEventLoop | None = None


def run_async[T](coroutine: Coroutine[Any, Any, T]) -> T:
    """Run ``coroutine`` to the end on this child's one event loop.

    Like ``asyncio.run``, it then cancels whatever tasks the coroutine left behind, so
    nothing from one job runs into the next; unlike it, the loop is kept.
    """
    global _loop
    if _loop is None:
        _loop = asyncio.new_event_loop()
        asyncio.set_event_loop(_loop)
    try:
        return _loop.run_until_complete(coroutine)
    finally:
        leftovers = asyncio.all_tasks(_loop)
        for task in leftovers:
            task.cancel()
        if leftovers:
            _loop.run_until_complete(asyncio.gather(*leftovers, return_exceptions=True))


def _resolve(function: str) -> Any:
    module, _, name = function.partition(":")
    return getattr(importlib.import_module(module), name)


def _run_one(function: str, payload: dict[str, Any], cancel: Any, progress: ProgressFn) -> tuple:
    try:
        result = _resolve(function)(payload, cancel, progress)
        json.dumps(result, allow_nan=False)
    except JobCancelledError as exc:
        return ("cancelled", str(exc) or "cancelled")
    except Exception as exc:  # noqa: BLE001 - the boundary: report it to the worker
        logger.exception("GPU | %s failed", function)
        return ("error", describe_error(exc), isinstance(exc, PermanentJobError))
    return ("result", result)
