"""The job routes against the test database."""

from __future__ import annotations

import psycopg
import pytest
from httpx import AsyncClient
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine

from marquee.db.engine import create_session_factory
from marquee.jobs import batch, queue
from marquee.jobs.registry import JobContext, JobKind
from marquee.jobs.store import WORKER_LOCK_KEY


async def _noop(ctx: JobContext) -> None:
    return None


NET = JobKind("fake_net", "network", handler=_noop)
OTHER = JobKind("fake_other", "network", handler=_noop)

ROUTES = [
    ("GET", "/api/jobs"),
    ("GET", "/api/jobs/events"),
    ("GET", "/api/jobs/1"),
    ("GET", "/api/jobs/1/events"),
    ("POST", "/api/jobs/1/cancel"),
    ("POST", "/api/jobs/1/retry"),
    ("POST", "/api/sync"),
    ("GET", "/api/worker"),
]


@pytest.fixture
def headers(api_key: str) -> dict[str, str]:
    return {"X-Api-Key": api_key}


async def _enqueue(db_engine: AsyncEngine, kind: JobKind = NET, **payload: object) -> int:
    async with create_session_factory(db_engine)() as session:
        job, _ = await queue.enqueue(session, kind, dict(payload))
    return job.id


@pytest.mark.parametrize(("method", "path"), ROUTES)
async def test_every_job_route_needs_the_key(
    offline_client: AsyncClient, method: str, path: str
) -> None:
    response = await offline_client.request(method, path)
    assert response.status_code == 401
    wrong = await offline_client.request(method, path, headers={"X-Api-Key": "wrong"})
    assert wrong.status_code == 401


async def test_no_route_queues_an_arbitrary_kind(
    offline_client: AsyncClient, headers: dict[str, str]
) -> None:
    response = await offline_client.post("/api/jobs", headers=headers, json={"kind": "x"})
    assert response.status_code == 405


@pytest.mark.db
async def test_the_list_is_newest_first_with_filters_and_paging(
    client: AsyncClient, headers: dict[str, str], db_engine: AsyncEngine
) -> None:
    first = await _enqueue(db_engine, title="one")
    second = await _enqueue(db_engine, OTHER)
    third = await _enqueue(db_engine)
    async with create_session_factory(db_engine)() as session:
        await queue.request_cancel(session, first)

    body = (await client.get("/api/jobs", headers=headers)).json()
    assert body["total"] == 3
    assert [job["id"] for job in body["items"]] == [third, second, first]
    one = body["items"][2]
    assert (one["kind"], one["state"], one["payload"], one["job_class"]) == (
        "fake_net",
        "cancelled",
        {"title": "one"},
        "network",
    )

    cancelled = (await client.get("/api/jobs?state=cancelled", headers=headers)).json()
    assert [job["id"] for job in cancelled["items"]] == [first]
    other = (await client.get("/api/jobs?kind=fake_other", headers=headers)).json()
    assert [job["id"] for job in other["items"]] == [second]
    paged = (await client.get("/api/jobs?page=2&page_size=2", headers=headers)).json()
    assert (paged["total"], [job["id"] for job in paged["items"]]) == (3, [first])
    assert (await client.get("/api/jobs?state=lost", headers=headers)).status_code == 422


@pytest.mark.db
async def test_a_batch_lists_its_children(
    client: AsyncClient, headers: dict[str, str], db_engine: AsyncEngine
) -> None:
    async with create_session_factory(db_engine)() as session:
        parent, children = await batch.enqueue_batch(session, [(NET, {}), (NET, {})])

    body = (await client.get(f"/api/jobs?parent_id={parent.id}", headers=headers)).json()

    assert sorted(job["id"] for job in body["items"]) == sorted(c.id for c in children)
    parent_item = (await client.get(f"/api/jobs/{parent.id}", headers=headers)).json()
    assert (parent_item["kind"], parent_item["job_class"]) == ("batch", "batch")


@pytest.mark.db
async def test_one_job_and_its_events(
    client: AsyncClient, headers: dict[str, str], db_engine: AsyncEngine
) -> None:
    job_id = await _enqueue(db_engine)

    job = (await client.get(f"/api/jobs/{job_id}", headers=headers)).json()
    events = (await client.get(f"/api/jobs/{job_id}/events", headers=headers)).json()

    assert (job["id"], job["state"], job["attempt"], job["max_attempts"]) == (
        job_id,
        "queued",
        0,
        1,
    )
    assert [e["type"] for e in events["items"]] == ["queued"]
    assert events["items"][0]["job_id"] == job_id
    assert (await client.get("/api/jobs/999999", headers=headers)).status_code == 404
    assert (await client.get("/api/jobs/999999/events", headers=headers)).status_code == 404


