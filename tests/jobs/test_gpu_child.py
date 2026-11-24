"""The GPU child process, with fake functions (tests/jobs/gpu_fakes.py) and no GPU."""

from __future__ import annotations

import asyncio
import multiprocessing
import os
import signal
import threading
import time
from collections.abc import AsyncIterator, Iterable
from pathlib import Path
from typing import Any

import psutil
import pytest

from marquee.cancellation import JobCancelledError
from marquee.jobs.gpu_child import GpuChild, GpuJobError, GpuProcessDiedError, describe_error
from marquee.jobs.registry import PermanentJobError

FAKES = Path(__file__).resolve().parent


@pytest.fixture
def fakes_on_path(monkeypatch: pytest.MonkeyPatch) -> None:
    # A spawned child starts with its parent's sys.path.
    monkeypatch.syspath_prepend(str(FAKES))


@pytest.fixture
async def child(fakes_on_path: None) -> AsyncIterator[GpuChild]:
    child = GpuChild()
    try:
        yield child
    finally:
        child.kill()


async def _run(
    child: GpuChild,
    function: str,
    payload: dict | None = None,
    cancel: threading.Event | None = None,
    progress: list | None = None,
    timeout: float = 30,
) -> Any:
    seen = progress if progress is not None else []
    return await asyncio.wait_for(
        child.run(
            f"gpu_fakes:{function}",
            payload or {},
            cancel or threading.Event(),
            lambda *values: seen.append(values),
        ),
        timeout,
    )


def _alive(pid: int) -> bool:
    try:
        return psutil.Process(pid).status() != psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return False


def _all_gone(pids: Iterable[int], within: float = 5.0) -> bool:
    deadline = time.monotonic() + within
    while time.monotonic() < deadline:
        if not any(_alive(pid) for pid in pids):
            return True
        time.sleep(0.1)
    return False


async def test_a_result_and_progress_come_back(child: GpuChild) -> None:
    progress: list = []

    result = await _run(child, "echo", {"title": "Die Hard"}, progress=progress)

    assert result["echo"] == {"title": "Die Hard"}
    assert result["pid"] == child.pid != os.getpid()
    assert progress == [(1, 2, "half way")]
    assert not child._process.daemon


async def test_the_child_is_kept_between_jobs(child: GpuChild) -> None:
    first = await _run(child, "echo")
    second = await _run(child, "echo")

    assert first["pid"] == second["pid"]
    assert (first["calls"], second["calls"]) == (1, 2)


async def test_cancel_reaches_the_function_and_the_child_lives_on(child: GpuChild) -> None:
    cancel = threading.Event()
    asyncio.get_running_loop().call_later(0.5, cancel.set)

    with pytest.raises(JobCancelledError, match="stopped as asked"):
        await _run(child, "wait_for_cancel", cancel=cancel)

    pid = child.pid
    assert pid is not None
    assert (await _run(child, "echo"))["pid"] == pid


async def test_errors_come_back_as_one_line(child: GpuChild) -> None:
    with pytest.raises(GpuJobError) as failed:
        await _run(child, "fail")
    assert str(failed.value) == "ValueError: bad value"

    with pytest.raises(PermanentJobError, match="retrying cannot help"):
        await _run(child, "fail_permanently")

    with pytest.raises(GpuJobError, match="ValueError: Out of range float"):
        await _run(child, "not_json")

    assert child.alive


async def test_a_child_that_exits_mid_job_fails_it(child: GpuChild) -> None:
    with pytest.raises(GpuProcessDiedError, match=r"the GPU process died \(exit code 3\)"):
        await _run(child, "exit_now", {"code": 3})

    assert not child.alive


async def test_a_killed_child_fails_the_job_and_leaves_nothing_behind(child: GpuChild) -> None:
    progress: list = []
    job = asyncio.create_task(_run(child, "start_sleepers", progress=progress))
    deadline = time.monotonic() + 30
    while not progress and time.monotonic() < deadline:
        await asyncio.sleep(0.05)
    sleepers = [int(pid) for pid in progress[0][2].split()]
    pid = child.pid
    assert pid is not None and all(_alive(p) for p in sleepers)

    os.kill(pid, signal.SIGKILL)
    with pytest.raises(GpuProcessDiedError, match=r"signal 9, SIGKILL"):
        await job

    assert _all_gone([pid, *sleepers])


async def test_a_new_child_serves_the_next_job(child: GpuChild) -> None:
    with pytest.raises(GpuProcessDiedError):
        await _run(child, "kill_self")

    result = await _run(child, "echo")

    assert result["calls"] == 1
    assert child.alive


async def test_an_abandoned_job_kills_the_child(child: GpuChild) -> None:
    progress: list = []
    with pytest.raises(TimeoutError):
        await _run(child, "hang", progress=progress, timeout=3)

    assert progress == [(None, None, "hanging")]
    assert not child.alive


async def test_stop_ends_the_child(child: GpuChild) -> None:
    assert child.idle_seconds() is None
    await _run(child, "echo")
    pid = child.pid
    assert pid is not None
    await asyncio.sleep(0.2)
    idle = child.idle_seconds()
    assert idle is not None and idle >= 0.2

    child.stop()

    assert not child.alive and child.idle_seconds() is None
    assert _all_gone([pid])


async def test_the_child_can_start_a_process_pool(child: GpuChild) -> None:
    assert await _run(child, "pool") == {"squares": [0, 1, 4, 9, 16]}


async def test_the_child_can_run_asyncio(child: GpuChild) -> None:
    assert await _run(child, "uses_asyncio") == {"value": 7}


def test_a_dead_worker_takes_its_child_and_grandchildren_with_it(fakes_on_path: None) -> None:
    import gpu_fakes  # type: ignore[import-not-found]

    context = multiprocessing.get_context("spawn")
    report = context.Queue()
    worker = context.Process(target=gpu_fakes.worker_with_sleepers, args=(report,))
    worker.start()
    try:
        child_pid, sleepers = report.get(timeout=60)
        assert _alive(child_pid) and all(_alive(p) for p in sleepers)

        os.kill(worker.pid, signal.SIGKILL)
        worker.join(5)

        assert _all_gone([child_pid, *sleepers])
    finally:
        if worker.is_alive():
            worker.kill()
        worker.join(5)
        report.close()


def test_describe_error() -> None:
    assert describe_error(ValueError("bad\nmore")) == "ValueError: bad"
    assert describe_error(RuntimeError()) == "RuntimeError"
    assert describe_error(GpuProcessDiedError("the GPU process died")) == "the GPU process died"
    assert len(describe_error(ValueError("x" * 2000))) == 500
