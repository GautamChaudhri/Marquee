"""WorkerSupervisor crash-loop detection and status surfacing."""

from __future__ import annotations

import asyncio

import marquee.core.jobs.supervisor as supervisor_module
from marquee.core.jobs.supervisor import (
    MAX_CONSECUTIVE_FAST_FAILURES,
    WorkerSupervisor,
    _Child,
)

_REAL_SLEEP = asyncio.sleep


async def _instant_sleep(*_args, **_kwargs) -> None:
    await _REAL_SLEEP(0)


class _FakeProc:
    def __init__(self, returncode: int = 1) -> None:
        self.pid = 4242
        self.returncode: int | None = None
        self._final_returncode = returncode

    async def wait(self) -> int:
        self.returncode = self._final_returncode
        return self._final_returncode


async def test_supervise_gives_up_after_max_consecutive_fast_failures(monkeypatch):
    monkeypatch.setattr(supervisor_module.asyncio, "sleep", _instant_sleep)
    supervisor = WorkerSupervisor()
    child = _Child("test-child", ["-m", "fake"])
    child.proc = _FakeProc()

    async def fake_spawn(c: _Child) -> None:
        c.proc = _FakeProc()

    monkeypatch.setattr(supervisor, "_spawn", fake_spawn)

    await supervisor._supervise(child)

    assert child.degraded is True
    assert child.fast_failures == MAX_CONSECUTIVE_FAST_FAILURES
    status = supervisor.status()  # supervisor._children is empty in this unit test
    assert status == {"degraded": False, "children": []}


async def test_supervise_resets_fast_failure_count_after_long_run(monkeypatch):
    monkeypatch.setattr(supervisor_module.asyncio, "sleep", _instant_sleep)
    fake_clock = [0.0]
    monkeypatch.setattr(supervisor_module.time, "monotonic", lambda: fake_clock[0])

    supervisor = WorkerSupervisor()
    child = _Child("test-child", ["-m", "fake"])
    child.fast_failures = MAX_CONSECUTIVE_FAST_FAILURES - 1

    call_count = 0

    class _FirstRunIsSlowProc(_FakeProc):
        async def wait(self) -> int:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                fake_clock[0] += supervisor_module.FAST_FAILURE_WINDOW_SECONDS + 1
            self.returncode = self._final_returncode
            return self._final_returncode

    child.proc = _FirstRunIsSlowProc()

    async def fake_spawn(c: _Child) -> None:
        c.proc = _FakeProc()  # subsequent runs are instant crashes again

    monkeypatch.setattr(supervisor, "_spawn", fake_spawn)

    await supervisor._supervise(child)

    # Ran long once (resets counter to 0), then needs MAX_CONSECUTIVE_FAST_FAILURES
    # fresh fast failures before giving up again.
    assert child.degraded is True
    assert child.fast_failures == MAX_CONSECUTIVE_FAST_FAILURES


def test_status_reports_aggregate_degraded():
    supervisor = WorkerSupervisor()
    healthy = _Child("scheduler", [])
    broken = _Child("worker-0", [])
    broken.degraded = True
    broken.fast_failures = MAX_CONSECUTIVE_FAST_FAILURES
    supervisor._children = [healthy, broken]

    status = supervisor.status()

    assert status["degraded"] is True
    names = {c["name"]: c for c in status["children"]}
    assert names["scheduler"]["degraded"] is False
    assert names["worker-0"]["degraded"] is True
    assert names["worker-0"]["fast_failures"] == MAX_CONSECUTIVE_FAST_FAILURES
