import importlib
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event

from marquee.database import _get_engine
from marquee.main import app
from marquee.models import Job, JobAttempt, JobWorkItem

jobs_route = importlib.import_module("marquee.api.routes.jobs")

NOW = datetime(2026, 7, 13, 12, 0, tzinfo=UTC)
SYSTEM_SUBJECT = {
    "version": 1,
    "kind": "system_work",
    "display_id": "system:noop",
    "display_name": "System no-op",
    "snapshot_at": NOW.isoformat(),
    "work": "noop",
}
MOVIE_SUBJECT = {
    "version": 1,
    "kind": "movie",
    "display_id": "movie:1",
    "display_name": "Alpha Movie",
    "snapshot_at": NOW.isoformat(),
    "movie_id": 1,
    "title": "Alpha Movie",
    "media_kind": "movie",
}


def poster_parent(*, job_id: str, legacy: bool = False) -> Job:
    job = make_job(job_id=job_id, phase="queued")
    job.type = "poster_pipeline_batch"
    job.request = {
        "scope": "missing",
        "selection_count": 1,
        "grouping_mode": "individual" if legacy else "all_at_once",
    }
    job.feature_area = "ai_posters"
    job.presentation_family = "ai_posters"
    job.subject_kind = "aggregate_batch"
    job.subject_reference = job_id
    job.subject_snapshot = {
        "version": 1,
        "kind": "aggregate_batch",
        "display_id": f"batch:{job_id}",
        "display_name": "Poster analysis · movies",
        "snapshot_at": NOW.isoformat(),
        "batch_type": "poster_pipeline_batch",
        "child_count": 1,
        "sealed": True,
        "scope_summary": "Missing movie posters",
    }
    return job


def poster_group(*, job_id: str, parent_id: str, mode: str = "all_at_once") -> Job:
    job = make_job(job_id=job_id, phase="queued", offset=1)
    job.type = "poster_pipeline_group"
    job.parent_id = parent_id
    job.root_id = parent_id
    job.request = {
        "library": "movies",
        "chunk_index": 0,
        "batch_mode": mode,
        "members": [{"movie_id": 1, "title": "Alpha Movie"}],
    }
    job.feature_area = "ai_posters"
    job.presentation_family = "ai_posters"
    job.subject_kind = "poster_subject_group"
    job.subject_reference = "movies-0"
    job.subject_snapshot = {
        "version": 1,
        "kind": "poster_subject_group",
        "display_id": "poster-group:movies:0",
        "display_name": "Old grouped title",
        "snapshot_at": NOW.isoformat(),
        "library": "movies",
        "chunk_index": 0,
        "batch_mode": mode,
        "members": [{"subject_key": "movie:1", "subject": MOVIE_SUBJECT}],
    }
    return job


def poster_atomic(*, job_id: str, parent_id: str, title: str = "Legacy Movie") -> Job:
    job = make_job(job_id=job_id, phase="queued", offset=2)
    job.type = "poster_pipeline"
    job.parent_id = parent_id
    job.root_id = parent_id
    job.request = {"movie_id": 2, "title": title}
    job.feature_area = "ai_posters"
    job.presentation_family = "ai_posters"
    job.subject_kind = "movie"
    job.subject_reference = "2"
    job.subject_snapshot = {
        **MOVIE_SUBJECT,
        "display_id": "movie:2",
        "display_name": title,
        "movie_id": 2,
        "title": title,
    }
    return job


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
async def test_activity_hierarchy_promotes_groups_and_keeps_legacy_parent(db, client):
    grouped_parent = poster_parent(job_id="grouped-parent-0000000000000001")
    group = poster_group(
        job_id="group-execution-000000000000001",
        parent_id=grouped_parent.id,
    )
    hidden_atomic = poster_atomic(
        job_id="group-atomic-00000000000000001",
        parent_id=grouped_parent.id,
        title="Hidden Alpha Child",
    )
    nested_atomic = poster_atomic(
        job_id="nested-atomic-0000000000000001",
        parent_id=group.id,
        title="Nested Alpha Child",
    )
    legacy_parent = poster_parent(job_id="legacy-parent-00000000000000001", legacy=True)
    legacy_child = poster_atomic(
        job_id="legacy-child-000000000000000001",
        parent_id=legacy_parent.id,
    )
    reset = poster_atomic(
        job_id="independent-reset-00000000000001",
        parent_id=legacy_parent.id,
        title="Independent Reset",
    )
    reset.type = "poster_reset"
    reset.parent_id = None
    reset.root_id = reset.id
    reset.request = {"target_kind": "movie", "target_id": 2}
    db.add_all(
        [grouped_parent, group, hidden_atomic, nested_atomic, legacy_parent, legacy_child, reset]
    )
    await db.commit()

    all_response = await client.get("/api/jobs", params={"view": "queue", "hierarchy": "all"})
    assert all_response.status_code == 200
    assert len(all_response.json()["items"]) == 7

    activity = await client.get("/api/jobs", params={"view": "queue", "hierarchy": "activity"})
    assert activity.status_code == 200
    rows = {item["job_id"]: item for item in activity.json()["items"]}
    assert set(rows) == {group.id, legacy_parent.id, reset.id}
    assert rows[group.id]["subject"]["display_name"] == "Get Film Posters · 1 Subject"
    assert rows[group.id]["is_parent"] is False
    assert rows[legacy_parent.id]["is_parent"] is True
    assert rows[reset.id]["job_type"] == "poster_reset"


