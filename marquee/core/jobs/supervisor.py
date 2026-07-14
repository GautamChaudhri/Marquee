"""Supervise embedded worker/scheduler/Subgen subprocesses from the API lifespan."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import signal
import sys
import time

from marquee.config import settings
from marquee.core.subtitles.config import subtitle_settings
from marquee.core.subtitles.embedded_subgen import EmbeddedSubgenStatus, build_spawn_spec

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
    __slots__ = (
        "name",
        "args",
        "proc",
        "fast_failures",
        "degraded",
        "env",
        "capture_output",
        "status",
        "log_tasks",
    )

    def __init__(
        self,
        name: str,
        args: list[str],
        *,
        env: dict[str, str] | None = None,
        capture_output: bool = False,
    ) -> None:
        self.name = name
        self.args = args
        self.proc: asyncio.subprocess.Process | None = None
        self.fast_failures = 0
        self.degraded = False
        self.env = env
        self.capture_output = capture_output
        self.status = EmbeddedSubgenStatus() if name == "subgen" else None
        self.log_tasks: list[asyncio.Task] = []


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
        if subtitle_settings.subgen_deployment == "embedded":
            spec = build_spawn_spec()
            children.append(
                _Child("subgen", spec["args"], env=spec["env"], capture_output=True)
            )
        return children

    def _child_named(self, name: str) -> _Child | None:
        return next((child for child in self._children if child.name == name), None)

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
        children = []
        for child in self._children:
            item = {
                "name": child.name,
                "running": child.proc is not None and child.proc.returncode is None,
                "degraded": child.degraded,
                "fast_failures": child.fast_failures,
            }
            if child.name == "subgen" and child.status is not None:
                item["queue_processing"] = child.status.queue_processing
                item["queue_queued"] = child.status.queue_queued
                item["last_activity_line"] = child.status.last_activity_line
            children.append(item)
        return {"degraded": any(c["degraded"] for c in children), "children": children}

    def subgen_status(self) -> dict:
        child = self._child_named("subgen")
        if child is None or child.status is None:
            return {"state": "disabled", "logs": [], "queue_processing": 0, "queue_queued": 0}
        running = child.proc is not None and child.proc.returncode is None
        return {
            "state": child.status.state if child.status.state != "disabled" else ("running" if running else "starting"),
            "running": running,
            "degraded": child.degraded,
            "fast_failures": child.fast_failures,
            "queue_processing": child.status.queue_processing,
            "queue_queued": child.status.queue_queued,
            "last_activity_line": child.status.last_activity_line,
            "last_download_line": child.status.last_download_line,
            "last_model_line": child.status.last_model_line,
        }

    def subgen_logs(self, tail: int = 200) -> list[str]:
        child = self._child_named("subgen")
        if child is None or child.status is None:
            return []
        return list(child.status.logs)[-max(1, min(tail, 500)) :]

    async def restart_subgen(self) -> dict:
        child = self._child_named("subgen")
        if child is None:
            return {"restarted": False, "detail": "embedded subgen disabled"}
        if child.proc is not None and child.proc.returncode is None:
            self._signal_group(child.proc, signal.SIGTERM)
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(child.proc.wait(), timeout=10)
        await self._spawn(child)
        return {"restarted": True}

    async def _tail_stream(self, child: _Child, stream: asyncio.StreamReader | None, label: str) -> None:
        if child.status is None or stream is None:
            return
        while not stream.at_eof():
            line = await stream.readline()
            if not line:
                break
            text = f"[{label}] {line.decode(errors='ignore').rstrip()}"
            child.status.state = "running"
            child.status.record(text)

    async def _spawn(self, child: _Child) -> None:
        child.proc = await asyncio.create_subprocess_exec(
            sys.executable,
            *child.args,
            start_new_session=True,
            preexec_fn=_set_pdeathsig,
            env=child.env,
            stdout=asyncio.subprocess.PIPE if child.capture_output else None,
            stderr=asyncio.subprocess.PIPE if child.capture_output else None,
        )
        for task in child.log_tasks:
            task.cancel()
        child.log_tasks.clear()
        if child.capture_output and child.status is not None:
            child.status.state = "starting"
            child.log_tasks.append(
                asyncio.create_task(self._tail_stream(child, child.proc.stdout, "stdout"))
            )
            child.log_tasks.append(
                asyncio.create_task(self._tail_stream(child, child.proc.stderr, "stderr"))
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
            if child.status is not None:
                child.status.state = "crashed"
                child.status.record(f"[supervisor] process exited rc={returncode}")
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
            logger.warning("%s exited (rc=%s); respawning in %.0fs", child.name, returncode, backoff)
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
            await self._kill_orphaned_children()
            logger.info("Embedded job runtime stopped.")

    async def _kill_orphaned_children(self) -> None:
        try:
            from sqlalchemy import select  # noqa: PLC0415

            from marquee.database import _get_session_factory  # noqa: PLC0415
            from marquee.models import JobAttempt  # noqa: PLC0415

            factory = _get_session_factory()
            async with factory() as db:
                attempts = (
                    (
                        await db.execute(
                            select(JobAttempt).where(
                                JobAttempt.status.in_(("claimed", "running")),
                                JobAttempt.child_pids.is_not(None),
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
                for attempt in attempts:
                    if not attempt.child_pids:
                        continue
                    for pid in attempt.child_pids:
                        with contextlib.suppress(ProcessLookupError):
                            os.kill(pid, signal.SIGKILL)
        except Exception:  # noqa: BLE001
            logger.exception("Failed to kill orphaned children — manual cleanup may be needed")
