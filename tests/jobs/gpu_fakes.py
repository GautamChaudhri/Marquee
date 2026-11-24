"""Functions the GPU child runs in tests, standing in for the real GPU kinds. No GPU.

The tests put this folder on ``sys.path`` before the child starts; a spawned child takes
its parent's path, so it can import this module by name.
"""

from __future__ import annotations

import asyncio
import multiprocessing
import os
import signal
import time
from concurrent.futures import ProcessPoolExecutor
from typing import Any

from marquee.cancellation import JobCancelledError
from marquee.jobs.registry import PermanentJobError

_calls = 0


def echo(payload: dict, cancel: Any, progress: Any) -> dict:
    global _calls
    _calls += 1
    progress(1, 2, "half way")
    return {"echo": payload, "pid": os.getpid(), "calls": _calls}


def wait_for_cancel(payload: dict, cancel: Any, progress: Any) -> None:
    progress(None, None, "waiting")
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if cancel.is_set():
            raise JobCancelledError("stopped as asked")
        time.sleep(0.05)
    raise AssertionError("no cancel arrived")


def hang(payload: dict, cancel: Any, progress: Any) -> None:
    """Ignores cancel, as a hung native call would."""
    progress(None, None, "hanging")
    time.sleep(60)


def exit_now(payload: dict, cancel: Any, progress: Any) -> None:
    os._exit(int(payload.get("code", 3)))


def fail(payload: dict, cancel: Any, progress: Any) -> None:
    raise ValueError("bad value\nsecond line")


def fail_permanently(payload: dict, cancel: Any, progress: Any) -> None:
    raise PermanentJobError("retrying cannot help")


def not_json(payload: dict, cancel: Any, progress: Any) -> dict:
    return {"score": float("nan")}


def _square(n: int) -> int:
    return n * n


def pool(payload: dict, cancel: Any, progress: Any) -> dict:
    """The child can start processes of its own, as the OCR stage does."""
    context = multiprocessing.get_context("spawn")
    with ProcessPoolExecutor(max_workers=2, mp_context=context) as executor:
        squares = list(executor.map(_square, range(5)))
    return {"squares": squares}


def uses_asyncio(payload: dict, cancel: Any, progress: Any) -> dict:
    async def work() -> int:
        await asyncio.sleep(0.01)
        return 7

    return {"value": asyncio.run(work())}


# Created at import, as the engine creates its download semaphores.
_IMPORT_TIME_SEMAPHORE = asyncio.Semaphore(2)


def semaphore_downloads(payload: dict, cancel: Any, progress: Any) -> dict:
    """Waits on an import-time semaphore, as the engine's downloads do."""
    from marquee.jobs.gpu_child import run_async

    async def download(n: int) -> int:
        async with _IMPORT_TIME_SEMAPHORE:
            await asyncio.sleep(0.01)
            return n

    async def all_downloads() -> list[int]:
        return await asyncio.gather(*(download(n) for n in range(8)))

    return {"downloaded": len(run_async(all_downloads()))}


def leaves_a_task(payload: dict, cancel: Any, progress: Any) -> dict:
    """Leaves a task running when its coroutine ends; it must not survive into the next job."""
    from marquee.jobs.gpu_child import run_async

    async def lingering() -> None:
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            _left_behind["cancelled"] += 1
            raise

    async def main() -> int:
        asyncio.get_running_loop().create_task(lingering())
        await asyncio.sleep(0)
        return len(asyncio.all_tasks())

    cancelled_before = _left_behind["cancelled"]
    return {"tasks": run_async(main()), "cancelled_before": cancelled_before}


_left_behind = {"cancelled": 0}


def _sleep_forever() -> None:
    time.sleep(120)


def start_sleepers(payload: dict, cancel: Any, progress: Any) -> None:
    """Start two processes the way the OCR pool does, report their pids, then block."""
    context = multiprocessing.get_context("spawn")
    sleepers = [context.Process(target=_sleep_forever) for _ in range(2)]
    for sleeper in sleepers:
        sleeper.start()
    progress(None, None, " ".join(str(s.pid) for s in sleepers))
    time.sleep(120)


def kill_self(payload: dict, cancel: Any, progress: Any) -> None:
    os.kill(os.getpid(), signal.SIGKILL)


def worker_with_sleepers(report: Any) -> None:
    """Stands in for the worker: runs ``start_sleepers`` in a GPU child, then waits to be
    killed. Puts the child's pid and the sleepers' pids on ``report``."""
    from marquee.jobs.gpu_child import GpuChild

    async def main() -> None:
        child = GpuChild()

        def progress(current: Any, total: Any, message: str | None) -> None:
            report.put((child.pid, [int(p) for p in (message or "").split()]))

        class Never:
            def is_set(self) -> bool:
                return False

        await child.run("gpu_fakes:start_sleepers", {}, Never(), progress)

    asyncio.run(main())