@pytest.mark.asyncio
async def test_activity_filters_match_hidden_parent_and_contained_assets(db, client):
    parent = poster_parent(job_id="filter-parent-00000000000000001")
    group = poster_group(job_id="filter-group-000000000000000001", parent_id=parent.id)
    db.add_all([parent, group])
    await db.commit()

    by_parent_type = await client.get(
        "/api/jobs",
        params={
            "view": "queue",
            "hierarchy": "activity",
            "type": "poster_pipeline_batch",
        },
    )
    assert [item["job_id"] for item in by_parent_type.json()["items"]] == [group.id]

    by_atomic_type = await client.get(
        "/api/jobs",
        params={
            "view": "queue",
            "hierarchy": "activity",
            "type": "poster_pipeline",
        },
    )
    assert [item["job_id"] for item in by_atomic_type.json()["items"]] == [group.id]

    for params in (
        {"q": "Alpha Movie"},
        {"subject_kind": "movie"},
        {"subject_id": "1"},
    ):
        response = await client.get(
            "/api/jobs",
            params={"view": "queue", "hierarchy": "activity", **params},
        )
        assert response.status_code == 200
        assert [item["job_id"] for item in response.json()["items"]] == [group.id]


@pytest.mark.asyncio
async def test_activity_attention_uses_the_same_hierarchy_predicate(db, client):
    parent = poster_parent(job_id="attention-parent-000000000000001")
    parent.attention = {"level": "warning", "reason": "failed"}
    group = poster_group(job_id="attention-group-0000000000000001", parent_id=parent.id)
    group.attention = {"level": "warning", "reason": "failed"}
    atomic = poster_atomic(
        job_id="attention-atomic-000000000000001",
        parent_id=parent.id,
    )
    atomic.attention = {"level": "warning", "reason": "failed"}
    db.add_all([parent, group, atomic])
    await db.commit()

    response = await client.get("/api/jobs/attention")

    assert response.status_code == 200
    assert response.json()["needs_attention"] == 1
    assert response.json()["warning"] == 1
    assert response.json()["waiting_held"] == 1


