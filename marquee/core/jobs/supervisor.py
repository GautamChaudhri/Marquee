"""Supervise embedded worker/scheduler subprocesses from the API lifespan.

Standalone dev runs the API with ``uvicorn``; rather than make the operator
start ``python -m marquee.core.jobs.worker`` by hand, the API spawns the worker
and scheduler as *child processes* in their own process groups.

Why subprocesses (not asyncio tasks): heavy GPU/ffmpeg/inference work then never
runs on the API event loop, so the UI never stalls.  Because each child leads
its own session/process-group, shutdown signals the *whole group* — the worker
and any ffmpeg/Paddle child it spawned die together, leaving nothing orphaned.

Set ``JOB_EMBEDDED_WORKERS=false`` to disable (the Compose topology runs
dedicated ``marquee-worker``/``marquee-scheduler`` services instead).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import signal
import sys
import time

from marquee.config import settings

logger = logging.getLogger(__name__)


def _set_pdeathsig() -> None:  # pragma: no cover - runs in the forked child
    """Ask the kernel to SIGTERM this child if the parent (API) dies abruptly.

    Defense in depth for ungraceful API death (e.g. a hard ``--reload`` kill or
    ``SIGKILL``) where the lifespan shutdown never runs.  Linux-only; a no-op
    elsewhere.
    """
    if sys.platform != "linux":
        return
    try:
        import ctypes  # noqa: PLC0415

        pr_set_pdeathsig = 1
        ctypes.CDLL("libc.so.6", use_errno=True).prctl(pr_set_pdeathsig, signal.SIGTERM)
    except Exception:  # noqa: BLE001 - best effort; group-kill is the primary path
        pass


class _Child:
    __slots__ = ("name", "args", "proc")

    def __init__(self, name: str, args: list[str]) -> None:
        self.name = name
        self.args = args
        self.proc: asyncio.subprocess.Process | None = None


class WorkerSupervisor:
    """Owns the lifecycle of the embedded scheduler + worker subprocesses."""

    def __init__(self) -> None:
        self._children: list[_Child] = []
        self._tasks: list[asyncio.Task] = []
        self._shutting_down = False

    def _plan(self) -> list[_Child]:
        children = [_Child("scheduler", ["-m", "marquee.core.jobs.scheduler"])]
        for index in range(max(1, settings.JOB_EMBEDDED_WORKER_COUNT)):
            children.append(_Child(f"worker-{index}", ["-m", "marquee.core.jobs.worker"]))
        return children

    async def start(self) -> None:
        self._shutting_down = False
        self._children = self._plan()
        for child in self._children:
            await self._spawn(child)
            self._tasks.append(
                asyncio.create_task(self._supervise(child), name=f"supervise-{child.name}")
            )
        logger.info(
            "Embedded job runtime started: %s", ", ".join(c.name for c in self._children)
        )

    async def _spawn(self, child: _Child) -> None:
        child.proc = await asyncio.create_subprocess_exec(
            sys.executable,
            *child.args,
            # New session => child is its own process-group leader, so a single
            # killpg() on shutdown reaps the worker plus any ffmpeg/Paddle
            # subprocesses it started.
            start_new_session=True,
            preexec_fn=_set_pdeathsig,
        )
        logger.info("Spawned %s (pid=%s)", child.name, child.proc.pid)

    async def _supervise(self, child: _Child) -> None:
        """Respawn a child that dies unexpectedly, with capped backoff."""
        backoff = 1.0
        while not self._shutting_down:
            proc = child.proc
            if proc is None:
                return
            started = time.monotonic()
            returncode = await proc.wait()
            if self._shutting_down:
                return
            if time.monotonic() - started > 30:
                backoff = 1.0  # it ran a while; treat this as a fresh failure
            logger.warning(
                "%s exited (rc=%s); respawning in %.0fs", child.name, returncode, backoff
            )
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30.0)
            if self._shutting_down:
                return
            try:
                await self._spawn(child)
            except Exception:  # noqa: BLE001
                logger.exception("respawn of %s failed", child.name)
                return

    def _signal_group(self, proc: asyncio.subprocess.Process, sig: int) -> None:
        try:
            os.killpg(os.getpgid(proc.pid), sig)
        except ProcessLookupError:
            pass
        except OSError:
            with contextlib.suppress(ProcessLookupError):
                proc.send_signal(sig)

    async def shutdown(self) -> None:
        """Stop respawning and tear the whole worker tree down — no orphans.

        The hard SIGKILL escalation lives in ``finally`` and uses only the
        synchronous ``killpg`` syscall, so it still runs if the caller's overall
        shutdown budget (``SHUTDOWN_TIMEOUT_SECONDS``) cancels us mid-drain.
        Killed children become short-lived zombies reaped when the API exits.
        """
        self._shutting_down = True
        for task in self._tasks:
            task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            if self._tasks:
                await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()

        alive = [c.proc for c in self._children if c.proc is not None and c.proc.returncode is None]
        for proc in alive:
            self._signal_group(proc, signal.SIGTERM)

        grace = settings.JOB_SHUTDOWN_GRACE_SECONDS
        try:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(
                    asyncio.gather(*(proc.wait() for proc in alive), return_exceptions=True),
                    timeout=grace,
                )
        finally:
            for proc in alive:
                if proc.returncode is None:
                    logger.warning("worker pid=%s did not stop — SIGKILL", proc.pid)
                    self._signal_group(proc, signal.SIGKILL)
            logger.info("Embedded job runtime stopped.")
