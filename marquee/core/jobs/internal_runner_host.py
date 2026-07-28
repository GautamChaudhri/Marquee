"""Host-side coordinator for the JMC6H internal runner.

Launches the fixed runner through the tracked process launcher, drives the bounded
control protocol, stays responsive to cancellation/timeout without corrupting a
partially read frame, proves process-tree death on interruption, and validates
every produced file (existence, size, checksum) before a caller may register an
artifact or activate a publication. It never trusts a runner result frame over the
coordinator's own cancellation intent, timeout, or a caller-supplied fence check.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import struct
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from marquee.core.jobs.process_launcher import ProcessLauncher, ProcessLaunchError
from marquee.core.jobs.runner_protocol import (
    MAX_FRAME_BYTES,
    MAX_FRAMES,
    ProtocolError,
    RunnerOperation,
    RunnerRuntimeOptions,
    decode_payload,
    encode_manifest,
)

# Coordinator outcome vocabulary. These are transport-level; a definition/handler
# maps them onto its own terminal contract (JMC6G TerminalDecision).
OUTCOME_SUCCEEDED = "succeeded"
OUTCOME_FAILED = "failed"
OUTCOME_CANCELLED = "cancelled"
OUTCOME_TIMEOUT = "timeout"
OUTCOME_PROTOCOL_ERROR = "protocol_error"

_MAX_PRODUCED_FILES = 4096
_MAX_WARNINGS = 1024
_POLL_SECONDS = 0.1


@dataclass(frozen=True, slots=True)
class RunnerFile:
    key: str
    checksum: str
    size: int


@dataclass(frozen=True, slots=True)
class RunnerOutcome:
    outcome: str
    summary: dict[str, Any] = field(default_factory=dict)
    files: tuple[RunnerFile, ...] = ()
    warnings: tuple[str, ...] = ()
    error: dict[str, Any] | None = None
    exit_code: int | None = None
    exit_signal: int | None = None
    ready: bool = False

    @property
    def succeeded(self) -> bool:
        return self.outcome == OUTCOME_SUCCEEDED


async def _read_frame(reader: asyncio.StreamReader) -> dict[str, Any] | None:
    """Read one length-prefixed frame; ``None`` only on a clean EOF at a boundary."""
    try:
        header = await reader.readexactly(4)
    except asyncio.IncompleteReadError as exc:
        if not exc.partial:
            return None
        raise ProtocolError("control stream ended mid-header") from exc
    (length,) = struct.unpack(">I", header)
    if length > MAX_FRAME_BYTES:
        raise ProtocolError("control frame exceeds the fixed bound")
    try:
        payload = await reader.readexactly(length)
    except asyncio.IncompleteReadError as exc:
        raise ProtocolError("control frame was truncated") from exc
    return decode_payload(payload)


async def _drain_frames(reader: asyncio.StreamReader, queue: asyncio.Queue) -> None:
    """Read frames to EOF/error into a queue so the main loop never cancels a read."""
    count = 0
    try:
        while True:
            frame = await _read_frame(reader)
            if frame is None:
                await queue.put(("eof", None))
                return
            count += 1
            if count > MAX_FRAMES:
                await queue.put(("error", ProtocolError("control frame count exceeded")))
                return
            await queue.put(("frame", frame))
    except ProtocolError as exc:
        await queue.put(("error", exc))
    except Exception as exc:  # noqa: BLE001 - surface any reader failure as protocol error
        await queue.put(("error", ProtocolError(str(exc))))


def validate_runner_files(
    files: tuple[RunnerFile, ...],
    resolve: Callable[[str], Path],
) -> None:
    """Verify each produced file exists, is confined, and matches size + checksum.

    ``resolve`` maps a confined key to a filesystem path; it must itself reject any
    key that escapes the workspace. Raises :class:`ProtocolError` on any mismatch.
    """
    if len(files) > _MAX_PRODUCED_FILES:
        raise ProtocolError("runner produced too many files")
    seen: set[str] = set()
    for descriptor in files:
        if descriptor.key in seen:
            raise ProtocolError("runner produced a duplicate file key")
        seen.add(descriptor.key)
        path = resolve(descriptor.key)
        if not path.is_file():
            raise ProtocolError(f"produced file {descriptor.key!r} is missing")
        digest = hashlib.sha256()
        size = 0
        with path.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                size += len(chunk)
                digest.update(chunk)
        if size != descriptor.size or digest.hexdigest() != descriptor.checksum:
            raise ProtocolError(f"produced file {descriptor.key!r} failed validation")


def _parse_files(frames: list[dict[str, Any]]) -> tuple[RunnerFile, ...]:
    files: list[RunnerFile] = []
    for frame in frames:
        key = frame.get("key")
        checksum = frame.get("checksum")
        size = frame.get("size")
        if not isinstance(key, str) or not isinstance(checksum, str) or not isinstance(size, int):
            raise ProtocolError("produced-file frame is malformed")
        if size < 0 or len(checksum) != 64:
            raise ProtocolError("produced-file frame is malformed")
        files.append(RunnerFile(key=key, checksum=checksum, size=size))
    return tuple(files)


@dataclass(slots=True)
class _ProtocolState:
    """Mutable observations from one protocol loop; classification, not cleanup."""

    ready: bool = False
    warnings: list[str] = field(default_factory=list)
    file_frames: list[dict[str, Any]] = field(default_factory=list)
    result_frame: dict[str, Any] | None = None
    protocol_error: ProtocolError | None = None
    cancelled: bool = False
    timed_out: bool = False

    @property
    def terminate(self) -> bool:
        return self.cancelled or self.timed_out or self.protocol_error is not None


async def _protocol_loop(
    state: _ProtocolState,
    queue: asyncio.Queue,
    *,
    on_progress: Callable[[dict[str, Any]], Awaitable[None]] | None,
    should_stop: Callable[[], bool] | None,
    deadline: float | None,
) -> None:
    loop = asyncio.get_running_loop()
    while True:
        if should_stop is not None and should_stop():
            state.cancelled = True
            return
        if deadline is not None and loop.time() >= deadline:
            state.timed_out = True
            return
        try:
            kind, payload = await asyncio.wait_for(queue.get(), timeout=_POLL_SECONDS)
        except TimeoutError:
            continue
        if kind == "eof":
            return
        if kind == "error":
            state.protocol_error = payload
            return
        frame = payload
        frame_type = frame.get("type")
        if frame_type == "ready":
            state.ready = True
        elif frame_type == "progress":
            if on_progress is not None:
                await on_progress(frame)
        elif frame_type == "warning":
            if len(state.warnings) < _MAX_WARNINGS:
                message = frame.get("message")
                state.warnings.append(message if isinstance(message, str) else "")
        elif frame_type == "file":
            state.file_frames.append(frame)
            if len(state.file_frames) > _MAX_PRODUCED_FILES:
                state.protocol_error = ProtocolError("runner produced too many files")
                return
        elif frame_type == "result":
            state.result_frame = frame
            return
        # unknown frame types are ignored but bounded by MAX_FRAMES


async def _cleanup_tracked(
    tracked: Any,
    reader_task: asyncio.Task,
    *,
    terminate: bool,
    cooperative_seconds: float,
    term_seconds: float,
    exit_grace_seconds: float,
) -> Any:
    """The single cleanup path for every exit after launch.

    Bounded at every stage: a normal exit gets a bounded grace wait, everything
    else (and a child that outlives its result/EOF) goes through cooperative/TERM/
    KILL escalation with owned-tree death confirmation. Raises
    :class:`ProcessLaunchError` when death cannot be confirmed.
    """
    try:
        summary = None
        if not terminate:
            summary = await tracked.wait_bounded(exit_grace_seconds)
        if summary is None:
            summary = await tracked.cancel(
                cooperative_seconds=cooperative_seconds, term_seconds=term_seconds
            )
        return summary
    finally:
        reader_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await reader_task


async def _await_cleanup(cleanup: asyncio.Task) -> tuple[Any, BaseException | None, int]:
    """Await cleanup to completion inside a cancellation-resistant section.

    Outer cancellations delivered while cleanup runs are absorbed (counted and
    re-raised by the caller after death is confirmed); the cleanup task itself is
    never abandoned mid-escalation.
    """
    absorbed = 0
    while True:
        try:
            return await asyncio.shield(cleanup), None, absorbed
        except asyncio.CancelledError:
            if cleanup.cancelled():
                return (
                    None,
                    ProcessLaunchError("runner cleanup was cancelled before death confirmation"),
                    absorbed,
                )
            absorbed += 1
        except BaseException as exc:  # noqa: BLE001 - cleanup failure is reported, never lost
            return None, exc, absorbed


async def run_internal_operation(
    launcher: ProcessLauncher,
    *,
    operation: RunnerOperation,
    manifest: dict[str, Any],
    on_progress: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    should_stop: Callable[[], bool] | None = None,
    timeout_seconds: float | None = None,
    runtime_options: RunnerRuntimeOptions | None = None,
    resolve_output: Callable[[str], Path] | None = None,
    cooperative_seconds: float = 0.25,
    term_seconds: float = 0.25,
    exit_grace_seconds: float = 5.0,
) -> RunnerOutcome:
    """Run one contained operation and return a validated, honest outcome.

    Timeout authority: the definition-owned delivery ``asyncio.timeout`` around the
    handler is the sole execution-timeout policy. ``timeout_seconds`` is a
    subordinate, helper-local bounded safety deadline (used by focused tests and
    optional lower-level bounds); production handlers must not pass a second,
    contradictory deadline.

    Cleanup contract (JMC6I §5): once the runner is launched, every exit — a
    validated result, clean EOF, protocol violation, ``should_stop`` cancellation,
    the helper deadline, outer ``Task.cancel()``/delivery timeout, a progress
    callback or decoder failure, or worker shutdown — either proves normal
    termination within a bounded grace or performs bounded cooperative/TERM/KILL
    cancellation, and always confirms owned-tree death before returning or
    re-raising. The original outer cancellation/exception classification is
    preserved for the caller; death-confirmation failure is raised as a hard
    operational failure and never reported as successful cancellation.
    """
    manifest_bytes = encode_manifest({**manifest, "v": 1, "operation": operation.value})
    tracked, reader = await launcher.launch_internal_runner(
        operation, manifest=manifest_bytes, runtime_options=runtime_options
    )
    queue: asyncio.Queue = asyncio.Queue()
    reader_task = asyncio.create_task(_drain_frames(reader, queue))
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_seconds if timeout_seconds is not None else None

    state = _ProtocolState()
    interrupt: BaseException | None = None
    try:
        await _protocol_loop(
            state,
            queue,
            on_progress=on_progress,
            should_stop=should_stop,
            deadline=deadline,
        )
    except BaseException as exc:  # noqa: BLE001 - outer cancel/timeout or callback failure
        interrupt = exc

    cleanup = asyncio.create_task(
        _cleanup_tracked(
            tracked,
            reader_task,
            terminate=interrupt is not None or state.terminate,
            cooperative_seconds=cooperative_seconds,
            term_seconds=term_seconds,
            exit_grace_seconds=exit_grace_seconds,
        )
    )
    summary, cleanup_error, absorbed_cancels = await _await_cleanup(cleanup)

    if cleanup_error is not None:
        # Death is unconfirmed: report a hard operational failure. It must win over
        # the original classification — acknowledging a clean cancellation or
        # timeout here would release gates over a possibly-live process tree.
        raise cleanup_error from interrupt
    if absorbed_cancels and not isinstance(interrupt, asyncio.CancelledError):
        # A fresh outer cancellation arrived during cleanup; honor it now that the
        # owned tree is confirmed dead (any earlier exception rides along as context).
        raise asyncio.CancelledError from interrupt
    if interrupt is not None:
        raise interrupt

    return _finalize(
        ready=state.ready,
        warnings=tuple(state.warnings),
        file_frames=state.file_frames,
        result_frame=state.result_frame,
        protocol_error=state.protocol_error,
        cancelled=state.cancelled,
        timed_out=state.timed_out,
        summary=summary,
        should_stop=should_stop,
        resolve_output=resolve_output,
    )


def _finalize(
    *,
    ready: bool,
    warnings: tuple[str, ...],
    file_frames: list[dict[str, Any]],
    result_frame: dict[str, Any] | None,
    protocol_error: ProtocolError | None,
    cancelled: bool,
    timed_out: bool,
    summary: Any,
    should_stop: Callable[[], bool] | None,
    resolve_output: Callable[[str], Path] | None,
) -> RunnerOutcome:
    exit_code = summary.exit_code
    exit_signal = summary.exit_signal

    if cancelled:
        return RunnerOutcome(
            OUTCOME_CANCELLED,
            ready=ready,
            warnings=warnings,
            exit_code=exit_code,
            exit_signal=exit_signal,
        )
    if timed_out:
        return RunnerOutcome(
            OUTCOME_TIMEOUT,
            ready=ready,
            warnings=warnings,
            exit_code=exit_code,
            exit_signal=exit_signal,
        )
    if protocol_error is not None:
        return RunnerOutcome(
            OUTCOME_PROTOCOL_ERROR,
            ready=ready,
            warnings=warnings,
            error={"code": "ProtocolError", "message": str(protocol_error)},
            exit_code=exit_code,
            exit_signal=exit_signal,
        )

    # A late fence loss after a clean result must not be reported as success.
    if should_stop is not None and should_stop():
        return RunnerOutcome(
            OUTCOME_CANCELLED,
            ready=ready,
            warnings=warnings,
            exit_code=exit_code,
            exit_signal=exit_signal,
        )

    if result_frame is None:
        return RunnerOutcome(
            OUTCOME_FAILED,
            ready=ready,
            warnings=warnings,
            error={"code": "NoResult", "message": "runner exited without a result"},
            exit_code=exit_code,
            exit_signal=exit_signal,
        )

    reported = result_frame.get("outcome")
    result_summary = result_frame.get("summary")
    result_summary = result_summary if isinstance(result_summary, dict) else {}
    error = result_frame.get("error") if isinstance(result_frame.get("error"), dict) else None

    try:
        files = _parse_files(file_frames)
    except ProtocolError as exc:
        return RunnerOutcome(
            OUTCOME_PROTOCOL_ERROR,
            ready=ready,
            warnings=warnings,
            error={"code": "ProtocolError", "message": str(exc)},
            exit_code=exit_code,
            exit_signal=exit_signal,
        )

    if reported != OUTCOME_SUCCEEDED or exit_code != 0:
        return RunnerOutcome(
            OUTCOME_FAILED,
            summary=result_summary,
            files=files,
            warnings=warnings,
            error=error or {"code": "RunnerFailed", "message": str(reported)},
            exit_code=exit_code,
            exit_signal=exit_signal,
            ready=ready,
        )

    if resolve_output is not None:
        try:
            validate_runner_files(files, resolve_output)
        except ProtocolError as exc:
            return RunnerOutcome(
                OUTCOME_PROTOCOL_ERROR,
                summary=result_summary,
                files=files,
                warnings=warnings,
                error={"code": "ProtocolError", "message": str(exc)},
                exit_code=exit_code,
                exit_signal=exit_signal,
                ready=ready,
            )

    return RunnerOutcome(
        OUTCOME_SUCCEEDED,
        summary=result_summary,
        files=files,
        warnings=warnings,
        exit_code=exit_code,
        exit_signal=exit_signal,
        ready=ready,
    )