@pytest.mark.asyncio
async def test_work_items_are_stably_paginated_with_authoritative_summary(db, client):
    parent = poster_parent(job_id="work-parent-0000000000000000001")
    group = poster_group(job_id="work-group-00000000000000000001", parent_id=parent.id)
    group.work_item_sequence = 4
    group.work_item_summary = {
        "version": 1,
        "total": 2,
        "counts": {
            "pending": 0,
            "running": 1,
            "succeeded": 0,
            "no_change": 0,
            "review_required": 1,
            "failed": 0,
            "cancelled": 0,
        },
    }
    group.work_item_updated_at = NOW
    db.add_all([parent, group])
    await db.flush()
    db.add_all(
        [
            JobWorkItem(
                job_id=group.id,
                subject_key="movie:1",
                ordinal=0,
                subject_kind="movie",
                subject_reference="1",
                subject_snapshot=MOVIE_SUBJECT,
                fence_token=1,
                status="running",
                stage_key="validating",
                stage_number=4,
                stage_total=9,
                completed=3,
                total=10,
                unit="candidates",
                update_sequence=4,
                updated_at=NOW,
            ),
            JobWorkItem(
                job_id=group.id,
                subject_key="movie:2",
                ordinal=1,
                subject_kind="movie",
                subject_reference="2",
                subject_snapshot={**MOVIE_SUBJECT, "movie_id": 2, "display_id": "movie:2"},
                fence_token=1,
                status="review_required",
                stage_key="finalizing",
                stage_number=9,
                stage_total=9,
                message="Poster candidates are ready for review.",
                update_sequence=4,
                updated_at=NOW,
            ),
        ]
    )
    await db.commit()

    with count_queries() as queries:
        first = await client.get(f"/api/jobs/{group.id}/work-items", params={"limit": 1})
    assert first.status_code == 200
    assert queries[0] == 2
    body = first.json()
    assert body["summary"]["counts"]["review_required"] == 1
    assert body["summary"]["sequence"] == 4
    assert body["items"][0]["subject_key"] == "movie:1"
    assert body["items"][0]["stage_name"] == "Validating"
    assert body["items"][0]["progress"] == {
        "completed": 3,
        "total": 10,
        "unit": "candidates",
    }
    assert body["next_cursor"] == 0

    second = await client.get(
        f"/api/jobs/{group.id}/work-items",
        params={"limit": 1, "cursor": body["next_cursor"]},
    )
    assert second.status_code == 200
    assert [item["subject_key"] for item in second.json()["items"]] == ["movie:2"]
    assert second.json()["next_cursor"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("outcome", "expected_status"),
    [
        ("cancelled", "cancelled"),
        ("succeeded", "succeeded"),
        ("no_change", "no_change"),
        ("partially_succeeded", "review_required"),
    ],
)
async def test_historical_group_work_items_fall_back_without_backfill(
    db, client, outcome, expected_status
):
    parent = poster_parent(job_id="fallback-parent-0000000000000001")
    group = poster_group(job_id="fallback-group-00000000000000001", parent_id=parent.id)
    group.phase = "terminal"
    group.outcome = outcome
    group.terminal_at = NOW
    group.subject_snapshot = {
        **group.subject_snapshot,
        "members": [
            {"subject_key": "movie:1", "subject": MOVIE_SUBJECT},
            {
                "subject_key": "movie:2",
                "subject": {**MOVIE_SUBJECT, "movie_id": 2, "display_id": "movie:2"},
            },
        ],
    }
    group.request = {
        **group.request,
        "members": [
            {"movie_id": 1, "title": "Alpha Movie"},
            {"movie_id": 2, "title": "Beta Movie"},
        ],
    }
    db.add_all([parent, group])
    await db.commit()

    response = await client.get(f"/api/jobs/{group.id}/work-items")

    assert response.status_code == 200
    assert response.json()["historical_fallback"] is True
    assert response.json()["summary"]["counts"][expected_status] == 2
    assert {item["status"] for item in response.json()["items"]} == {expected_status}
    if outcome == "partially_succeeded":
        assert {item["message"] for item in response.json()["items"]} == {
            "This older run needs review; no poster-specific reason was recorded."
        }


@pytest.mark.parametrize(
    ("status", "selected_artifact_id", "expected_message"),
    [
        ("flagged_manual", None, "No candidate passed the configured filters."),
        ("completed", None, "Poster candidates are ready for review."),
    ],
)
def test_historical_pipeline_runs_explain_review_required(
    status, selected_artifact_id, expected_message
):
    job = poster_group(
        job_id="historical-message-group-00000001",
        parent_id="historical-message-parent-0000001",
    )
    job.phase = "terminal"
    job.outcome = "partially_succeeded"
    job.work_item_sequence = 0
    run = type(
        "HistoricalRun",
        (),
        {
            "status": status,
            "selected_artifact_id": selected_artifact_id,
            "completed_at": NOW,
            "error": None,
        },
    )()

    row = jobs_route._fallback_work_item(
        job=job,
        wrapper=job.subject_snapshot["members"][0],
        ordinal=0,
        run=run,
    )

    assert row.status == "review_required"
    assert row.message == expected_message


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
