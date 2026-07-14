from __future__ import annotations

import asyncio
import os
import signal
from pathlib import Path

import pytest

from marquee.core.filesystem import FilesystemBoundary, RootSpec
from marquee.core.jobs.orphan_reconciliation import terminate_verified_orphan
from marquee.core.jobs.process_canary import CanaryBehavior
from marquee.core.jobs.process_identity import (
    IdentityStatus,
    ProcessIdentity,
    containment_capabilities,
    read_process_start_ticks,
)
from marquee.core.jobs.process_launcher import (
    ProcessLauncher,
    ProcessLaunchError,
    TerminationStage,
    UnsafeProcessIdentityError,
)
from marquee.media import binaries


def _launcher(tmp_path: Path, **kwargs: object) -> ProcessLauncher:
    work = tmp_path / "work"
    work.mkdir()
    boundary = FilesystemBoundary(
        {"test": RootSpec(name="test", path=tmp_path, purpose="test", access="read_write")}
    )
    classified = boundary.classify(work)
    return ProcessLauncher(
        worker_node="test-node",
        boundary=boundary,
        working_directory=classified,
        **kwargs,
    )


def test_proc_stat_parser_handles_spaces_and_parentheses(tmp_path: Path) -> None:
    stat = tmp_path / "123" / "stat"
    stat.parent.mkdir()
    suffix = ["S"] + [str(index) for index in range(4, 23)]
    stat.write_text(f"123 (odd process (name)) {' '.join(suffix)}\n", encoding="ascii")
    assert read_process_start_ticks(123, proc_root=tmp_path) == 22


def test_containment_capability_is_honest() -> None:
    capability = containment_capabilities()
    assert capability.tier in {"cgroup_v2", "process_group", "unsupported"}
    if capability.tier == "process_group":
        assert capability.process_groups is True
        assert capability.proc_identity is True
        assert capability.cgroup_v2_delegated is False


@pytest.mark.asyncio
async def test_clean_canary_records_identity_before_start(tmp_path: Path) -> None:
    identities: list[ProcessIdentity] = []

    async def record(identity: ProcessIdentity) -> bool:
        identities.append(identity)
        return True

    process = await _launcher(tmp_path, record_identity=record).launch_canary(
        CanaryBehavior.CLEAN_EXIT
    )
    summary = await process.wait()
    assert summary.exit_code == 0
    assert summary.termination_stage == TerminationStage.EXITED
    assert identities == [process.identity]
    assert process.identity.pid == process.identity.process_group_id


@pytest.mark.asyncio
async def test_both_high_output_pipes_are_drained_and_bounded(tmp_path: Path) -> None:
    process = await _launcher(tmp_path, capture_limit=4096).launch_canary(
        CanaryBehavior.BOUNDED_OUTPUT
    )
    summary = await asyncio.wait_for(process.wait(), timeout=5)
    assert summary.stdout.total_bytes == 1024 * 1024
    assert summary.stderr.total_bytes == 1024 * 1024
    assert len(summary.stdout.captured) == len(summary.stderr.captured) == 4096
    assert summary.stdout.truncated and summary.stderr.truncated


@pytest.mark.asyncio
async def test_process_launch_does_not_block_event_loop(tmp_path: Path) -> None:
    ticks = 0
    running = True

    async def ticker() -> None:
        nonlocal ticks
        while running:
            ticks += 1
            await asyncio.sleep(0)

    ticker_task = asyncio.create_task(ticker())
    process = await _launcher(tmp_path).launch_canary(CanaryBehavior.BOUNDED_OUTPUT)
    await process.wait()
    running = False
    await ticker_task
    assert ticks > 10


@pytest.mark.asyncio
async def test_cooperative_cancellation_confirms_tree_death(tmp_path: Path) -> None:
    process = await _launcher(tmp_path).launch_canary(CanaryBehavior.COOPERATIVE_WAIT)
    summary = await process.cancel(cooperative_seconds=1, term_seconds=0.1)
    assert summary.exit_code == 0
    assert summary.termination_stage == TerminationStage.COOPERATIVE


@pytest.mark.asyncio
async def test_ignoring_process_tree_reaches_kill(tmp_path: Path) -> None:
    process = await _launcher(tmp_path).launch_canary(CanaryBehavior.IGNORE_UNTIL_KILL)
    summary = await process.cancel(cooperative_seconds=0.05, term_seconds=0.05)
    assert summary.exit_signal == signal.SIGKILL
    assert summary.termination_stage == TerminationStage.KILL


@pytest.mark.asyncio
async def test_identity_mismatch_prevents_signaling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    process = await _launcher(tmp_path).launch_canary(CanaryBehavior.COOPERATIVE_WAIT)
    monkeypatch.setattr(
        "marquee.core.jobs.process_launcher.verify_process_identity",
        lambda _identity: IdentityStatus.MISMATCH,
    )
    with pytest.raises(UnsafeProcessIdentityError):
        await process.cancel(cooperative_seconds=0.05, term_seconds=0.05)
    monkeypatch.undo()
    os.killpg(process.identity.process_group_id, signal.SIGKILL)
    await process.wait()


@pytest.mark.asyncio
async def test_orphan_mismatch_never_signals(monkeypatch: pytest.MonkeyPatch) -> None:
    identity = ProcessIdentity("node", "wrong-boot", os.getpid(), os.getpgrp(), 1)
    called = False

    def signal_group(*_: object) -> None:
        nonlocal called
        called = True

    monkeypatch.setattr(os, "killpg", signal_group)
    status = await terminate_verified_orphan(
        identity, cooperative_seconds=0.01, term_seconds=0.01
    )
    assert status == IdentityStatus.MISMATCH
    assert called is False


@pytest.mark.asyncio
async def test_launcher_rejects_non_enum_and_unconfined_cwd(tmp_path: Path) -> None:
    launcher = _launcher(tmp_path)
    with pytest.raises(ProcessLaunchError):
        await launcher.launch_canary("clean_exit")  # type: ignore[arg-type]
    (tmp_path / "work").rmdir()
    with pytest.raises(ProcessLaunchError):
        await launcher.launch_canary(CanaryBehavior.CLEAN_EXIT)


@pytest.mark.skipif(binaries.resolve("ffprobe") is None, reason="ffprobe is not available")
@pytest.mark.asyncio
async def test_launch_runs_catalog_tool_and_captures_stdout(tmp_path: Path) -> None:
    process = await _launcher(tmp_path).launch("ffprobe", ["-version"])
    summary = await process.wait()
    assert summary.exit_code == 0
    assert b"ffprobe" in summary.stdout.captured


@pytest.mark.asyncio
async def test_launch_rejects_uncatalogued_tool(tmp_path: Path) -> None:
    with pytest.raises(ProcessLaunchError, match="catalog"):
        await _launcher(tmp_path).launch("rm", ["-rf", "/tmp/nope"])
