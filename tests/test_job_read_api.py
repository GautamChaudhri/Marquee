from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event

from marquee.database import _get_engine
from marquee.main import app
from marquee.models import Job, JobAttempt

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
async def test_queue_rank_is_class_local_and_does_not_restart_on_next_page(db, client):
    jobs = [make_job(job_id=f"rank{i:028d}", phase="queued", offset=i) for i in range(25)]
    db.add_all(jobs)
    await db.commit()

    first = await client.get("/api/jobs", params={"view": "queue", "limit": 10})
    assert first.status_code == 200
    second = await client.get(
        "/api/jobs",
        params={
            "view": "queue",
            "limit": 10,
            "cursor": first.json()["next_cursor"],
        },
    )

    assert second.status_code == 200
    assert [item["queue_rank"] for item in first.json()["items"]] == list(range(1, 11))
    assert [item["queue_rank"] for item in second.json()["items"]] == list(range(11, 21))


@pytest.mark.asyncio
async def test_queue_rank_is_omitted_beyond_bounded_class_window(db, client):
    ahead = [make_job(job_id=f"bound{i:027d}", phase="queued", offset=i) for i in range(1001)]
    target = make_job(
        job_id="boundedranktarget000000000000001",
        phase="queued",
        offset=1001,
    )
    target.subject_reference = "system:bounded-rank-target"
    db.add_all([*ahead, target])
    await db.commit()

    response = await client.get(
        "/api/jobs",
        params={
            "view": "queue",
            "subject_reference": target.subject_reference,
        },
    )

    assert response.status_code == 200
    assert [item["job_id"] for item in response.json()["items"]] == [target.id]
    assert response.json()["items"][0]["queue_rank"] is None


@pytest.mark.asyncio
async def test_activity_attention_is_one_bounded_server_aggregate(db, client):
    running = make_job(job_id="attention-running-000000000001", phase="running")
    warning = make_job(job_id="attention-warning-000000000001", phase="running", offset=1)
    warning.attention = {"level": "warning", "reason": "slow"}
    waiting = make_job(job_id="attention-waiting-000000000001", phase="queued", offset=2)
    error = make_job(job_id="attention-error-0000000000001", phase="queued", offset=3)
    error.attention = {"level": "error", "reason": "blocked"}
    retrying = make_job(job_id="attention-retrying-00000000001", phase="queued", offset=4)
    retrying.progress = {"wait": {"kind": "retry"}}
    db.add_all([running, warning, waiting, error, retrying])
    await db.commit()

    with count_queries() as queries:
        response = await client.get("/api/jobs/attention")

    assert response.status_code == 200
    assert response.json() == {
        "running": 2,
        "waiting_held": 2,
        "retrying": 1,
        "needs_attention": 2,
        "warning": 1,
        "error": 1,
        "highest_severity": "error",
    }
    assert queries[0] == 1


@pytest.mark.asyncio
async def test_queue_filters_by_execution_class_and_worker(db, client):
    matching = make_job(job_id="workerfilter00000000000000000001", phase="running")
    matching.execution_policy_id = "gpu"
    other = make_job(job_id="otherfilter000000000000000000001", phase="running", offset=1)
    other.execution_policy_id = "cpu"
    db.add_all([matching, other])
    await db.flush()
    db.add(
        JobAttempt(
            job_id=matching.id,
            number=1,
            fence_token=1,
            worker_node_id="worker-a",
            phase="running",
        )
    )
    await db.commit()

    response = await client.get(
        "/api/jobs",
        params={"view": "queue", "execution_class": "gpu", "worker_id": "worker-a"},
    )

    assert response.status_code == 200
    assert [item["job_id"] for item in response.json()["items"]] == [matching.id]


@pytest.mark.asyncio
async def test_queue_exact_scope_recovers_beyond_first_page(db, client) -> None:
    unrelated = [
        make_job(
            job_id=f"unrelated{i:023d}",
            phase="queued",
            offset=i,
        )
        for i in range(25)
    ]
    for index, job in enumerate(unrelated):
        job.subject_reference = f"media-file:{index}"
    matching = make_job(
        job_id="exactscope0000000000000000000001",
        phase="queued",
        offset=100,
    )
    matching.subject_reference = "media-file:target"
    db.add_all([*unrelated, matching])
    await db.commit()

    response = await client.get(
        "/api/jobs",
        params=[
            ("view", "queue"),
            ("limit", "20"),
            ("types", "system_noop"),
            ("subject_reference", "media-file:target"),
        ],
    )

    assert response.status_code == 200
    assert [item["job_id"] for item in response.json()["items"]] == [matching.id]
    assert response.json()["next_cursor"] is None

    # Multi-valued scope filters are repeated params, never one comma-joined value —
    # a joined list reads as a single unknown job type and rejects the whole request.
    repeated = await client.get(
        "/api/jobs",
        params=[
            ("view", "queue"),
            ("limit", "20"),
            ("types", "system_noop"),
            ("types", "poster_reset"),
            ("subject_reference", "media-file:target"),
            ("subject_reference", "media-file:1"),
        ],
    )
    assert repeated.status_code == 200
    assert {item["job_id"] for item in repeated.json()["items"]} == {
        matching.id,
        unrelated[1].id,
    }

    joined = await client.get(
        "/api/jobs",
        params=[("view", "queue"), ("types", "system_noop,poster_reset")],
    )
    assert joined.status_code == 422


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
    jobs = [make_job(job_id=f"budget{i:026d}", phase="queued", offset=i) for i in range(201)]
    db.add_all(jobs)
    await db.commit()

    with count_queries() as list_queries:
        response = await client.get("/api/jobs", params={"view": "queue", "limit": 200})
    assert response.status_code == 200
    assert len(response.json()["items"]) == 200
    assert list_queries[0] == 2

    job_id = jobs[0].id
    with count_queries() as presentation_queries:
        response = await client.get(f"/api/jobs/{job_id}/presentation")
    assert response.status_code == 200
    assert presentation_queries[0] == 1

    with count_queries() as children_queries:
        response = await client.get(f"/api/jobs/{job_id}/children", params={"limit": 200})
    assert response.status_code == 200
    assert children_queries[0] == 2


@pytest.mark.asyncio
async def test_batch_children_are_failed_first_with_stable_cursors_beyond_100(db, client):
    parent = make_job(job_id="parent00000000000000000000000001", phase="running")
    children = []
    for index in range(105):
        outcome = "failed" if index in {80, 104} else "succeeded"
        child = make_job(
            job_id=f"child{index:027d}",
            phase="terminal",
            outcome=outcome,
            offset=index,
        )
        child.parent_id = parent.id
        child.root_id = parent.id
        children.append(child)
    db.add_all([parent, *children])
    await db.commit()

    first = await client.get(
        f"/api/jobs/{parent.id}/children",
        params={"limit": 100, "sort": "failed_first"},
    )
    assert first.status_code == 200
    first_body = first.json()
    assert len(first_body["items"]) == 100
    assert [item["status"]["outcome"] for item in first_body["items"][:2]] == [
        "failed",
        "failed",
    ]
    assert first_body["next_cursor"]

    second = await client.get(
        f"/api/jobs/{parent.id}/children",
        params={
            "limit": 100,
            "sort": "failed_first",
            "cursor": first_body["next_cursor"],
        },
    )
    assert second.status_code == 200
    assert len(second.json()["items"]) == 5
    ids = [item["job_id"] for item in first_body["items"] + second.json()["items"]]
    assert len(ids) == len(set(ids)) == 105
