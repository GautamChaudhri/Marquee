from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient

from marquee.core.jobs.control import pgqueuer_gateway
from marquee.main import app
from marquee.models import Job

NOW = datetime(2026, 7, 13, 13, 0, tzinfo=UTC)
SUBJECT = {
    "version": 1,
    "kind": "system_work",
    "display_id": "system:noop",
    "display_name": "System no-op",
    "snapshot_at": NOW.isoformat(),
    "work": "noop",
}


def make_job(
    job_id: str,
    *,
    phase: str = "planned",
    outcome: str | None = None,
    pgq_job_id: int | None = None,
) -> Job:
    return Job(
        id=job_id,
        type="system_noop",
        request={"echo": {"job_id": job_id}},
        phase=phase,
        outcome=outcome,
        desired_state="run",
        fence_token=0,
        priority=50,
        eligible_at=NOW,
        pgq_job_id=pgq_job_id,
        dispatch_generation=1 if pgq_job_id is not None else 0,
        root_id=job_id,
        trigger_kind="system",
        feature_area="system",
        presentation_family="system",
        subject_kind="system_work",
        subject_reference="system:noop",
        subject_snapshot=SUBJECT,
        created_at=NOW,
        queued_at=NOW if phase == "queued" else None,
        terminal_at=NOW if phase == "terminal" else None,
    )


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as value:
        yield value


@pytest.mark.asyncio
async def test_priority_is_optimistic_and_execution_class_scoped(db, client):
    job = make_job("priority000000000000000000000001")
    db.add(job)
    await db.commit()

    response = await client.post(
        f"/api/jobs/{job.id}/priority",
        json={"expected_fence_token": 0, "priority": 80},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["execution_class"] == "control"
    assert body["snapshot"]["priority"] == 80
    assert body["snapshot"]["fence_token"] == 1

    stale = await client.post(
        f"/api/jobs/{job.id}/priority",
        json={"expected_fence_token": 0, "priority": 90},
    )
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "stale_job_version"
    assert stale.json()["detail"]["current_version"] == 1

    disallowed = await client.post(
        f"/api/jobs/{job.id}/pause", json={"expected_fence_token": 1}
    )
    assert disallowed.status_code == 409
    assert disallowed.json()["detail"]["code"] == "action_not_allowed"


@pytest.mark.asyncio
async def test_bulk_actions_deduplicate_and_report_partial_failure(db, client):
    first = make_job("bulk00000000000000000000000001")
    second = make_job("bulk00000000000000000000000002")
    db.add_all([first, second])
    await db.commit()

    response = await client.post(
        "/api/jobs/actions",
        json={
            "items": [
                {
                    "request_id": "first",
                    "job_id": first.id,
                    "action": "change_priority",
                    "expected_fence_token": 0,
                    "priority": 70,
                },
                {
                    "request_id": "blocked",
                    "job_id": second.id,
                    "action": "pause",
                    "expected_fence_token": 0,
                },
                {
                    "request_id": "first-duplicate",
                    "job_id": first.id,
                    "action": "change_priority",
                    "expected_fence_token": 0,
                    "priority": 70,
                },
            ]
        },
    )
    assert response.status_code == 200
    items = response.json()["items"]
    assert [(item["request_id"], item["success"]) for item in items] == [
        ("first", True),
        ("blocked", False),
        ("first-duplicate", True),
    ]
    assert items[1]["error"]["code"] == "action_not_allowed"
    await db.refresh(first)
    assert first.priority == 70
    assert first.fence_token == 1


@pytest.mark.asyncio
async def test_cancel_returns_new_snapshot(db, client, monkeypatch):
    job = make_job(
        "cancel0000000000000000000000001", phase="queued", pgq_job_id=401
    )
    db.add(job)
    await db.commit()

    async def fake_cancel(session, *, job_id):
        stored = await session.get(Job, job_id)
        stored.desired_state = "cancel"
        stored.phase = "terminal"
        stored.outcome = "cancelled"
        stored.terminal_at = NOW

    monkeypatch.setattr(pgqueuer_gateway, "cancel_known_ticket", fake_cancel)
    response = await client.post(
        f"/api/jobs/{job.id}/cancel", json={"expected_fence_token": 0}
    )
    assert response.status_code == 200
    assert response.json()["snapshot"]["outcome"] == "cancelled"
    assert response.json()["snapshot"]["fence_token"] == 1


@pytest.mark.asyncio
async def test_terminal_cancel_is_idempotent_without_reopening_work(db, client):
    job = make_job(
        "cancelterminal000000000000000001",
        phase="terminal",
        outcome="cancelled",
    )
    job.desired_state = "cancel"
    job.fence_token = 4
    db.add(job)
    await db.commit()

    response = await client.post(
        f"/api/jobs/{job.id}/cancel",
        json={"expected_fence_token": 0},
    )

    assert response.status_code == 200
    snapshot = response.json()["snapshot"]
    assert (snapshot["phase"], snapshot["outcome"], snapshot["fence_token"]) == (
        "terminal",
        "cancelled",
        4,
    )


@pytest.mark.asyncio
async def test_retry_creates_successor_with_lineage(db, client, monkeypatch):
    original = make_job(
        "retry00000000000000000000000001", phase="terminal", outcome="failed"
    )
    original.correlation_id = "retry-correlation"
    db.add(original)
    await db.commit()

    monkeypatch.setattr(
        "marquee.core.jobs.control.configuration_provider.snapshot_for",
        lambda _keys: SimpleNamespace(version=1, values={}),
    )

    async def fake_enqueue(_session, **_kwargs):
        return 501

    monkeypatch.setattr(pgqueuer_gateway, "enqueue", fake_enqueue)
    response = await client.post(
        f"/api/jobs/{original.id}/retry", json={"expected_fence_token": 0}
    )
    assert response.status_code == 200
    body = response.json()
    replacement_id = body["replacement_job_id"]
    assert body["original_job_id"] == original.id
    assert replacement_id != original.id
    assert body["snapshot"]["retry_of_job_id"] == original.id
    assert body["snapshot"]["root_id"] == original.root_id

    replacement = await db.get(Job, replacement_id)
    assert replacement.retry_of_job_id == original.id
    assert replacement.root_id == original.root_id
    assert replacement.correlation_id == original.correlation_id
    await db.refresh(original)
    assert original.phase == "terminal"
    assert original.fence_token == 1


@pytest.mark.asyncio
async def test_action_targets_job_discovered_beyond_first_page(db, client):
    jobs = [make_job(f"action{i:026d}") for i in range(101)]
    for index, job in enumerate(jobs):
        job.created_at = NOW.replace(microsecond=index)
        job.eligible_at = job.created_at
    db.add_all(jobs)
    await db.commit()

    first = await client.get("/api/jobs", params={"view": "queue", "limit": 100})
    assert first.status_code == 200
    second = await client.get(
        "/api/jobs",
        params={"view": "queue", "limit": 100, "cursor": first.json()["next_cursor"]},
    )
    target = second.json()["items"][0]

    response = await client.post(
        f"/api/jobs/{target['job_id']}/priority",
        json={"expected_fence_token": target["fence_token"], "priority": 75},
    )

    assert response.status_code == 200
    assert response.json()["snapshot"]["priority"] == 75
    assert (await client.patch(f"/api/jobs/{target['job_id']}/priority")).status_code == 405
