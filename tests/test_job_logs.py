"""Job logs: bounded, redacted, attempt-owned capture.

Handler output is captured per attempt and sealed. Covers the redaction matrix, capture
across every source including cross-chunk secrets, the single truncation record emitted at
the cap without ever blocking the producer, scoped Python capture that cannot
cross-contaminate, the list/stream/download contract, and recovery of a finished attempt's
log when its process is already gone."""

from __future__ import annotations

import asyncio
import gzip
import json
import logging
import os
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from marquee.config import settings
from marquee.core.jobs.log_capture import (
    AttemptLogFiles,
    AttemptLogSink,
    CentralRedactor,
    recover_abandoned_attempt_logs,
)
from marquee.main import app
from marquee.models import Job, JobAttempt, JobEvent, JobLog


def _job(job_id: str) -> Job:
    job_id = uuid.uuid5(uuid.NAMESPACE_URL, f"marquee-test:{job_id}").hex
    return Job(
        id=job_id,
        type="system_noop",
        payload_version=1,
        request={},
        phase="running",
        root_id=job_id,
        subject_kind="system_work",
        subject_reference="system:noop",
        subject_snapshot={"version": 1, "kind": "system_work", "title": "No-op"},
        fence_token=1,
    )


async def _attempt(db, job_id: str, *, worker_node: str | None = None):
    job = _job(job_id)
    db.add(job)
    await db.flush()
    attempt = JobAttempt(
        job_id=job.id,
        number=1,
        fence_token=1,
        phase="running",
        worker_node_id=worker_node,
    )
    db.add(attempt)
    await db.flush()
    job.current_attempt_id = attempt.id
    await db.commit()
    return job, attempt


def test_contract_redaction_matrix() -> None:
    fixture = json.loads(
        (Path(__file__).parent / "fixtures/job_logs/redaction_cases.json").read_text()
    )
    redactor = CentralRedactor(fixture["configured_secrets"])

    for case in fixture["text_cases"]:
        assert redactor.redact_text("".join(case["chunks"]))[0] == case["expected"], case["name"]
    for case in fixture["argument_cases"]:
        assert list(redactor.redact_arguments(case["arguments"])) == case["expected"], case["name"]


async def test_sink_captures_all_sources_redacts_cross_chunk_and_seals(db) -> None:
    job, attempt = await _attempt(db, "log-capture")
    job_id = job.id
    attempt_id = attempt.id
    secret = "callback-secret"
    sink = await AttemptLogSink.create(
        job_id=job.id,
        attempt_id=attempt.id,
        fence_token=1,
        data_dir=settings.DATA_DIR,
        redactor=CentralRedactor([secret]),
    )

    await sink.feed_pipe("stdout", b"callback_token=callback-")
    await sink.feed_pipe("stdout", b"secret done\n")
    await sink.feed_pipe("stderr", b"warning text\n", eof=True)
    await sink.write(source="system", message="system checkpoint", stage="execute")
    test_logger = logging.getLogger("marquee.tests.logcapture")
    async with sink.capture_python_logs():
        test_logger.warning("python secret=%s", secret)
    assert await asyncio.wait_for(sink.seal(), timeout=5)

    db.expire_all()
    row = (await db.scalars(select(JobLog).where(JobLog.attempt_id == attempt_id))).one()
    assert row.seal_status == "sealed"
    assert row.compression == "gzip"
    assert row.checksum and len(row.checksum) == 64
    assert row.line_count == 4
    assert row.last_cursor == 4
    assert row.stored_byte_count > 0
    assert row.expires_at - row.closed_at == timedelta(days=30)

    files = AttemptLogFiles.for_data_dir(settings.DATA_DIR)
    lines, next_cursor, compressed = files.read_lines(row, after=0, limit=20)
    assert compressed is True
    assert next_cursor is None
    assert [line.source for line in lines] == ["stdout", "stderr", "system", "python"]
    messages = "\n".join(line.message for line in lines)
    assert secret not in messages
    assert "callback_token=[REDACTED] done" in messages
    assert "python secret=[REDACTED]" in messages

    physical = files.existing(row.storage_key).path
    fd = files.boundary.open_read(physical)
    with os.fdopen(fd, "rb") as raw, gzip.open(raw, "rt", encoding="utf-8") as stream:
        stored = stream.read()
    assert secret not in stored
    assert [line.cursor for line in lines] == [1, 2, 3, 4]
    event = (
        await db.scalars(
            select(JobEvent).where(JobEvent.job_id == job_id, JobEvent.event_key == "log.available")
        )
    ).one()
    assert event.attempt_id == attempt_id


