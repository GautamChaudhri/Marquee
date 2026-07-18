"""Closed-catalog tracked process launcher with identity-safe cancellation."""

from __future__ import annotations

import asyncio
import contextlib
import os
import shutil
import signal
import sys
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

from marquee.core.filesystem import ClassifiedPath, FilesystemBoundary, FilesystemBoundaryError
from marquee.core.jobs.process_canary import CanaryBehavior
from marquee.core.jobs.process_identity import (
    CgroupV2Handle,
    IdentityStatus,
    ProcessIdentity,
    ProcessIdentityError,
    capture_process_identity,
    containment_capabilities,
    create_attempt_cgroup,
    process_group_exists,
    verify_process_identity,
)

IdentityRecorder = Callable[[ProcessIdentity], Awaitable[object]]
ExitRecorder = Callable[["ExecutionSummary"], Awaitable[object]]
PipeSink = Callable[[str, bytes, bool], Awaitable[None]]

# Closed catalog of read-only/analysis media tools migrated handlers may launch. Executable
# paths are resolved through marquee.media.binaries (which honours the .env LETTERBOX_* paths,
# e.g. the project-local bin/dovi_tool). No executable is ever taken from a request payload.
TOOL_CATALOG = frozenset(
    {"ffprobe", "ffmpeg", "mkvmerge", "mkvpropedit", "convert", "dovi_tool", "pg_dump"}
)
# Bounded default in-memory capture for tool stdout that a handler parses (e.g. ffprobe JSON).
DEFAULT_TOOL_STDOUT_LIMIT = 16 * 1024 * 1024


def _resolve_tool(tool: str) -> str:
    if tool not in TOOL_CATALOG:
        raise ProcessLaunchError(f"tool {tool!r} is not in the launcher catalog")
    if tool == "pg_dump":
        binary = shutil.which("pg_dump")
        if binary is None:
            raise ProcessLaunchError("tool 'pg_dump' is not available")
        return binary
    from marquee.media.binaries import resolve  # noqa: PLC0415 - avoid settings import at load

    binary = resolve(tool)
    if binary is None:
        raise ProcessLaunchError(f"tool {tool!r} is not available")
    return binary


class ProcessLaunchError(RuntimeError):
    pass


class UnsafeProcessIdentityError(ProcessLaunchError):
    pass


class TerminationStage(StrEnum):
    EXITED = "exited"
    COOPERATIVE = "cooperative"
    TERM = "term"
    KILL = "kill"


@dataclass(frozen=True, slots=True)
class StreamSummary:
    captured: bytes
    total_bytes: int
    truncated: bool


@dataclass(frozen=True, slots=True)
class ExecutionSummary:
    exit_code: int | None
    exit_signal: int | None
    stdout: StreamSummary
    stderr: StreamSummary
    started_at: datetime
    finished_at: datetime
    termination_stage: TerminationStage


async def _drain(
    reader: asyncio.StreamReader,
    *,
    capture_limit: int,
    source: str,
    sink: PipeSink | None = None,
) -> StreamSummary:
    captured = bytearray()
    total = 0
    sink_active = sink is not None
    try:
        while chunk := await reader.read(64 * 1024):
            total += len(chunk)
            remaining = capture_limit - len(captured)
            if remaining > 0:
                captured.extend(chunk[:remaining])
            if sink_active:
                try:
                    assert sink is not None
                    await sink(source, chunk, False)
                except Exception:
                    sink_active = False
    finally:
        if sink_active:
            with contextlib.suppress(Exception):
                assert sink is not None
                await sink(source, b"", True)
    return StreamSummary(
        captured=bytes(captured),
        total_bytes=total,
        truncated=total > len(captured),
    )


def _minimal_environment() -> dict[str, str]:
    environment: dict[str, str] = {}
    for key in ("PATH", "LANG", "LC_ALL", "TZ"):
        value = os.environ.get(key)
        if value and "\0" not in value:
            environment[key] = value
    environment["PYTHONUNBUFFERED"] = "1"
    return environment


