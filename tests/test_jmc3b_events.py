from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import asyncpg
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, func, select, text

from marquee.config import settings
from marquee.core.jobs.event_service import (
    JOB_EVENT_CHANNEL,
    JobEventContractError,
    job_event_writer,
)
from marquee.core.jobs.event_stream import (
    EventClient,
    JobEventDelta,
    JobEventFrame,
    JobEventTailer,
    frame_from_event,
)
from marquee.main import app
from marquee.models import Job, JobEvent

NOW = datetime(2026, 7, 13, 13, 0, tzinfo=UTC)


def _job(job_id: str = "events000000000000000000000001") -> Job:
    return Job(
        id=job_id,
        type="system_noop",
        request={"echo": {"job_id": job_id}},
        phase="queued",
        desired_state="run",
        fence_token=3,
        priority=50,
        eligible_at=NOW,
        dispatch_generation=1,
        root_id=job_id,
        trigger_kind="system",
        feature_area="system",
        presentation_family="system",
        subject_kind="system_work",
        subject_reference="system:noop",
        subject_snapshot={
            "version": 1,
            "kind": "system_work",
            "display_id": "system:noop",
            "display_name": "System no-op",
            "snapshot_at": NOW.isoformat(),
            "work": "noop",
        },
        created_at=NOW,
        queued_at=NOW,
    )


async def _append(db, *, key: str = "job.queued", message: str = "queued") -> JobEvent:
    return await job_event_writer.append(
        db,
        job_id="events000000000000000000000001",
        event_key=key,
        state="queued",
        message=message,
        detail={"dispatch_generation": 1},
    )


async def test_event_notification_is_a_commit_scoped_cursor_hint(db) -> None:
    job = _job()
    db.add(job)
    await db.commit()
    schema = await db.scalar(text("SELECT current_schema()"))
    await db.rollback()
    dsn = settings.db_url_resolved.replace("postgresql+asyncpg://", "postgresql://", 1)
    listener = await asyncpg.connect(
        dsn,
        server_settings={"search_path": schema, "application_name": "marquee:test:event-listener"},
    )
    cursors: asyncio.Queue[str] = asyncio.Queue()

    def notified(connection, pid, channel, payload) -> None:
        del connection, pid, channel
        cursors.put_nowait(payload)

    await listener.add_listener(JOB_EVENT_CHANNEL, notified)
    try:
        event = await _append(db)
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(cursors.get(), timeout=0.05)
        await db.commit()
        assert await asyncio.wait_for(cursors.get(), timeout=1) == str(event.id)
    finally:
        await listener.close()


async def test_rolled_back_event_is_neither_durable_nor_notified(db) -> None:
    db.add(_job())
    await db.commit()
    schema = await db.scalar(text("SELECT current_schema()"))
    dsn = settings.db_url_resolved.replace("postgresql+asyncpg://", "postgresql://", 1)
    listener = await asyncpg.connect(dsn, server_settings={"search_path": schema})
    notified = asyncio.Event()

    def callback(*args) -> None:
        del args
        notified.set()

    await listener.add_listener(JOB_EVENT_CHANNEL, callback)
    try:
        await _append(db, message="must roll back")
        await db.rollback()
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(notified.wait(), timeout=0.05)
        assert await db.scalar(select(func.count(JobEvent.id))) == 0
    finally:
        await listener.close()


async def test_event_writer_rejects_unknown_and_sensitive_detail(db) -> None:
    db.add(_job())
    await db.commit()
    with pytest.raises(JobEventContractError, match="unknown semantic"):
        await job_event_writer.append(
            db,
            job_id="events000000000000000000000001",
            event_key="transport.raw_row",
            state="queued",
        )
    with pytest.raises(JobEventContractError, match="not allowlisted"):
        await job_event_writer.append(
            db,
            job_id="events000000000000000000000001",
            event_key="job.queued",
            state="queued",
            detail={"authorization": "Bearer example"},
        )
    with pytest.raises(JobEventContractError, match="credential-shaped"):
        await job_event_writer.append(
            db,
            job_id="events000000000000000000000001",
            event_key="job.queued",
            state="queued",
            message="database failed at postgresql://user:password@db/marquee",
        )
    assert await db.scalar(select(func.count(JobEvent.id))) == 0


async def test_replay_is_strictly_after_cursor_and_invalid_cursor_resets(db) -> None:
    db.add(_job())
    await db.commit()
    first = await _append(db, message="first")
    second = await _append(db, message="second")
    await db.commit()
    tailer = JobEventTailer()

    replay = await tailer.subscribe(first.id)
    frame = replay.queue.get_nowait()
    assert frame.cursor == second.id
    assert frame.canonical_version == 3
    assert replay.queue.empty()
    await tailer.unsubscribe(replay)

    invalid = await tailer.subscribe(None, invalid_cursor=True)
    reset = invalid.queue.get_nowait()
    assert reset.event_key == "stream.reset_required"
    assert reset.cursor == second.id
    assert invalid.closed
    await tailer.unsubscribe(invalid)


