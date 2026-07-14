from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event

from marquee.database import _get_engine
from marquee.main import app
from marquee.models import Job

NOW = datetime(2026, 7, 13, 12, 0, tzinfo=UTC)
SYSTEM_SUBJECT = {
    "version": 1,
    "kind": "system_work",
    "display_id": "system:noop",
    "display_name": "System no-op",
    "snapshot_at": NOW.isoformat(),
    "work": "noop",
}


@contextmanager
def count_queries() -> Iterator[list[int]]:
    counts = [0]

    def before_cursor_execute(*_args):
        counts[0] += 1

    engine = _get_engine().sync_engine
    event.listen(engine, "before_cursor_execute", before_cursor_execute)
    try:
        yield counts
    finally:
        event.remove(engine, "before_cursor_execute", before_cursor_execute)


def make_job(*, job_id: str, phase: str, offset: int = 0, outcome: str | None = None) -> Job:
    created = NOW + timedelta(seconds=offset)
    return Job(
        id=job_id,
        type="system_noop",
        request={"echo": {"offset": offset}},
        phase=phase,
        outcome=outcome,
        desired_state="run",
        priority=50,
        eligible_at=created,
        dispatch_generation=1,
        root_id=job_id,
        trigger_kind="system",
        feature_area="system",
        presentation_family="system",
        subject_kind="system_work",
        subject_reference="system:noop",
        subject_snapshot=SYSTEM_SUBJECT,
        progress_sequence=0,
        created_at=created,
        queued_at=created if phase != "planned" else None,
        terminal_at=created if phase == "terminal" else None,
    )


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as value:
        yield value


@pytest.mark.asyncio
async def test_queue_history_are_partitioned_and_cursor_bound(db, client):
    db.add_all(
        [
            make_job(job_id="queue000000000000000000000000001", phase="queued"),
            make_job(
                job_id="history000000000000000000000001",
                phase="terminal",
                outcome="succeeded",
                offset=1,
            ),
            make_job(job_id="queue000000000000000000000000002", phase="queued", offset=2),
        ]
    )
    await db.commit()

    first = await client.get("/api/jobs", params={"view": "queue", "limit": 1})
    assert first.status_code == 200
    body = first.json()
    assert body["view"] == "queue"
    assert len(body["items"]) == 1
    assert body["next_cursor"]

    second = await client.get(
        "/api/jobs",
        params={"view": "queue", "limit": 1, "cursor": body["next_cursor"]},
    )
    assert second.status_code == 200
    assert second.json()["items"][0]["job_id"] != body["items"][0]["job_id"]

    rebound = await client.get(
        "/api/jobs",
        params={"view": "history", "limit": 1, "cursor": body["next_cursor"]},
    )
    assert rebound.status_code == 422
    assert rebound.json()["detail"]["code"] == "invalid_cursor"

    history = await client.get("/api/jobs", params={"view": "history"})
    assert [item["job_id"] for item in history.json()["items"]] == [
        "history000000000000000000000001"
    ]


@pytest.mark.asyncio
async def test_snapshot_presentation_and_raw_documents_are_bounded(db, client):
    job = make_job(job_id="snapshot00000000000000000000001", phase="queued")
    db.add(job)
    await db.commit()

    snapshot = await client.get(f"/api/jobs/{job.id}/snapshot")
    assert snapshot.status_code == 200
    assert snapshot.json()["version"] == 1
    assert "request" not in snapshot.json()
    assert "pgq_job_id" not in snapshot.json()

    presentation = await client.get(f"/api/jobs/{job.id}/presentation")
    assert presentation.status_code == 200
    assert presentation.json()["presenter_key"] == "jobs.system_noop"

    raw = await client.get(f"/api/jobs/{job.id}/raw/request", params={"download": True})
    assert raw.status_code == 200
    assert raw.json()["document"] == {"echo": {"offset": 0}}
    assert raw.headers["content-disposition"].startswith("attachment;")


@pytest.mark.asyncio
async def test_superseded_media_job_and_polling_sse_routes_are_absent(client):
    assert (await client.get("/api/media-jobs/example")).status_code == 404
    response = await client.get("/api/jobs/example/events")
    assert response.status_code == 404
    assert response.headers.get("content-type", "").startswith("application/json")


@pytest.mark.asyncio
async def test_read_query_budgets_hold_at_maximum_page_size(db, client):
    jobs = [
        make_job(job_id=f"budget{i:026d}", phase="queued", offset=i)
        for i in range(201)
    ]
    db.add_all(jobs)
    await db.commit()

    with count_queries() as list_queries:
        response = await client.get("/api/jobs", params={"view": "queue", "limit": 200})
    assert response.status_code == 200
    assert len(response.json()["items"]) == 200
    assert list_queries[0] == 1

    job_id = jobs[0].id
    with count_queries() as presentation_queries:
        response = await client.get(f"/api/jobs/{job_id}/presentation")
    assert response.status_code == 200
    assert presentation_queries[0] == 1

    with count_queries() as children_queries:
        response = await client.get(f"/api/jobs/{job_id}/children", params={"limit": 200})
    assert response.status_code == 200
    assert children_queries[0] == 2
