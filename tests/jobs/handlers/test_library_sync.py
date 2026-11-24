from __future__ import annotations

import threading
from typing import Any

import pytest

from marquee.jobs.handlers.library_sync import library_sync
from marquee.jobs.registry import JobContext, PermanentJobError
from marquee.library import sync


def _context() -> JobContext:
    return JobContext(job_id=1, payload={}, cancel=threading.Event(), progress=lambda *_: None)


def _sync_does(monkeypatch: pytest.MonkeyPatch, outcome: Any) -> dict[str, Any]:
    seen: dict[str, Any] = {}

    async def fake(*, dry_run: bool = False, cancel: Any = None) -> sync.SyncReport:
        seen.update(dry_run=dry_run, cancel=cancel)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    monkeypatch.setattr(sync, "sync_from_settings", fake)
    return seen


async def test_the_result_is_the_report(monkeypatch: pytest.MonkeyPatch) -> None:
    report = sync.SyncReport(dry_run=False, movies_listed=489, with_file=155, posters_found=147)
    seen = _sync_does(monkeypatch, report)
    context = _context()

    result = await library_sync(context)

    assert result == {"skipped": False, "ok": True, **report.to_dict()}
    assert seen == {"dry_run": False, "cancel": context.cancel}


async def test_another_sync_running_is_a_skip(monkeypatch: pytest.MonkeyPatch) -> None:
    _sync_does(monkeypatch, sync.SyncAlreadyRunningError("another sync is running"))

    assert await library_sync(_context()) == {
        "skipped": True,
        "reason": "another sync is running",
    }


async def test_a_path_mapping_failure_is_permanent(monkeypatch: pytest.MonkeyPatch) -> None:
    _sync_does(monkeypatch, sync.SyncPathError("Radarr's root folder does not map"))

    with pytest.raises(PermanentJobError, match="does not map"):
        await library_sync(_context())


async def test_other_errors_are_left_to_the_retry_rule(monkeypatch: pytest.MonkeyPatch) -> None:
    _sync_does(monkeypatch, sync.SyncError("Radarr's movie list is not a list"))

    with pytest.raises(sync.SyncError) as raised:
        await library_sync(_context())
    assert not isinstance(raised.value, PermanentJobError)
