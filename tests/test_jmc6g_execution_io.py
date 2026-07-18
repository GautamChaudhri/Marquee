from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from marquee.core.filesystem import FilesystemBoundary, RootSpec
from marquee.core.jobs.execution_io import ExecutionIO, ExecutionIOCancelledError
from marquee.core.jobs.process_launcher import ExecutionSummary, StreamSummary, TerminationStage
from marquee.core.jobs.remux_coordinator import run_mkvmerge_plan


async def test_chunked_copy_yields_and_reports_bytes(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "source.bin"
    destination = tmp_path / "destination.bin"
    source.write_bytes(b"abcdefgh" * (256 * 1024))
    observations: list[tuple[int, int]] = []
    ticks = 0
    running = True
    original_to_thread = asyncio.to_thread

    async def slow_to_thread(function, /, *args, **kwargs):
        await asyncio.sleep(0.001)
        return await original_to_thread(function, *args, **kwargs)

    async def heartbeat() -> None:
        nonlocal ticks
        while running:
            ticks += 1
            await asyncio.sleep(0)

    monkeypatch.setattr(asyncio, "to_thread", slow_to_thread)
    io = ExecutionIO(
        cancelled=lambda: False,
        owns_fence=lambda: asyncio.sleep(0, result=True),
        progress=lambda completed, total: asyncio.sleep(
            0, result=observations.append((completed, total))
        ),
        chunk_bytes=64 * 1024,
    )
    pulse = asyncio.create_task(heartbeat())
    try:
        result = await io.copy(source, destination)
    finally:
        running = False
        await pulse

    assert destination.read_bytes() == source.read_bytes()
    assert result.size == source.stat().st_size
    assert observations[-1] == (result.size, result.size)
    assert len(observations) > 1
    assert ticks > len(observations)


@pytest.mark.parametrize("lost_fence", [False, True])
async def test_chunked_copy_removes_partial_output_on_cancellation_or_stale_fence(
    tmp_path: Path, lost_fence: bool
) -> None:
    source = tmp_path / "source.bin"
    destination = tmp_path / "destination.bin"
    source.write_bytes(b"x" * (512 * 1024))
    checkpoints = 0

    async def owns_fence() -> bool:
        nonlocal checkpoints
        checkpoints += 1
        return not lost_fence or checkpoints < 3

    io = ExecutionIO(
        cancelled=lambda: not lost_fence and checkpoints >= 2,
        owns_fence=owns_fence,
        chunk_bytes=64 * 1024,
    )

    with pytest.raises(ExecutionIOCancelledError):
        await io.copy(source, destination)
    assert not destination.exists()


async def test_chunked_checksum_rejects_cancellation(tmp_path: Path) -> None:
    source = tmp_path / "source.bin"
    source.write_bytes(b"x" * (256 * 1024))
    cancelled = False

    async def progress(_completed: int, _total: int) -> None:
        nonlocal cancelled
        cancelled = True

    io = ExecutionIO(
        cancelled=lambda: cancelled,
        owns_fence=lambda: asyncio.sleep(0, result=True),
        progress=progress,
        chunk_bytes=64 * 1024,
    )
    with pytest.raises(ExecutionIOCancelledError):
        await io.checksum(source)


@pytest.mark.parametrize(
    ("tool", "progress_chunk"),
    [
        ("mkvmerge", b"#GUI#progress 25%\n"),
        ("ffmpeg", b"out_time_us=1000000\nprogress=continue\n"),
    ],
)
async def test_native_progress_is_emitted_before_tracked_process_exit(
    tmp_path: Path, tool: str, progress_chunk: bytes
) -> None:
    boundary = FilesystemBoundary(
        {"test": RootSpec(name="test", path=tmp_path, purpose="test", access="read_write")}
    )
    candidate_path = tmp_path / "candidate.mkv"
    candidate_path.write_bytes(b"candidate")
    candidate = boundary.classify(candidate_path, require_file=True)
    observed_while_running: list[dict[str, object]] = []

    class Process:
        finished = False

        def __init__(self, sink) -> None:
            self.sink = sink

        async def wait(self) -> ExecutionSummary:
            await self.sink("stdout", progress_chunk, False)
            self.finished = True
            now = datetime.now(UTC)
            return ExecutionSummary(
                exit_code=0,
                exit_signal=None,
                stdout=StreamSummary(captured=progress_chunk, total_bytes=len(progress_chunk), truncated=False),
                stderr=StreamSummary(captured=b"", total_bytes=0, truncated=False),
                started_at=now,
                finished_at=now,
                termination_stage=TerminationStage.EXITED,
            )

    class Launcher:
        process: Process | None = None

        async def launch(self, _tool, _args, *, stdout_sink):
            self.process = Process(stdout_sink)
            return self.process

    launcher = Launcher()

    async def stage(_stage_key: str, **values) -> None:
        assert launcher.process is not None and launcher.process.finished is False
        observed_while_running.append(values)

    async def owns_fence() -> bool:
        return True

    context = SimpleNamespace(
        cancellation=SimpleNamespace(is_cancelled=lambda: False),
        process_launcher=launcher,
        progress=SimpleNamespace(stage=stage),
        io=ExecutionIO(cancelled=lambda: False, owns_fence=owns_fence),
    )
    outcome = await run_mkvmerge_plan(
        context,
        boundary=boundary,
        args=(tool, "--synthetic"),
        candidate=candidate,
    )

    assert outcome.progress_samples == 1
    assert observed_while_running
