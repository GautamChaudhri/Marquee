"""Supervise the embedded worker/scheduler subprocesses from the API lifespan."""

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

MAX_CONSECUTIVE_FAST_FAILURES = 5
FAST_FAILURE_WINDOW_SECONDS = 30


def _set_pdeathsig() -> None:  # pragma: no cover
    if sys.platform != "linux":
        return
    try:
        import ctypes  # noqa: PLC0415

        ctypes.CDLL("libc.so.6", use_errno=True).prctl(1, signal.SIGTERM)
    except Exception:  # noqa: BLE001
        pass


class _Child:
    __slots__ = ("name", "args", "proc", "fast_failures", "degraded", "env")

    def __init__(
        self,
        name: str,
        args: list[str],
        *,
        env: dict[str, str] | None = None,
    ) -> None:
        self.name = name
        self.args = args
        self.proc: asyncio.subprocess.Process | None = None
        self.fast_failures = 0
        self.degraded = False
        self.env = env


class WorkerSupervisor:
    """Owns the lifecycle of the embedded scheduler + worker subprocesses."""

    def __init__(self) -> None:
        self._children: list[_Child] = []
        self._tasks: list[asyncio.Task] = []
        self._shutting_down = False

    def _plan(self) -> list[_Child]:
        scheduler_env = {**os.environ, "MARQUEE_PROCESS_ROLE": "scheduler"}
        children = [
            _Child(
                "scheduler",
                ["-m", "marquee.core.jobs.pgqueuer_scheduler"],
                env=scheduler_env,
            )
        ]
        for index in range(max(1, settings.JOB_EMBEDDED_WORKER_COUNT)):
            worker_env = {**os.environ, "MARQUEE_PROCESS_ROLE": "worker"}
            children.append(
                _Child(
                    f"worker-{index}",
                    ["-m", "marquee.core.jobs.pgqueuer_worker"],
                    env=worker_env,
                )
            )
        return children

    async def start(self) -> None:
        self._shutting_down = False
        self._children = self._plan()
        for child in self._children:
            await self._spawn(child)
            self._tasks.append(
                asyncio.create_task(self._supervise(child), name=f"supervise-{child.name}")
            )
        logger.info("Embedded job runtime started: %s", ", ".join(c.name for c in self._children))

    def status(self) -> dict:
        children = [
            {
                "name": child.name,
                "running": child.proc is not None and child.proc.returncode is None,
                "degraded": child.degraded,
                "fast_failures": child.fast_failures,
            }
            for child in self._children
        ]
        return {"degraded": any(c["degraded"] for c in children), "children": children}

    async def _spawn(self, child: _Child) -> None:
        child.proc = await asyncio.create_subprocess_exec(
            sys.executable,
            *child.args,
            start_new_session=True,
            preexec_fn=_set_pdeathsig,
            env=child.env,
        )
        logger.info("Spawned %s (pid=%s)", child.name, child.proc.pid)

    async def _supervise(self, child: _Child) -> None:
        backoff = 1.0
        while not self._shutting_down:
            proc = child.proc
            if proc is None:
                return
            started = time.monotonic()
            returncode = await proc.wait()
            if self._shutting_down:
                return
            ran_seconds = time.monotonic() - started
            if ran_seconds > FAST_FAILURE_WINDOW_SECONDS:
                backoff = 1.0
                child.fast_failures = 0
            else:
                child.fast_failures += 1
            if child.fast_failures >= MAX_CONSECUTIVE_FAST_FAILURES:
                child.degraded = True
                logger.critical(
                    "%s has crashed %d times in a row within %ds of starting (rc=%s) — giving up.",
                    child.name,
                    child.fast_failures,
                    FAST_FAILURE_WINDOW_SECONDS,
                    returncode,
                )
                return
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
                child.degraded = True
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
