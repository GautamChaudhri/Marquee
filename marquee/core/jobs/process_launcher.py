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
from marquee.core.jobs.runner_protocol import (
    CONTROL_FD_ENV,
    MAX_FRAME_BYTES,
    MAX_MANIFEST_BYTES,
    RunnerOperation,
    RunnerRuntimeOptions,
)

# The internal runner is a fixed, code-owned module; its name is never taken from a
# caller. Only the closed RunnerOperation vocabulary selects what it does.
INTERNAL_RUNNER_MODULE = "marquee.core.jobs.internal_runner"

IdentityRecorder = Callable[[ProcessIdentity], Awaitable[object]]
ExitRecorder = Callable[["ExecutionSummary"], Awaitable[object]]
PipeSink = Callable[[str, bytes, bool], Awaitable[None]]

# Closed catalog of tools a handler may launch. No executable is ever taken from a request
# payload. Poster work runs entirely inside the internal runner; pg_dump is the one external
# binary the product still shells out to (database backups).
TOOL_CATALOG = frozenset({"pg_dump"})
# Bounded default in-memory capture for tool stdout that a handler parses.
DEFAULT_TOOL_STDOUT_LIMIT = 16 * 1024 * 1024


def _resolve_tool(tool: str) -> str:
    if tool not in TOOL_CATALOG:
        raise ProcessLaunchError(f"tool {tool!r} is not in the launcher catalog")
    binary = shutil.which(tool)
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