async def test_cap_emits_one_truncation_record_and_never_blocks_producer(db) -> None:
    job, attempt = await _attempt(db, "log-cap")
    attempt_id = attempt.id
    sink = await AttemptLogSink.create(
        job_id=job.id,
        attempt_id=attempt.id,
        fence_token=1,
        data_dir=settings.DATA_DIR,
        redactor=CentralRedactor([]),
        cap_bytes=4096,
    )

    async def produce() -> None:
        for index in range(500):
            await sink.feed_pipe("stdout", f"line-{index}-{'x' * 300}\n".encode())
        await sink.feed_pipe("stdout", b"post-cap sentinel\n", eof=True)

    await asyncio.wait_for(produce(), timeout=5)
    assert await asyncio.wait_for(sink.seal(), timeout=5)
    db.expire_all()
    row = (await db.scalars(select(JobLog).where(JobLog.attempt_id == attempt_id))).one()
    lines, _, _ = AttemptLogFiles.for_data_dir(settings.DATA_DIR).read_lines(
        row, after=0, limit=500
    )
    truncations = [line for line in lines if line.fields.get("truncated") is True]
    assert row.truncated is True
    assert row.byte_count <= 4096
    assert len(truncations) == 1
    assert "post-cap sentinel" not in "\n".join(line.message for line in lines)


async def test_python_capture_is_scoped_and_does_not_cross_contaminate(db) -> None:
    job_a, attempt_a = await _attempt(db, "log-context-a")
    job_b, attempt_b = await _attempt(db, "log-context-b")
    job_a_id = job_a.id
    job_b_id = job_b.id
    sink_a = await AttemptLogSink.create(
        job_id=job_a.id,
        attempt_id=attempt_a.id,
        fence_token=1,
        data_dir=settings.DATA_DIR,
    )
    sink_b = await AttemptLogSink.create(
        job_id=job_b.id,
        attempt_id=attempt_b.id,
        fence_token=1,
        data_dir=settings.DATA_DIR,
    )
    scoped_logger = logging.getLogger("marquee.tests.logcapture.scoped")
    scoped_logger.warning("outside")

    async def emit(sink: AttemptLogSink, marker: str) -> None:
        async with sink.capture_python_logs():
            scoped_logger.warning(marker)
            await asyncio.sleep(0)

    await asyncio.gather(emit(sink_a, "only-a"), emit(sink_b, "only-b"))
    assert await sink_a.seal()
    assert await sink_b.seal()
    db.expire_all()
    rows = (await db.scalars(select(JobLog).order_by(JobLog.id))).all()
    files = AttemptLogFiles.for_data_dir(settings.DATA_DIR)
    messages = {
        row.job_id: "\n".join(line.message for line in files.read_lines(row, after=0, limit=20)[0])
        for row in rows
    }
    assert "only-a" in messages[job_a_id] and "only-b" not in messages[job_a_id]
    assert "only-b" in messages[job_b_id] and "only-a" not in messages[job_b_id]
    assert "outside" not in messages[job_a_id] + messages[job_b_id]


async def test_log_list_stream_and_download_contract(db) -> None:
    job, attempt = await _attempt(db, "log-api")
    sink = await AttemptLogSink.create(
        job_id=job.id,
        attempt_id=attempt.id,
        fence_token=1,
        data_dir=settings.DATA_DIR,
    )
    await sink.write(source="system", message="first")
    await sink.write(source="system", message="second", level="warning")
    assert await sink.seal()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        page = await client.get(
            f"/api/jobs/{job.id}/attempts/{attempt.id}/logs", params={"limit": 1}
        )
        assert page.status_code == 200
        assert page.json()["items"][0]["message"] == "first"
        assert page.json()["next_cursor"] == 1
        filtered = await client.get(
            f"/api/jobs/{job.id}/attempts/{attempt.id}/logs",
            params={"after": 1, "level": "warning"},
        )
        assert [item["message"] for item in filtered.json()["items"]] == ["second"]
        stream = await client.get(f"/api/jobs/{job.id}/attempts/{attempt.id}/logs/stream")
        assert stream.status_code == 200
        assert stream.text.count("event: log.line") == 2
        assert "event: log.sealed" in stream.text
        download = await client.get(f"/api/jobs/{job.id}/attempts/{attempt.id}/logs/download")
        assert download.status_code == 200
        assert download.headers["content-type"].startswith("application/gzip")
        assert download.headers["x-content-type-options"] == "nosniff"
        assert gzip.decompress(download.content).count(b"\n") == 2
        assert (await client.get(f"/api/jobs/other/attempts/{attempt.id}/logs")).status_code == 404


async def test_finished_processless_attempt_log_is_recovered(db) -> None:
    job, attempt = await _attempt(db, "log-recovery", worker_node="worker:test")
    attempt_id = attempt.id
    sink = await AttemptLogSink.create(
        job_id=job.id,
        attempt_id=attempt.id,
        fence_token=1,
        data_dir=settings.DATA_DIR,
    )
    await sink.write(source="system", message="recover me")
    await asyncio.sleep(0)
    attempt.phase = "finished"
    attempt.finished_at = datetime.now(UTC)
    await db.commit()
    # Model a worker crash after durable writes: stop the writer and close the descriptor.
    sink._accepting = False
    await sink._queue.put(None)
    await sink._task
    if isinstance(sink._fd, int) and sink._fd >= 0:
        os.close(sink._fd)
        sink._fd = -1

    counts = await recover_abandoned_attempt_logs(
        worker_node="worker:test", data_dir=settings.DATA_DIR
    )
    assert counts == {"recovered": 1, "deferred": 0, "failed": 0}
    db.expire_all()
    row = (await db.scalars(select(JobLog).where(JobLog.attempt_id == attempt_id))).one()
    assert row.seal_status == "sealed"
    assert row.compression == "gzip"