class TrackedProcess:
    def __init__(
        self,
        *,
        process: asyncio.subprocess.Process,
        identity: ProcessIdentity,
        stdout_task: asyncio.Task[StreamSummary],
        stderr_task: asyncio.Task[StreamSummary],
        started_at: datetime,
        record_exit: ExitRecorder | None,
        on_finished: Callable[[TrackedProcess], None] | None,
        cgroup: CgroupV2Handle | None,
    ) -> None:
        self._process = process
        self.identity = identity
        self._stdout_task = stdout_task
        self._stderr_task = stderr_task
        self._started_at = started_at
        self._record_exit = record_exit
        self._on_finished = on_finished
        self._cgroup = cgroup
        self._summary: ExecutionSummary | None = None
        self._stage = TerminationStage.EXITED

    @property
    def pid(self) -> int:
        return self.identity.pid

    def _verified_status(self) -> IdentityStatus:
        return verify_process_identity(self.identity)

    def _signal(self, sig: signal.Signals) -> bool:
        status = self._verified_status()
        if sig == signal.SIGKILL and self._cgroup is not None:
            if status not in {IdentityStatus.MATCH, IdentityStatus.DEAD}:
                raise UnsafeProcessIdentityError(
                    f"refusing cgroup kill because process identity is {status.value}"
                )
            self._cgroup.kill()
            return True
        if status == IdentityStatus.DEAD:
            return False
        if status != IdentityStatus.MATCH:
            raise UnsafeProcessIdentityError(
                f"refusing signal because process identity is {status.value}"
            )
        try:
            os.killpg(self.identity.process_group_id, sig)
        except ProcessLookupError:
            return False
        return True

    async def _wait_for_exit(self, timeout: float) -> bool:
        try:
            await asyncio.wait_for(asyncio.shield(self._process.wait()), timeout=timeout)
        except TimeoutError:
            return False
        return True

    async def _confirm_tree_dead(self, timeout: float = 2.0) -> None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while (
            self._cgroup.populated()
            if self._cgroup is not None
            else process_group_exists(self.identity.process_group_id)
        ):
            if loop.time() >= deadline:
                raise ProcessLaunchError("process-tree death could not be confirmed")
            await asyncio.sleep(0.02)

    async def cancel(
        self,
        *,
        cooperative_seconds: float = 0.25,
        term_seconds: float = 0.25,
    ) -> ExecutionSummary:
        if self._summary is not None:
            return self._summary
        if self._process.returncode is not None:
            return await self.wait()
        if self._signal(signal.SIGINT):
            self._stage = TerminationStage.COOPERATIVE
        if not await self._wait_for_exit(cooperative_seconds):
            if self._signal(signal.SIGTERM):
                self._stage = TerminationStage.TERM
            if not await self._wait_for_exit(term_seconds):
                if self._signal(signal.SIGKILL):
                    self._stage = TerminationStage.KILL
                await self._process.wait()
        await self._confirm_tree_dead()
        return await self.wait()

    async def wait(self) -> ExecutionSummary:
        if self._summary is not None:
            return self._summary
        returncode = await self._process.wait()
        stdout, stderr = await asyncio.gather(self._stdout_task, self._stderr_task)
        await self._confirm_tree_dead()
        exit_signal = -returncode if returncode < 0 else None
        summary = ExecutionSummary(
            exit_code=returncode if returncode >= 0 else None,
            exit_signal=exit_signal,
            stdout=stdout,
            stderr=stderr,
            started_at=self._started_at,
            finished_at=datetime.now(UTC),
            termination_stage=self._stage,
        )
        if self._cgroup is not None:
            self._cgroup.cleanup()
        if self._record_exit is not None:
            await self._record_exit(summary)
        self._summary = summary
        if self._on_finished is not None:
            self._on_finished(self)
        return summary