def _prepare_runner_workspace(path: Path, *, uid: int, gid: int) -> None:
    """Transfer one confined workspace without following links or crossing a size bound."""

    if os.name != "posix":
        raise ProcessLaunchError("runner privilege separation requires a POSIX host")
    if os.geteuid() != 0:
        if uid != os.geteuid() or gid != os.getegid():
            raise ProcessLaunchError("worker lacks authority to select the runner identity")
        return
    count = 0
    for current, directories, files in os.walk(path, topdown=True, followlinks=False):
        current_path = Path(current)
        entries = [*directories, *files]
        for name in entries:
            count += 1
            if count > 50_000:
                raise ProcessLaunchError("runner workspace exceeds the ownership-transfer bound")
            entry = current_path / name
            if entry.is_symlink():
                raise ProcessLaunchError("runner workspace contains a symbolic link")
            os.chown(entry, uid, gid, follow_symlinks=False)
        os.chown(current_path, uid, gid, follow_symlinks=False)


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

    async def wait_bounded(self, timeout: float) -> ExecutionSummary | None:
        """Wait for natural exit up to ``timeout``; ``None`` if still running.

        Side-effect free while the process lives, so a caller may escalate to
        :meth:`cancel` afterwards without racing a half-finished :meth:`wait`.
        """
        if self._summary is not None:
            return self._summary
        if not await self._wait_for_exit(timeout):
            return None
        return await self.wait()

    async def cancel(
        self,
        *,
        cooperative_seconds: float = 0.25,
        term_seconds: float = 0.25,
        kill_seconds: float = 5.0,
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
                # SIGKILL cannot be ignored; a bounded wait still guards against an
                # unkillable (kernel-stuck) child so cancellation can never block
                # forever. Failure here is death-confirmation failure, not success.
                if not await self._wait_for_exit(kill_seconds):
                    raise ProcessLaunchError("process-tree death could not be confirmed")
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
        runner_uid: int | None = None,
        runner_gid: int | None = None,
        require_privilege_separation: bool = False,
    ) -> None:
        if not worker_node or len(worker_node) > 100:
            raise ProcessLaunchError("worker node identity is invalid")
        if capture_limit < 0 or capture_limit > 1024 * 1024:
            raise ProcessLaunchError("capture limit is outside the fixed bound")
        if (runner_uid is None) != (runner_gid is None):
            raise ProcessLaunchError("runner UID and GID must be configured together")
        if require_privilege_separation and runner_uid is None:
            raise ProcessLaunchError(
                "runner privilege separation is required while bootstrap secrets are mounted"
            )
        if require_privilege_separation and runner_uid == os.geteuid():
            raise ProcessLaunchError("runner identity must differ from the secret-bearing worker")
        self._worker_node = worker_node
        self._boundary = boundary
        self._working_directory = working_directory
        self._record_identity = record_identity
        self._record_exit = record_exit
        self._pipe_sink = pipe_sink
        self._capture_limit = capture_limit
        self._cgroup_root = cgroup_root
        self._runner_uid = runner_uid
        self._runner_gid = runner_gid
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

    async def launch_internal_runner(
        self,
        operation: RunnerOperation | str,
        *,
        manifest: bytes,
        runtime_options: RunnerRuntimeOptions | None = None,
    ) -> tuple[TrackedProcess, asyncio.StreamReader]:
        """Launch the fixed internal runner for one closed operation, contained.

        The runner module and its operation vocabulary are code-owned; no path,
        module name, or command fragment ever comes from a caller. The bounded,
        versioned manifest is written to the child's stdin, and structured
        control/progress/result frames return on a dedicated inherited pipe whose
        StreamReader is returned to the coordinator. stdout/stderr still tee into
        the redacted attempt log so large data never crosses the frame channel.
        """
        resolved = (
            operation if isinstance(operation, RunnerOperation) else RunnerOperation(operation)
        )
        if not isinstance(manifest, bytes | bytearray):
            raise ProcessLaunchError("internal runner manifest must be bytes")
        if not 4 <= len(manifest) <= MAX_MANIFEST_BYTES + 4:
            raise ProcessLaunchError("internal runner manifest is out of bounds")
        capabilities = containment_capabilities(cgroup_root=self._cgroup_root)
        if capabilities.tier == "unsupported":
            raise ProcessLaunchError("verified process-group containment is unavailable")
        command = (sys.executable, "-m", INTERNAL_RUNNER_MODULE, resolved.value)
        environment = _minimal_environment()
        environment["MARQUEE_INTERNAL_RUNNER"] = "1"
        if runtime_options is not None:
            environment.update(runtime_options.environment())
        cwd = self._cwd()
        identity_options: dict[str, object] = {}
        if self._runner_uid is not None and self._runner_gid is not None:
            await asyncio.to_thread(
                _prepare_runner_workspace,
                cwd,
                uid=self._runner_uid,
                gid=self._runner_gid,
            )
            identity_options = {
                "user": self._runner_uid,
                "group": self._runner_gid,
                "extra_groups": (),
                "umask": 0o077,
            }
        control_read, control_write = os.pipe()
        started_at = datetime.now(UTC)
        try:
            environment[CONTROL_FD_ENV] = str(control_write)
            process = await asyncio.create_subprocess_exec(
                *command,
                cwd=cwd,
                env=environment,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
                pass_fds=(control_write,),
                **identity_options,
            )
        except BaseException:
            os.close(control_read)
            os.close(control_write)
            raise
        os.close(control_write)
        assert process.stdin is not None
        assert process.stdout is not None
        assert process.stderr is not None
        stdout_task: asyncio.Task[StreamSummary] | None = None
        stderr_task: asyncio.Task[StreamSummary] | None = None
        cgroup: CgroupV2Handle | None = None
        pipe_file: object | None = None
        control_transport: asyncio.ReadTransport | None = None
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
            loop = asyncio.get_running_loop()
            control_reader = asyncio.StreamReader(limit=2 * MAX_FRAME_BYTES)
            pipe_file = os.fdopen(control_read, "rb", 0)
            control_transport, _ = await loop.connect_read_pipe(
                lambda: asyncio.StreamReaderProtocol(control_reader), pipe_file
            )
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
            process.stdin.write(bytes(manifest))
            await process.stdin.drain()
            process.stdin.close()
            with contextlib.suppress(BrokenPipeError, ConnectionResetError):
                await process.stdin.wait_closed()
        except BaseException:
            if control_transport is not None:
                control_transport.close()
            elif pipe_file is not None:
                pipe_file.close()  # type: ignore[attr-defined]
            else:
                os.close(control_read)
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
        return tracked, control_reader

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