@pytest.mark.db
async def test_the_event_feed_follows_a_cursor(
    client: AsyncClient, headers: dict[str, str], db_engine: AsyncEngine
) -> None:
    first = await _enqueue(db_engine)
    second = await _enqueue(db_engine)

    page = (await client.get("/api/jobs/events?limit=1", headers=headers)).json()
    assert [(e["job_id"], e["type"]) for e in page["items"]] == [(first, "queued")]
    cursor = page["next_after"]
    assert cursor == page["items"][0]["id"]

    rest = (await client.get(f"/api/jobs/events?after={cursor}", headers=headers)).json()
    assert [(e["job_id"], e["type"]) for e in rest["items"]] == [(second, "queued")]

    async with create_session_factory(db_engine)() as session:
        await queue.request_cancel(session, first)
    latest = rest["next_after"]
    newer = (await client.get(f"/api/jobs/events?after={latest}", headers=headers)).json()
    assert [(e["job_id"], e["type"]) for e in newer["items"]] == [(first, "cancelled")]

    idle = (
        await client.get(f"/api/jobs/events?after={newer['next_after']}", headers=headers)
    ).json()
    assert idle == {"items": [], "next_after": newer["next_after"]}


@pytest.mark.db
async def test_cancel_and_retry_answer_409_where_they_should(
    client: AsyncClient, headers: dict[str, str], db_engine: AsyncEngine
) -> None:
    job_id = await _enqueue(db_engine)

    retry_queued = await client.post(f"/api/jobs/{job_id}/retry", headers=headers)
    assert retry_queued.status_code == 409
    assert "only a failed or cancelled job" in retry_queued.json()["detail"]

    cancelled = await client.post(f"/api/jobs/{job_id}/cancel", headers=headers)
    assert (cancelled.status_code, cancelled.json()["state"]) == (200, "cancelled")
    again = await client.post(f"/api/jobs/{job_id}/cancel", headers=headers)
    assert again.status_code == 409

    retried = await client.post(f"/api/jobs/{job_id}/retry", headers=headers)
    assert (retried.status_code, retried.json()["state"]) == (200, "queued")
    assert (await client.post("/api/jobs/999999/cancel", headers=headers)).status_code == 404
    assert (await client.post("/api/jobs/999999/retry", headers=headers)).status_code == 404


@pytest.mark.db
async def test_cancelling_a_running_job_asks_it_to_stop(
    client: AsyncClient, headers: dict[str, str], db_engine: AsyncEngine
) -> None:
    job_id = await _enqueue(db_engine)
    async with create_session_factory(db_engine)() as session:
        await queue.claim(session, ["network"])

    asked = (await client.post(f"/api/jobs/{job_id}/cancel", headers=headers)).json()

    assert (asked["state"], asked["cancel_requested"]) == ("running", True)


@pytest.mark.db
async def test_sync_queues_one_job_at_a_time(client: AsyncClient, headers: dict[str, str]) -> None:
    first = await client.post("/api/sync", headers=headers)
    second = await client.post("/api/sync", headers=headers)

    assert first.status_code == 202
    assert (first.json()["kind"], first.json()["state"]) == ("library_sync", "queued")
    assert first.json()["max_attempts"] == 3
    assert second.status_code == 200
    assert second.json()["id"] == first.json()["id"]


@pytest.mark.db
async def test_the_worker_route_reads_the_lock(
    client: AsyncClient, headers: dict[str, str], database_url: str, db_engine: AsyncEngine
) -> None:
    await _enqueue(db_engine)

    idle = (await client.get("/api/worker", headers=headers)).json()
    assert idle == {"running": False, "since": None, "queued": 1, "running_jobs": 0, "failed": 0}

    libpq = make_url(database_url).set(drivername="postgresql")
    async with await psycopg.AsyncConnection.connect(
        libpq.render_as_string(hide_password=False), autocommit=True
    ) as holder:
        await holder.execute("SELECT pg_advisory_lock(%s)", [WORKER_LOCK_KEY])
        busy = (await client.get("/api/worker", headers=headers)).json()
    assert busy["running"] is True and busy["since"] is not None