class ProcessLauncher:
    """Launch only compiled-in canaries; no arbitrary exec surface exists."""

    def __init__(
        self,
        *,
        worker_node: str,
        boundary: FilesystemBoundary,
        working_directory: ClassifiedPath,
        record_identity: IdentityRecorder | None = None,
        record_exit: ExitRecorder | None = None,
        pipe_sink: PipeSink | None = None,
        capture_limit: int = 64 * 1024,
        cgroup_root: Path = Path("/sys/fs/cgroup"),
    ) -> None:
        if not worker_node or len(worker_node) > 100:
            raise ProcessLaunchError("worker node identity is invalid")
        if capture_limit < 0 or capture_limit > 1024 * 1024:
            raise ProcessLaunchError("capture limit is outside the fixed bound")
        self._worker_node = worker_node
        self._boundary = boundary
        self._working_directory = working_directory
        self._record_identity = record_identity
        self._record_exit = record_exit
        self._pipe_sink = pipe_sink
        self._capture_limit = capture_limit
        self._cgroup_root = cgroup_root
        self._active: set[TrackedProcess] = set()

    def _cwd(self) -> Path:
        candidate = self._working_directory.root.resolved().joinpath(
            *self._working_directory.key.parts
        )
        try:
            classified = self._boundary.classify(candidate, require_exists=True)
        except FilesystemBoundaryError as exc:
            raise ProcessLaunchError("working directory is no longer confined") from exc
        if classified != self._working_directory or not candidate.is_dir():
            raise ProcessLaunchError("working directory is no longer confined")
        return candidate

    async def launch_canary(self, behavior: CanaryBehavior) -> TrackedProcess:
        if not isinstance(behavior, CanaryBehavior):
            raise ProcessLaunchError("unknown fixed canary behavior")
        capabilities = containment_capabilities(cgroup_root=self._cgroup_root)
        if capabilities.tier == "unsupported":
            raise ProcessLaunchError("verified process-group containment is unavailable")
        command = (sys.executable, "-m", "marquee.core.jobs.process_canary", behavior.value)
        if any("\0" in argument or len(argument) > 4096 for argument in command):
            raise ProcessLaunchError("compiled command is invalid")
        started_at = datetime.now(UTC)
        process = await asyncio.create_subprocess_exec(
            *command,
            cwd=self._cwd(),
            env=_minimal_environment(),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
        assert process.stdin is not None
        assert process.stdout is not None
        assert process.stderr is not None
        stdout_task: asyncio.Task[StreamSummary] | None = None
        stderr_task: asyncio.Task[StreamSummary] | None = None
        cgroup: CgroupV2Handle | None = None
        try:
            identity = capture_process_identity(process.pid, worker_node=self._worker_node)
            if identity.process_group_id != process.pid:
                raise ProcessIdentityError("child did not become a process-group leader")
            cgroup = create_attempt_cgroup(
                process.pid,
                process_start_ticks=identity.process_start_ticks,
                cgroup_root=self._cgroup_root,
            )
            if cgroup is not None:
                identity = replace(identity, cgroup_path=str(cgroup.path))
            if self._record_identity is not None:
                recorded = await self._record_identity(identity)
                if recorded is not True and recorded != "applied":
                    raise ProcessLaunchError("durable process identity ownership was rejected")
            ready = await asyncio.wait_for(process.stdout.readline(), timeout=5.0)
            if ready != b"MARQUEE_CANARY_READY\n":
                raise ProcessLaunchError("fixed canary start barrier failed")
            if self._pipe_sink is not None:
                with contextlib.suppress(Exception):
                    await self._pipe_sink("stdout", ready, False)
            stdout_task = asyncio.create_task(
                _drain(
                    process.stdout,
                    capture_limit=self._capture_limit,
                    source="stdout",
                    sink=self._pipe_sink,
                )
            )
            stderr_task = asyncio.create_task(
                _drain(
                    process.stderr,
                    capture_limit=self._capture_limit,
                    source="stderr",
                    sink=self._pipe_sink,
                )
            )
            process.stdin.write(b"1")
            await process.stdin.drain()
            process.stdin.close()
            with contextlib.suppress(BrokenPipeError, ConnectionResetError):
                await process.stdin.wait_closed()
        except BaseException:
            if cgroup is not None:
                with contextlib.suppress(ProcessIdentityError):
                    cgroup.kill()
            else:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGKILL)
            await process.wait()
            tasks = tuple(task for task in (stdout_task, stderr_task) if task is not None)
            if tasks:
                await asyncio.gather(*tasks)
            if cgroup is not None:
                with contextlib.suppress(ProcessIdentityError):
                    cgroup.cleanup()
            raise
        assert stdout_task is not None and stderr_task is not None
        tracked = TrackedProcess(
            process=process,
            identity=identity,
            stdout_task=stdout_task,
            stderr_task=stderr_task,
            started_at=started_at,
            record_exit=self._record_exit,
            on_finished=self._active.discard,
            cgroup=cgroup,
        )
        self._active.add(tracked)
        return tracked

    async def launch(
        self,
        tool: str,
        args: Sequence[str],
        *,
        stdout_limit: int = DEFAULT_TOOL_STDOUT_LIMIT,
        stdout_sink: PipeSink | None = None,
        environment: Mapping[str, str] | None = None,
    ) -> TrackedProcess:
        """Launch one allowlisted read-only media tool as a tracked, contained process.

        stdout is captured in a bounded in-memory buffer for the handler to parse (never routed
        to the attempt-log pipe); stderr is teed to the log sink. stdin is closed.
        """
        binary = _resolve_tool(tool)
        arguments = tuple(str(argument) for argument in args)
        command = (binary, *arguments)
        if len(command) > 4096:
            raise ProcessLaunchError("tool command has too many arguments")
        if any("\0" in argument or len(argument) > 4096 for argument in command):
            raise ProcessLaunchError("tool argument is invalid")
        if not 0 < stdout_limit <= 64 * 1024 * 1024:
            raise ProcessLaunchError("tool stdout capture limit is out of bounds")
        environment_overrides = dict(environment or {})
        if set(environment_overrides) - {"PGPASSFILE"}:
            raise ProcessLaunchError("tool environment contains a non-allowlisted key")
        if any("\0" in value or len(value) > 4096 for value in environment_overrides.values()):
            raise ProcessLaunchError("tool environment value is invalid")
        process_environment = _minimal_environment()
        process_environment.update(environment_overrides)
        capabilities = containment_capabilities(cgroup_root=self._cgroup_root)
        if capabilities.tier == "unsupported":
            raise ProcessLaunchError("verified process-group containment is unavailable")
        started_at = datetime.now(UTC)
        process = await asyncio.create_subprocess_exec(
            *command,
            cwd=self._cwd(),
            env=process_environment,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
        assert process.stdout is not None
        assert process.stderr is not None
        stdout_task: asyncio.Task[StreamSummary] | None = None
        stderr_task: asyncio.Task[StreamSummary] | None = None
        cgroup: CgroupV2Handle | None = None
        try:
            identity = capture_process_identity(process.pid, worker_node=self._worker_node)
            if identity.process_group_id != process.pid:
                raise ProcessIdentityError("child did not become a process-group leader")
            cgroup = create_attempt_cgroup(
                process.pid,
                process_start_ticks=identity.process_start_ticks,
                cgroup_root=self._cgroup_root,
            )
            if cgroup is not None:
                identity = replace(identity, cgroup_path=str(cgroup.path))
            if self._record_identity is not None:
                recorded = await self._record_identity(identity)
                if recorded is not True and recorded != "applied":
                    raise ProcessLaunchError("durable process identity ownership was rejected")
            stdout_task = asyncio.create_task(
                _drain(
                    process.stdout,
                    capture_limit=stdout_limit,
                    source="stdout",
                    sink=stdout_sink,
                )
            )
            stderr_task = asyncio.create_task(
                _drain(
                    process.stderr,
                    capture_limit=self._capture_limit,
                    source="stderr",
                    sink=self._pipe_sink,
                )
            )
        except BaseException:
            if cgroup is not None:
                with contextlib.suppress(ProcessIdentityError):
                    cgroup.kill()
            else:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGKILL)
            await process.wait()
            tasks = tuple(task for task in (stdout_task, stderr_task) if task is not None)
            if tasks:
                await asyncio.gather(*tasks)
            if cgroup is not None:
                with contextlib.suppress(ProcessIdentityError):
                    cgroup.cleanup()
            raise
        assert stdout_task is not None and stderr_task is not None
        tracked = TrackedProcess(
            process=process,
            identity=identity,
            stdout_task=stdout_task,
            stderr_task=stderr_task,
            started_at=started_at,
            record_exit=self._record_exit,
            on_finished=self._active.discard,
            cgroup=cgroup,
        )
        self._active.add(tracked)
        return tracked

    async def shutdown(
        self,
        *,
        cooperative_seconds: float,
        term_seconds: float,
    ) -> None:
        """Prove every launched process tree dead before the owning gates can release."""
        processes = tuple(self._active)
        if processes:
            await asyncio.gather(
                *(
                    process.cancel(
                        cooperative_seconds=cooperative_seconds,
                        term_seconds=term_seconds,
                    )
                    for process in processes
                )
            )
