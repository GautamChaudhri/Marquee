"""JMC6I intentional-red runner-host cancellation contracts (I0 / plan §3.4).

The revalidated §2 finding: ``run_internal_operation()`` only terminates its child
when its *local* ``cancelled``/``timed_out``/``protocol_error`` flags are set. An
outer ``Task.cancel()`` (worker shutdown), the delivery ``asyncio.timeout``, or an
exception raised by the progress callback enters ``finally`` with all three flags
false and falls into an unbounded ``tracked.wait()`` on a child that was never told
to stop.

These contracts freeze the required behavior: every exit after launch either proves
normal termination or performs bounded cooperative/TERM/KILL cleanup, confirms
owned-tree death, and preserves the original cancellation/exception classification.
RED at the JMC6I plan base by design; phase I1 makes them green. They must never be
weakened, skipped, or xfailed.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
from pathlib import Path

import pytest

from marquee.core.filesystem import FilesystemBoundary, RootSpec
from marquee.core.jobs.internal_runner_host import run_internal_operation
from marquee.core.jobs.process_identity import ProcessIdentity, process_group_exists
from marquee.core.jobs.process_launcher import ProcessLauncher
from marquee.core.jobs.runner_protocol import RunnerOperation

# One outer cancellation/timeout must complete bounded cleanup well inside this
# window (cooperative 0.3s + term 0.3s + kill + 2s death confirmation < 8s).
_CLEANUP_BOUND_SECONDS = 8.0


def _launcher(tmp_path: Path, pids: list[int]) -> ProcessLauncher:
    async def record(identity: ProcessIdentity) -> bool:
        pids.append(identity.process_group_id)
        return True

    work = tmp_path / "work"
    work.mkdir(exist_ok=True)
    boundary = FilesystemBoundary(
        {"test": RootSpec(name="test", path=tmp_path, purpose="test", access="read_write")}
    )
    return ProcessLauncher(
        worker_node="jmc6i-node",
        boundary=boundary,
        working_directory=boundary.classify(work),
        record_identity=record,
    )


async def _drain_task(task: asyncio.Task, pids: list[int]) -> None:
    """Best-effort teardown so a red hang cannot leak children into the suite."""
    if not task.done():
        task.cancel()
        with contextlib.suppress(BaseException):
            await asyncio.wait_for(asyncio.shield(task), timeout=_CLEANUP_BOUND_SECONDS)
    if not task.done():
        task.cancel()
        await asyncio.wait({task}, timeout=_CLEANUP_BOUND_SECONDS)
    for pgid in pids:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(pgid, signal.SIGKILL)
    await asyncio.sleep(0.1)


@pytest.mark.asyncio
async def test_outer_task_cancellation_terminates_runner_tree(tmp_path: Path) -> None:
    """One outer Task.cancel() must run bounded cleanup, confirm owned-tree death,
    and re-raise CancelledError — never fall into an unbounded normal wait."""
    pids: list[int] = []
    launcher = _launcher(tmp_path, pids)
    live = asyncio.Event()

    async def on_progress(_frame: dict) -> None:
        live.set()

    task = asyncio.create_task(
        run_internal_operation(
            launcher,
            operation=RunnerOperation.NOOP,
            manifest={"params": {"stages": 1, "hold": "cooperative"}},
            on_progress=on_progress,
            cooperative_seconds=0.3,
            term_seconds=0.3,
        )
    )
    try:
        await asyncio.wait_for(live.wait(), timeout=15.0)
        task.cancel()
        done, _pending = await asyncio.wait({task}, timeout=_CLEANUP_BOUND_SECONDS)
        assert task in done, (
            "a single outer cancellation must complete bounded runner cleanup; "
            "the host hung in an unbounded tracked.wait() instead"
        )
        assert task.cancelled() or isinstance(task.exception(), asyncio.CancelledError), (
            "outer cancellation must be preserved for the caller, not converted"
        )
        assert pids, "the launched runner must have recorded its process-group identity"
        assert not process_group_exists(pids[0]), (
            "the owned process tree must be dead before cancellation completes"
        )
    finally:
        await _drain_task(task, pids)


@pytest.mark.asyncio
async def test_outer_delivery_timeout_terminates_runner_tree(tmp_path: Path) -> None:
    """The definition-owned delivery ``asyncio.timeout`` is the timeout authority.
    Its cancellation must trigger bounded cleanup and surface as TimeoutError."""
    pids: list[int] = []
    launcher = _launcher(tmp_path, pids)
    live = asyncio.Event()

    async def on_progress(_frame: dict) -> None:
        live.set()

    async def delivery() -> None:
        async with asyncio.timeout(1.0):
            await run_internal_operation(
                launcher,
                operation=RunnerOperation.NOOP,
                manifest={"params": {"stages": 1, "hold": "cooperative"}},
                on_progress=on_progress,
                cooperative_seconds=0.3,
                term_seconds=0.3,
            )

    task = asyncio.create_task(delivery())
    try:
        await asyncio.wait_for(live.wait(), timeout=15.0)
        done, _pending = await asyncio.wait({task}, timeout=_CLEANUP_BOUND_SECONDS)
        assert task in done, (
            "the delivery timeout must complete bounded runner cleanup; "
            "the host hung in an unbounded tracked.wait() instead"
        )
        assert isinstance(task.exception(), TimeoutError), (
            "the delivery timeout classification must be preserved, "
            f"got {task.exception()!r}"
        )
        assert pids and not process_group_exists(pids[0]), (
            "the owned process tree must be dead before the timeout propagates"
        )
    finally:
        await _drain_task(task, pids)


@pytest.mark.asyncio
async def test_worker_shutdown_during_progress_callback_terminates_tree(tmp_path: Path) -> None:
    """Cancellation delivered while the host awaits a progress callback must run
    the same bounded cleanup and preserve the cancellation."""
    pids: list[int] = []
    launcher = _launcher(tmp_path, pids)
    in_callback = asyncio.Event()

    async def on_progress(_frame: dict) -> None:
        in_callback.set()
        await asyncio.Event().wait()  # block inside the callback until cancelled

    task = asyncio.create_task(
        run_internal_operation(
            launcher,
            operation=RunnerOperation.NOOP,
            manifest={"params": {"stages": 1, "hold": "cooperative"}},
            on_progress=on_progress,
            cooperative_seconds=0.3,
            term_seconds=0.3,
        )
    )
    try:
        await asyncio.wait_for(in_callback.wait(), timeout=15.0)
        task.cancel()
        done, _pending = await asyncio.wait({task}, timeout=_CLEANUP_BOUND_SECONDS)
        assert task in done, "cancellation during a progress callback must stay bounded"
        assert task.cancelled() or isinstance(task.exception(), asyncio.CancelledError)
        assert pids and not process_group_exists(pids[0])
    finally:
        await _drain_task(task, pids)


@pytest.mark.asyncio
async def test_second_cancellation_during_cleanup_is_absorbed_and_honored(
    tmp_path: Path,
) -> None:
    """A cancel that lands while cleanup is running must not abandon the
    escalation; the task still completes cancelled with the tree dead."""
    pids: list[int] = []
    launcher = _launcher(tmp_path, pids)
    live = asyncio.Event()

    async def on_progress(_frame: dict) -> None:
        live.set()

    task = asyncio.create_task(
        run_internal_operation(
            launcher,
            operation=RunnerOperation.NOOP,
            # An ignore-hold forces the full cooperative/TERM/KILL escalation, so
            # the second cancel reliably lands inside cleanup.
            manifest={"params": {"stages": 1, "hold": "ignore"}},
            on_progress=on_progress,
            cooperative_seconds=0.4,
            term_seconds=0.4,
        )
    )
    try:
        await asyncio.wait_for(live.wait(), timeout=15.0)
        task.cancel()
        await asyncio.sleep(0.15)
        task.cancel()
        done, _pending = await asyncio.wait({task}, timeout=_CLEANUP_BOUND_SECONDS)
        assert task in done, "repeated cancellation must not abandon bounded cleanup"
        assert task.cancelled() or isinstance(task.exception(), asyncio.CancelledError)
        assert pids and not process_group_exists(pids[0])
    finally:
        await _drain_task(task, pids)


@pytest.mark.asyncio
async def test_result_then_resistant_child_is_bounded_and_not_success(tmp_path: Path) -> None:
    """A child that returns a result frame but outlives its exit grace and resists
    TERM is escalated to SIGKILL and cannot be reported as success."""
    pids: list[int] = []
    launcher = _launcher(tmp_path, pids)
    outcome = await run_internal_operation(
        launcher,
        operation=RunnerOperation.NOOP,
        manifest={"params": {"stages": 1, "hold": "result_then_ignore"}},
        cooperative_seconds=0.2,
        term_seconds=0.2,
        exit_grace_seconds=0.4,
    )
    assert outcome.outcome == "failed"
    assert outcome.exit_signal is not None
    assert pids and not process_group_exists(pids[0])


@pytest.mark.asyncio
async def test_result_then_cooperative_linger_still_succeeds_bounded(tmp_path: Path) -> None:
    """A valid result followed by a cooperative lingering exit is nudged within the
    grace and remains an honest success with a confirmed-dead tree."""
    pids: list[int] = []
    launcher = _launcher(tmp_path, pids)
    outcome = await run_internal_operation(
        launcher,
        operation=RunnerOperation.NOOP,
        manifest={"params": {"stages": 1, "echo": "x", "hold": "result_then_cooperative"}},
        cooperative_seconds=1.0,
        term_seconds=0.3,
        exit_grace_seconds=0.4,
    )
    assert outcome.outcome == "succeeded"
    assert outcome.summary == {"echo": "x"}
    assert pids and not process_group_exists(pids[0])


@pytest.mark.asyncio
async def test_clean_eof_without_result_is_bounded_failure(tmp_path: Path) -> None:
    """A clean control-channel EOF with no result and a still-running child must
    escalate within the exit grace and report an honest failure."""
    pids: list[int] = []
    launcher = _launcher(tmp_path, pids)
    outcome = await run_internal_operation(
        launcher,
        operation=RunnerOperation.NOOP,
        manifest={"params": {"stages": 1, "hold": "close_control_then_hold"}},
        cooperative_seconds=1.0,
        term_seconds=0.3,
        exit_grace_seconds=0.4,
    )
    assert outcome.outcome == "failed"
    assert outcome.error is not None and outcome.error["code"] == "NoResult"
    assert pids and not process_group_exists(pids[0])


@pytest.mark.asyncio
async def test_death_confirmation_failure_is_hard_failure_not_cancellation(
    tmp_path: Path, monkeypatch
) -> None:
    """If owned-tree death cannot be confirmed, the host must raise a hard
    operational failure — never acknowledge a successful cancellation."""
    from marquee.core.jobs.process_launcher import ProcessLaunchError, TrackedProcess

    pids: list[int] = []
    launcher = _launcher(tmp_path, pids)

    async def broken_cancel(self, **_kwargs):
        raise ProcessLaunchError("simulated: process-tree death could not be confirmed")

    monkeypatch.setattr(TrackedProcess, "cancel", broken_cancel)
    with pytest.raises(ProcessLaunchError):
        await run_internal_operation(
            launcher,
            operation=RunnerOperation.NOOP,
            manifest={"params": {"stages": 1, "hold": "cooperative"}},
            should_stop=lambda: True,
            cooperative_seconds=0.3,
            term_seconds=0.3,
        )
    monkeypatch.undo()
    # The child leaked by the simulated confirmation failure is cleaned here.
    for pgid in pids:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(pgid, signal.SIGKILL)
    await asyncio.sleep(0.2)


@pytest.mark.asyncio
async def test_progress_callback_error_terminates_runner_tree(tmp_path: Path) -> None:
    """An unexpected progress-callback exception must run the same bounded cleanup
    and then surface the original exception — not hang or report success."""
    pids: list[int] = []
    launcher = _launcher(tmp_path, pids)

    async def on_progress(_frame: dict) -> None:
        raise RuntimeError("progress callback exploded")

    task = asyncio.create_task(
        run_internal_operation(
            launcher,
            operation=RunnerOperation.NOOP,
            manifest={"params": {"stages": 1, "hold": "cooperative"}},
            on_progress=on_progress,
            cooperative_seconds=0.3,
            term_seconds=0.3,
        )
    )
    try:
        done, _pending = await asyncio.wait({task}, timeout=_CLEANUP_BOUND_SECONDS)
        assert task in done, (
            "a progress-callback failure must complete bounded runner cleanup; "
            "the host hung in an unbounded tracked.wait() instead"
        )
        exception = task.exception()
        assert isinstance(exception, RuntimeError), (
            f"the original callback exception must be preserved, got {exception!r}"
        )
        assert pids and not process_group_exists(pids[0]), (
            "the owned process tree must be dead before the callback error propagates"
        )
    finally:
        await _drain_task(task, pids)
