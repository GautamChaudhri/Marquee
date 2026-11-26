"""The maintenance jobs, with the dump itself faked (tests/maintenance runs the dump code)."""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from marquee.config import settings
from marquee.jobs.handlers.maintenance import DB_BACKUP, db_backup
from marquee.jobs.registry import JobContext, JobError, PermanentJobError
from marquee.maintenance import dumps

NOON = datetime(2026, 11, 26, 12, 0, 0, tzinfo=UTC)


def _context() -> JobContext:
    return JobContext(job_id=1, payload={}, cancel=threading.Event(), progress=lambda *_: None)


@pytest.fixture
def folder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path / "data")
    settings.db_backups_dir.mkdir(parents=True)
    return settings.db_backups_dir


def _fake_dump(monkeypatch: pytest.MonkeyPatch, outcome: Any = None) -> list[dict[str, Any]]:
    seen: list[dict[str, Any]] = []

    def make_dump(kind: str, *, cancel: Any = None, **_: Any) -> dumps.DumpResult:
        seen.append({"kind": kind, "cancel": cancel})
        if isinstance(outcome, BaseException):
            raise outcome
        name = dumps.dump_name("scheduled", NOON + timedelta(days=2), "0007")
        (settings.db_backups_dir / name).write_bytes(b"dump")
        return dumps.DumpResult(
            file=name,
            kind="scheduled",
            size_bytes=4,
            sha256="a" * 64,
            revision="0007",
            seconds=0.5,
            server_major=19,
            client_major=19,
        )

    monkeypatch.setattr(dumps, "make_dump", make_dump)
    return seen


async def test_a_backup_records_its_dump_and_keeps_the_newest_few(
    folder: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    older = [dumps.dump_name("scheduled", NOON + timedelta(days=d), "0006") for d in (0, 1)]
    for name in older:
        (folder / name).write_bytes(b"old")
    manual = dumps.dump_name("manual", NOON - timedelta(days=9), "0006")
    (folder / manual).write_bytes(b"mine")
    monkeypatch.setattr(settings, "DB_BACKUP_KEEP", 2)
    context = _context()
    seen = _fake_dump(monkeypatch)

    result = await db_backup(context)

    assert seen == [{"kind": "scheduled", "cancel": context.cancel}]
    assert result == {
        "file": "marquee-20261128T120000Z-0007.dump",
        "kind": "scheduled",
        "size_bytes": 4,
        "sha256": "a" * 64,
        "revision": "0007",
        "seconds": 0.5,
        "server_major": 19,
        "client_major": 19,
        "deleted": [older[0]],
        "keep": 2,
    }
    assert sorted(p.name for p in folder.iterdir()) == sorted(
        [older[1], manual, "marquee-20261128T120000Z-0007.dump"]
    )


async def test_missing_tools_are_permanent_and_other_failures_retry(
    folder: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_dump(monkeypatch, dumps.DumpToolsError("pg_dump is not installed"))
    with pytest.raises(PermanentJobError, match="pg_dump is not installed"):
        await db_backup(_context())

    _fake_dump(monkeypatch, dumps.DumpError("pg_dump failed (exit 1) on marquee@db:5432/m: x"))
    with pytest.raises(JobError, match="pg_dump failed") as raised:
        await db_backup(_context())
    assert not isinstance(raised.value, PermanentJobError)


def test_the_kind() -> None:
    assert (DB_BACKUP.job_class, DB_BACKUP.max_attempts, DB_BACKUP.dedupe_key) == (
        "maintenance",
        3,
        "db_backup",
    )


async def test_the_retention_job_records_counts_and_bytes_but_no_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from datetime import datetime

    from marquee.jobs.handlers import maintenance
    from marquee.maintenance import retention

    report = retention.Report(
        now=datetime(2027, 3, 1, tzinfo=UTC),
        dry_run=False,
        items=[
            retention.Item("thumbnails", "cache/thumbs/x-320.jpg", 5),
            retention.Item("jobs", "job 4", rows=3),
        ],
        kept=["leftovers: cache/deploys/9.jpg is kept: a queued or running job may own it"],
    )

    async def fake_run(session: Any, **_: Any) -> retention.Report:
        return report

    monkeypatch.setattr(retention, "run", fake_run)
    monkeypatch.setattr(maintenance, "create_engine", lambda: _FakeEngine())
    monkeypatch.setattr(maintenance, "create_session_factory", lambda engine: _FakeSessions)

    result = await maintenance.retention_pass(_context())

    assert result["count"] == 2 and result["bytes"] == 5 and result["kept"] == 1
    assert result["rules"]["thumbnails"] == {"count": 1, "bytes": 5, "rows": 0}
    assert result["rules"]["jobs"] == {"count": 1, "bytes": 0, "rows": 3}
    assert "cache/" not in str(result) and "items" not in result


async def test_a_refused_retention_is_permanent(monkeypatch: pytest.MonkeyPatch) -> None:
    from marquee.jobs.handlers import maintenance
    from marquee.maintenance import retention

    async def refuses(session: Any, **_: Any) -> retention.Report:
        raise retention.RetentionRefusedError("the data folder is inside the movie root /m")

    monkeypatch.setattr(retention, "run", refuses)
    monkeypatch.setattr(maintenance, "create_engine", lambda: _FakeEngine())
    monkeypatch.setattr(maintenance, "create_session_factory", lambda engine: _FakeSessions)

    with pytest.raises(PermanentJobError, match="inside the movie root"):
        await maintenance.retention_pass(_context())


class _FakeEngine:
    async def dispose(self) -> None:
        return None


class _FakeSessions:
    async def __aenter__(self) -> object:
        return object()

    async def __aexit__(self, *_: object) -> None:
        return None