async def test_listener_notification_tails_committed_range_once(db, monkeypatch) -> None:
    db.add(_job())
    await db.commit()
    schema = await db.scalar(text("SELECT current_schema()"))
    await db.rollback()
    dsn = settings.db_url_resolved.replace("postgresql+asyncpg://", "postgresql://", 1)
    tailer = JobEventTailer()

    async def connect(*, initialize_cursor: bool) -> None:
        connection = await asyncpg.connect(dsn, server_settings={"search_path": schema})
        await connection.add_listener(JOB_EVENT_CHANNEL, tailer._notify)
        if initialize_cursor:
            tailer._cursor = int(
                await connection.fetchval("SELECT COALESCE(MAX(id), 0) FROM job_events")
            )
        tailer._connection = connection

    monkeypatch.setattr(tailer, "_connect", connect)
    await tailer.start()
    client = await tailer.subscribe(None)
    try:
        event = await _append(db)
        await db.commit()
        frame = await asyncio.wait_for(client.queue.get(), timeout=2)
        assert frame.cursor == event.id
        assert frame.event_key == "job.queued"
        tailer._wake.set()
        await asyncio.sleep(0.05)
        assert client.queue.empty()
    finally:
        await tailer.unsubscribe(client)
        await tailer.stop()


async def test_retention_gap_requires_snapshot_reconciliation(db) -> None:
    db.add(_job())
    await db.commit()
    first = await _append(db, message="first")
    second = await _append(db, message="second")
    third = await _append(db, message="third")
    await db.commit()
    await db.execute(delete(JobEvent).where(JobEvent.id <= second.id))
    await db.commit()

    client = await JobEventTailer().subscribe(first.id)
    reset = client.queue.get_nowait()
    assert reset.event_key == "stream.reset_required"
    assert reset.cursor == third.id
    assert client.closed


async def test_slow_client_overflow_isolated_from_other_clients() -> None:
    tailer = JobEventTailer()
    slow = EventClient(after=1, queue=asyncio.Queue(maxsize=1))
    fast = EventClient(after=1, queue=asyncio.Queue(maxsize=2))
    slow.queue.put_nowait(
        JobEventFrame(
            cursor=1,
            event_key="job.queued",
            job_id="job-1",
            canonical_version=0,
            delta=JobEventDelta(state="queued"),
            reconciliation=None,
        )
    )
    tailer._clients.update({slow, fast})
    frame = JobEventFrame(
        cursor=2,
        event_key="job.succeeded",
        job_id="job-1",
        canonical_version=1,
        delta=JobEventDelta(state="succeeded"),
        reconciliation=None,
    )

    await tailer._fanout(frame)

    assert slow.queue.get_nowait().event_key == "stream.reset_required"
    assert slow.closed
    assert fast.queue.get_nowait() == frame
    assert not fast.closed
    assert tailer.health()["client_overflows"] == 1


async def test_start_is_idempotent_and_allocates_one_listener(monkeypatch) -> None:
    tailer = JobEventTailer()
    calls = 0

    async def connect(*, initialize_cursor: bool) -> None:
        nonlocal calls
        assert initialize_cursor
        calls += 1

    async def run() -> None:
        await asyncio.Future()

    monkeypatch.setattr(tailer, "_connect", connect)
    monkeypatch.setattr(tailer, "_run", run)
    await tailer.start()
    await tailer.start()
    assert calls == 1
    await tailer.stop()


def test_public_frame_does_not_duplicate_result_or_internal_metadata() -> None:
    event = JobEvent(
        id=9,
        job_id="job-1",
        event_key="job.succeeded",
        state="succeeded",
        message="done",
        detail={"_canonical_version": 4, "result": {"large": "document"}},
        created_at=NOW,
    )
    frame = frame_from_event(event)
    assert frame.canonical_version == 4
    assert frame.delta.detail == {}
    assert frame.reconciliation.snapshot_url == "/api/jobs/job-1/snapshot"


async def test_public_sse_invalid_cursor_emits_reset_and_closes(db, monkeypatch) -> None:
    from marquee.api.routes import jobs as jobs_route

    db.add(_job())
    await db.commit()
    await _append(db)
    await db.commit()
    monkeypatch.setattr(jobs_route.job_event_tailer, "health", lambda: {"status": "ok"})
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/api/jobs/events/stream", headers={"Last-Event-ID": "not-a-cursor"}
        )
    assert response.status_code == 200
    assert "event: stream.reset_required" in response.text
    assert response.text.count("data:") == 1


async def test_command_event_carries_the_committed_canonical_version(db) -> None:
    job = _job()
    db.add(job)
    await db.commit()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        changed = await client.post(
            f"/api/jobs/{job.id}/priority",
            json={"expected_fence_token": 3, "priority": 70},
        )
        events = await client.get(f"/api/jobs/{job.id}/events")
    assert changed.status_code == 200
    assert changed.json()["snapshot"]["fence_token"] == 4
    assert events.status_code == 200
    assert events.json()["items"][0]["canonical_version"] == 4
    assert events.json()["items"][0]["detail"]["priority"] == 70


async def test_public_sse_rejects_disagreeing_header_and_query() -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/api/jobs/events/stream?after=4", headers={"Last-Event-ID": "3"}
        )
    assert response.status_code == 400


def test_literal_event_stream_route_precedes_dynamic_job_routes() -> None:
    paths = [route.path for route in app.routes if hasattr(route, "path")]
    stream_index = paths.index("/api/jobs/events/stream")
    assert stream_index < paths.index("/api/jobs/{job_id}/events")
    assert paths.count("/api/jobs/events/stream") == 1
