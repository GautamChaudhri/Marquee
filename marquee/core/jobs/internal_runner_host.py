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

from marquee.core.jobs.process_launcher import ProcessLauncher
from marquee.core.jobs.runner_protocol import (
    MAX_FRAME_BYTES,
    MAX_FRAMES,
    ProtocolError,
    RunnerOperation,
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


async def run_internal_operation(
    launcher: ProcessLauncher,
    *,
    operation: RunnerOperation,
    manifest: dict[str, Any],
    on_progress: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    should_stop: Callable[[], bool] | None = None,
    timeout_seconds: float | None = None,
    resolve_output: Callable[[str], Path] | None = None,
    cooperative_seconds: float = 0.25,
    term_seconds: float = 0.25,
) -> RunnerOutcome:
    """Run one contained operation and return a validated, honest outcome."""
    manifest_bytes = encode_manifest({**manifest, "v": 1, "operation": operation.value})
    tracked, reader = await launcher.launch_internal_runner(operation, manifest=manifest_bytes)
    queue: asyncio.Queue = asyncio.Queue()
    reader_task = asyncio.create_task(_drain_frames(reader, queue))
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_seconds if timeout_seconds is not None else None

    ready = False
    warnings: list[str] = []
    file_frames: list[dict[str, Any]] = []
    result_frame: dict[str, Any] | None = None
    protocol_error: ProtocolError | None = None
    cancelled = False
    timed_out = False

    try:
        while True:
            if should_stop is not None and should_stop():
                cancelled = True
                break
            if deadline is not None and loop.time() >= deadline:
                timed_out = True
                break
            try:
                kind, payload = await asyncio.wait_for(queue.get(), timeout=_POLL_SECONDS)
            except TimeoutError:
                continue
            if kind == "eof":
                break
            if kind == "error":
                protocol_error = payload
                break
            frame = payload
            frame_type = frame.get("type")
            if frame_type == "ready":
                ready = True
            elif frame_type == "progress":
                if on_progress is not None:
                    await on_progress(frame)
            elif frame_type == "warning":
                if len(warnings) < _MAX_WARNINGS:
                    message = frame.get("message")
                    warnings.append(message if isinstance(message, str) else "")
            elif frame_type == "file":
                file_frames.append(frame)
                if len(file_frames) > _MAX_PRODUCED_FILES:
                    protocol_error = ProtocolError("runner produced too many files")
                    break
            elif frame_type == "result":
                result_frame = frame
                break
            # unknown frame types are ignored but bounded by MAX_FRAMES
    finally:
        terminate = cancelled or timed_out or protocol_error is not None
        if terminate:
            summary = await tracked.cancel(
                cooperative_seconds=cooperative_seconds, term_seconds=term_seconds
            )
        else:
            summary = await tracked.wait()
        reader_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await reader_task

    return _finalize(
        ready=ready,
        warnings=tuple(warnings),
        file_frames=file_frames,
        result_frame=result_frame,
        protocol_error=protocol_error,
        cancelled=cancelled,
        timed_out=timed_out,
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
        return RunnerOutcome(OUTCOME_CANCELLED, ready=ready, warnings=warnings,
                             exit_code=exit_code, exit_signal=exit_signal)
    if timed_out:
        return RunnerOutcome(OUTCOME_TIMEOUT, ready=ready, warnings=warnings,
                             exit_code=exit_code, exit_signal=exit_signal)
    if protocol_error is not None:
        return RunnerOutcome(OUTCOME_PROTOCOL_ERROR, ready=ready, warnings=warnings,
                             error={"code": "ProtocolError", "message": str(protocol_error)},
                             exit_code=exit_code, exit_signal=exit_signal)

    # A late fence loss after a clean result must not be reported as success.
    if should_stop is not None and should_stop():
        return RunnerOutcome(OUTCOME_CANCELLED, ready=ready, warnings=warnings,
                             exit_code=exit_code, exit_signal=exit_signal)

    if result_frame is None:
        return RunnerOutcome(OUTCOME_FAILED, ready=ready, warnings=warnings,
                             error={"code": "NoResult", "message": "runner exited without a result"},
                             exit_code=exit_code, exit_signal=exit_signal)

    reported = result_frame.get("outcome")
    result_summary = result_frame.get("summary")
    result_summary = result_summary if isinstance(result_summary, dict) else {}
    error = result_frame.get("error") if isinstance(result_frame.get("error"), dict) else None

    try:
        files = _parse_files(file_frames)
    except ProtocolError as exc:
        return RunnerOutcome(OUTCOME_PROTOCOL_ERROR, ready=ready, warnings=warnings,
                             error={"code": "ProtocolError", "message": str(exc)},
                             exit_code=exit_code, exit_signal=exit_signal)

    if reported != OUTCOME_SUCCEEDED or exit_code != 0:
        return RunnerOutcome(OUTCOME_FAILED, summary=result_summary, files=files, warnings=warnings,
                             error=error or {"code": "RunnerFailed", "message": str(reported)},
                             exit_code=exit_code, exit_signal=exit_signal, ready=ready)

    if resolve_output is not None:
        try:
            validate_runner_files(files, resolve_output)
        except ProtocolError as exc:
            return RunnerOutcome(OUTCOME_PROTOCOL_ERROR, summary=result_summary, files=files,
                                 warnings=warnings,
                                 error={"code": "ProtocolError", "message": str(exc)},
                                 exit_code=exit_code, exit_signal=exit_signal, ready=ready)

    return RunnerOutcome(OUTCOME_SUCCEEDED, summary=result_summary, files=files, warnings=warnings,
                         exit_code=exit_code, exit_signal=exit_signal, ready=ready)
