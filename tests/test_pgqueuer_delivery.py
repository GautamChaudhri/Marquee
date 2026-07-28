from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import anyio
import asyncpg
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from pgqueuer import PgQueuer, Queries, RetryRequested
from pgqueuer.models import Context
from pgqueuer.models import Job as PgQueuerJob
from pgqueuer.types import QueueExecutionMode
from sqlalchemy import func, select, text

from marquee.config import settings
from marquee.core.configuration_cache import (
    ExecutionConfigurationSnapshot,
    configuration_provider,
)
from marquee.core.jobs import delivery, submission
from marquee.core.jobs.artifact_service import repair_terminal_virtual_artifacts
from marquee.core.jobs.commands import create_system_noop
from marquee.core.jobs.definitions import JobDefinitionRegistry, TimeoutPolicy
from marquee.core.jobs.delivery import (
    DeliveryRejectedError,
    deliver_control_job,
    parse_transport_payload,
)
from marquee.core.jobs.fenced_writer import (
    AttemptOwnership,
    FencedWriter,
    WriteDisposition,
)
from marquee.core.jobs.manifest import JOB_DEFINITION_REGISTRY
from marquee.core.jobs.orphan_reconciliation import (
    _bounded_candidates,
    reconcile_candidate,
    reconcile_startup_orphans,
)
from marquee.core.jobs.pgqueuer_scheduler import create_scheduler
from marquee.core.jobs.pgqueuer_worker import create_worker
from marquee.core.jobs.policies import (
    ClassifiedExecutionError,
    RetryClassification,
    RetryPolicy,
)
from marquee.core.jobs.process_identity import IdentityStatus, read_boot_id
from marquee.core.jobs.progress_service import progress_writer
from marquee.core.jobs.safety_gates import (
    SafetyGateService,
    SafetyGateTimeoutError,
    SafetyRequirements,
)
from marquee.core.jobs.transport_intent_monitor import TransportIntentMonitor
from marquee.database import _get_engine
from marquee.main import app
from marquee.models import JobArtifact, RuntimeInstance
from marquee.models.job import Job, JobAttempt, JobDispatch, JobEvent


@pytest_asyncio.fixture(autouse=True)
async def installed_pgqueuer(db) -> Queries:
    async with _get_engine().connect() as connection:
        raw = await connection.get_raw_connection()
        queries = Queries.from_asyncpg_connection(raw.driver_connection)
        await queries.install()
        try:
            yield queries
        finally:
            await db.rollback()
            await queries.uninstall()


async def _canonical_ticket(db, *, payload: dict | None = None) -> tuple[str, int]:
    await db.rollback()
    job = await create_system_noop(
        db,
        payload=payload or {"echo": "noop"},
        idempotency_key=f"system_noop:{uuid4().hex}",
    )
    dispatch = await db.scalar(
        select(JobDispatch).where(
            JobDispatch.job_id == job.id,
            JobDispatch.generation == 1,
        )
    )
    assert dispatch is not None
    values = (job.id, dispatch.pgq_job_id)
    await db.rollback()
    assert values[1] is not None
    return values[0], values[1]


def _transport_job(
    job_id: str, pgq_job_id: int, *, attempts: int = 0, entrypoint: str = "control"
) -> PgQueuerJob:
    now = datetime.now(UTC)
    return PgQueuerJob(
        id=pgq_job_id,
        priority=50,
        created=now,
        updated=now,
        heartbeat=now,
        execute_after=now,
        status="picked",
        entrypoint=entrypoint,
        payload=(f'{{"dispatch_generation":1,"job_id":"{job_id}","payload_version":1}}'.encode()),
        attempts=attempts,
        queue_manager_id=uuid4(),
        headers=None,
    )


def _context() -> Context:
    return Context(cancellation=anyio.CancelScope())


@pytest.mark.parametrize(
    "payload",
    [
        None,
        b"not-json",
        b'{"job_id":"x","payload_version":1}',
        b'{"dispatch_generation":1,"job_id":"x","payload_version":1,"extra":true}',
        b'{"dispatch_generation":1,"job_id":"x","job_id":"y","payload_version":1}',
        b'{"dispatch_generation":1,"job_id":"x","payload_version":2}',
        b"\xff",
    ],
)
def test_transport_payload_is_strict(payload):
    with pytest.raises(DeliveryRejectedError):
        parse_transport_payload(payload)


async def test_delivery_commits_canonical_success_before_return(db):
    job_id, ticket_id = await _canonical_ticket(db)
    await deliver_control_job(_transport_job(job_id, ticket_id), _context())

    await db.rollback()
    db.expire_all()
    job = await db.get(Job, job_id)
    attempts = list(await db.scalars(select(JobAttempt).where(JobAttempt.job_id == job_id)))
    progress_events = list(
        await db.scalars(
            select(JobEvent)
            .where(JobEvent.job_id == job_id, JobEvent.event_key == "progress.updated")
            .order_by(JobEvent.id)
        )
    )
    assert job is not None
    assert (job.phase, job.outcome, job.result) == (
        "terminal",
        "succeeded",
        {"outcome": "succeeded", "message": None, "summary": {"echo": "noop"}},
    )
    assert len(attempts) == 1
    assert (attempts[0].phase, attempts[0].outcome) == ("finished", "succeeded")
    assert job.progress_sequence == 3
    assert job.progress["freshness"] == "terminal"
    assert [event.detail["progress_sequence"] for event in progress_events] == [1, 2, 3]


async def test_canonical_delivery_streams_execution_io_to_presenter(
    db,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    job_id, ticket_id = await _canonical_ticket(db)
    source = tmp_path / "source.bin"
    destination = tmp_path / "destination.bin"
    source.write_bytes(b"progress" * (256 * 1024))
    copied = asyncio.Event()
    release = asyncio.Event()

    async def handler(execution):
        await execution.io.copy(source, destination)
        copied.set()
        await release.wait()
        return {"outcome": "succeeded", "summary": {"echo": "io"}}

    monkeypatch.setitem(delivery._EXECUTION_HANDLERS, "system_noop", handler)
    task = asyncio.create_task(deliver_control_job(_transport_job(job_id, ticket_id), _context()))
    await copied.wait()
    release.set()
    await task
    await db.rollback()

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.get(f"/api/jobs/{job_id}/presentation")
    assert response.status_code == 200
    progress = response.json()["progress"]
    assert progress["metrics"]["bytes_processed"] == source.stat().st_size
    assert progress["metrics"]["bytes_total"] == source.stat().st_size
    assert progress["current_subject"]["kind"] == "system_work"

    assert destination.read_bytes() == source.read_bytes()


async def test_progress_failure_does_not_change_successful_media_effect(db, monkeypatch):
    job_id, ticket_id = await _canonical_ticket(db)

    async def broken_progress_write(**_kwargs):
        raise RuntimeError("synthetic progress database failure")

    monkeypatch.setattr(progress_writer, "write", broken_progress_write)
    await deliver_control_job(_transport_job(job_id, ticket_id), _context())

    await db.rollback()
    db.expire_all()
    job = await db.get(Job, job_id)
    assert job is not None
    assert (job.phase, job.outcome) == ("terminal", "succeeded")
    assert job.progress is None


async def test_duplicate_delivery_performs_effect_once(db, monkeypatch):
    job_id, ticket_id = await _canonical_ticket(db)
    calls = 0
    admitted = asyncio.Event()
    release = asyncio.Event()

    async def handler(execution):
        nonlocal calls
        calls += 1
        admitted.set()
        await release.wait()
        return {"outcome": "succeeded", "summary": {"echo": "held"}}

    monkeypatch.setitem(delivery._EXECUTION_HANDLERS, "system_noop", handler)
    transport_job = _transport_job(job_id, ticket_id)
    first = asyncio.create_task(deliver_control_job(transport_job, _context()))
    await admitted.wait()
    with pytest.raises(RetryRequested, match="active attempt"):
        await deliver_control_job(transport_job, _context())
    release.set()
    await first
    assert calls == 1


async def test_safety_wait_cancellation_creates_no_attempt(db):
    job_id, ticket_id = await _canonical_ticket(db)
    blocker = await SafetyGateService().acquire(
        SafetyRequirements.exclusive_maintenance(),
        cancelled=lambda: False,
        deadline_seconds=1,
    )
    context = _context()
    task = asyncio.create_task(deliver_control_job(_transport_job(job_id, ticket_id), context))
    try:
        for _ in range(100):
            await db.rollback()
            job = await db.get(Job, job_id)
            if job is not None and (job.attention or {}).get("code") == "safety_wait":
                break
            await asyncio.sleep(0.01)
        context.cancellation.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        await blocker.release()

    await db.rollback()
    attempts = list(await db.scalars(select(JobAttempt).where(JobAttempt.job_id == job_id)))
    assert attempts == []


async def test_fenced_writer_rejects_stale_attempt_ownership(db, monkeypatch):
    job_id, ticket_id = await _canonical_ticket(db)
    admitted = asyncio.Event()
    release = asyncio.Event()

    async def handler(execution):
        admitted.set()
        await release.wait()
        return {"outcome": "succeeded", "summary": {"echo": "held"}}

    monkeypatch.setitem(delivery._EXECUTION_HANDLERS, "system_noop", handler)
    task = asyncio.create_task(deliver_control_job(_transport_job(job_id, ticket_id), _context()))
    await admitted.wait()
    await db.rollback()
    job = await db.get(Job, job_id)
    assert job is not None and job.current_attempt_id is not None
    ownership = AttemptOwnership(
        job_id=job.id,
        attempt_id=job.current_attempt_id,
        fence_token=job.fence_token,
        dispatch_generation=job.dispatch_generation,
    )
    job.fence_token += 1
    await db.commit()

    writer = FencedWriter(ownership, JOB_DEFINITION_REGISTRY.get("system_noop"))
    disposition = await writer.succeed({"outcome": "succeeded", "summary": {"echo": "stale"}})
    assert disposition == WriteDisposition.STALE

    release.set()
    with pytest.raises(DeliveryRejectedError):
        await task
    await db.rollback()
    job = await db.get(Job, job_id)
    assert job is not None and (job.phase, job.outcome) == ("running", None)


async def test_terminal_event_keeps_deep_result_on_the_canonical_snapshot(db, monkeypatch):
    job_id, ticket_id = await _canonical_ticket(db)
    result = {"outcome": "succeeded", "summary": {"effect": {"evidence": {"depth": 4}}}}

    async def handler(execution):
        return result

    monkeypatch.setitem(delivery._EXECUTION_HANDLERS, "system_noop", handler)
    await deliver_control_job(_transport_job(job_id, ticket_id), _context())

    await db.rollback()
    job = await db.get(Job, job_id)
    event = await db.scalar(
        select(JobEvent).where(JobEvent.job_id == job_id, JobEvent.event_key == "job.succeeded")
    )
    assert job is not None
    assert job.result is not None
    assert job.result["summary"] == result["summary"]
    assert event is not None
    assert event.detail == {"_canonical_version": job.fence_token}


@pytest.mark.parametrize("terminal_action", ["fail", "retry"])
async def test_mutation_publish_intent_is_quarantined_instead_of_retried(
    db, terminal_action, monkeypatch
):
    job_id, ticket_id = await _canonical_ticket(db)
    admitted = asyncio.Event()
    release = asyncio.Event()

    async def handler(execution):
        admitted.set()
        await release.wait()
        return {"outcome": "succeeded", "summary": {"echo": "held"}}

    monkeypatch.setitem(delivery._EXECUTION_HANDLERS, "system_noop", handler)
    task = asyncio.create_task(deliver_control_job(_transport_job(job_id, ticket_id), _context()))
    await admitted.wait()
    await db.rollback()
    job = await db.get(Job, job_id)
    assert job is not None and job.current_attempt_id is not None
    ownership = AttemptOwnership(
        job_id=job.id,
        attempt_id=job.current_attempt_id,
        fence_token=job.fence_token,
        dispatch_generation=job.dispatch_generation,
    )
    writer = FencedWriter(ownership, JOB_DEFINITION_REGISTRY.get("poster_deploy"))
    assert await writer.record_publish_intent({"destination_identity": "synthetic-poster"})

    if terminal_action == "retry":
        disposition = await writer.retry(reason="synthetic crash", delay_seconds=0)
    else:
        disposition = await writer.fail(RuntimeError("synthetic crash"))
    assert disposition == WriteDisposition.APPLIED

    await db.rollback()
    db.expire_all()
    job = await db.get(Job, job_id)
    assert job is not None
    assert (job.phase, job.outcome) == ("terminal", "unsafe")
    assert job.error["atomicity"]["uncertain_state"] is True
    assert job.error["atomicity"]["published"] is False
    assert job.error["stage"] in {"publication_reconciliation", "retry_classification"}

    release.set()
    with pytest.raises(DeliveryRejectedError, match="completion conflicted"):
        await task


async def test_pause_before_admission_consumes_no_attempt(db):
    job_id, ticket_id = await _canonical_ticket(db)
    job = await db.get(Job, job_id)
    assert job is not None
    job.desired_state = "pause"
    await db.commit()

    await deliver_control_job(_transport_job(job_id, ticket_id), _context())

    await db.rollback()
    db.expire_all()
    job = await db.get(Job, job_id)
    attempts = list(await db.scalars(select(JobAttempt).where(JobAttempt.job_id == job_id)))
    assert job is not None
    assert (job.phase, job.desired_state, job.pgq_job_id) == ("queued", "pause", None)
    assert attempts == []


async def test_serial_redelivery_after_terminal_commit_is_noop(db):
    job_id, ticket_id = await _canonical_ticket(db)
    delivery = _transport_job(job_id, ticket_id)
    await deliver_control_job(delivery, _context())
    await deliver_control_job(delivery, _context())

    await db.rollback()
    attempts = list(await db.scalars(select(JobAttempt).where(JobAttempt.job_id == job_id)))
    assert len(attempts) == 1


async def test_dispatch_disabled_registry_definition_is_held(db):
    job_id, ticket_id = await _canonical_ticket(db)
    job = await db.get(Job, job_id)
    assert job is not None
    job.type = "poster_heal"
    await db.commit()

    with pytest.raises(DeliveryRejectedError, match="definition or request"):
        await deliver_control_job(_transport_job(job_id, ticket_id), _context())

    await db.rollback()
    attempts = list(await db.scalars(select(JobAttempt).where(JobAttempt.job_id == job_id)))
    assert attempts == []


async def test_duplicate_delivery_never_guesses_running_attempt_is_abandoned(db, monkeypatch):
    job_id, ticket_id = await _canonical_ticket(db)
    admitted = asyncio.Event()
    release = asyncio.Event()

    async def handler(execution):
        admitted.set()
        await release.wait()
        return {"outcome": "succeeded", "summary": {"echo": "held"}}

    monkeypatch.setitem(delivery._EXECUTION_HANDLERS, "system_noop", handler)
    first = asyncio.create_task(deliver_control_job(_transport_job(job_id, ticket_id), _context()))
    await admitted.wait()
    retry: RetryRequested | None = None
    try:
        await deliver_control_job(
            _transport_job(job_id, ticket_id),
            _context(),
        )
    except RetryRequested as exc:
        retry = exc
    release.set()
    await first
    assert retry is not None
    assert "active attempt" in str(retry)

    await db.rollback()
    job = await db.get(Job, job_id)
    attempts = list(
        await db.scalars(
            select(JobAttempt).where(JobAttempt.job_id == job_id).order_by(JobAttempt.number)
        )
    )
    assert job is not None
    assert (job.phase, job.outcome) == ("terminal", "succeeded")
    assert [(attempt.phase, attempt.outcome) for attempt in attempts] == [
        ("finished", "succeeded"),
    ]


async def test_orphan_candidates_are_global_across_container_identity_change(db):
    job_id, ticket_id = await _canonical_ticket(db)
    job = await db.get(Job, job_id)
    assert job is not None
    attempt = JobAttempt(
        job_id=job_id,
        number=1,
        fence_token=1,
        pgq_job_id=ticket_id,
        transport_attempt=0,
        worker_node_id="container-before-recreation",
        phase="running",
        admitted_at=datetime.now(UTC),
        started_at=datetime.now(UTC),
    )
    db.add(attempt)
    await db.flush()
    job.phase = "running"
    job.fence_token = 1
    job.current_attempt_id = attempt.id
    await db.commit()

    candidates = await _bounded_candidates("container-after-recreation", limit=50)

    assert [candidate.ownership.attempt_id for candidate in candidates] == [attempt.id]


async def _attach_runtime_attempt(
    db,
    *,
    fresh: bool,
    job_type: str = "system_noop",
    metrics: dict | None = None,
    with_process_identity: bool = False,
    process_id: int = 42420,
) -> tuple[str, int, int]:
    job_id, ticket_id = await _canonical_ticket(db)
    now = datetime.now(UTC)
    runtime_id = str(uuid4())
    runtime = RuntimeInstance(
        id=runtime_id,
        role="worker",
        node_label="remote-runtime",
        build="runtime-test",
        host_boot_id=read_boot_id() if with_process_identity else "remote-boot",
        process_id=process_id,
        process_start_ticks=202,
        process_group_id=process_id,
        advertised_entrypoints=["control"],
        capabilities={"entrypoints": ["control"]},
        readiness="ready",
        started_at=now - timedelta(minutes=1),
        last_heartbeat_at=now if fresh else now - timedelta(minutes=1),
        heartbeat_expires_at=now + timedelta(seconds=30) if fresh else now - timedelta(seconds=1),
    )
    db.add(runtime)
    job = await db.get(Job, job_id)
    assert job is not None
    job.type = job_type
    attempt_metrics = dict(metrics or {})
    attempt = JobAttempt(
        job_id=job_id,
        number=1,
        fence_token=1,
        pgq_job_id=ticket_id,
        transport_attempt=0,
        worker_node_id="remote-runtime",
        runtime_instance_id=runtime_id,
        process_id=process_id if with_process_identity else None,
        process_group_id=process_id if with_process_identity else None,
        host_boot_id=read_boot_id() if with_process_identity else None,
        metrics=(
            {**attempt_metrics, "process_start_ticks": 202}
            if with_process_identity
            else attempt_metrics
        ),
        phase="running",
        admitted_at=now - timedelta(minutes=1),
        started_at=now - timedelta(minutes=1),
    )
    db.add(attempt)
    await db.flush()
    job.phase = "running"
    job.fence_token = 1
    job.current_attempt_id = attempt.id
    await db.commit()
    return job_id, ticket_id, attempt.id


async def test_fresh_runtime_attempt_is_never_superseded(db):
    job_id, _ticket_id, attempt_id = await _attach_runtime_attempt(db, fresh=True)

    counts = await reconcile_startup_orphans(
        worker_node="different-container",
        cooperative_seconds=0.01,
        term_seconds=0.01,
    )

    assert counts == {"active": 0, "interrupted": 0, "unsafe": 0, "stale": 0}
    await db.rollback()
    job = await db.get(Job, job_id)
    attempt = await db.get(JobAttempt, attempt_id)
    assert job is not None and attempt is not None
    assert (job.phase, attempt.phase) == ("running", "running")


async def test_stale_orphan_selection_is_bounded_after_freshness_filter(db):
    for offset in range(3):
        await _attach_runtime_attempt(db, fresh=True, process_id=42420 + offset)
    _job_id, _ticket_id, stale_attempt_id = await _attach_runtime_attempt(
        db, fresh=False, process_id=42423
    )

    candidates = await _bounded_candidates("replacement-container", limit=1)

    assert [candidate.ownership.attempt_id for candidate in candidates] == [stale_attempt_id]
    assert candidates[0].runtime_fresh is False


async def test_expired_remote_read_only_attempt_is_policy_superseded(db):
    job_id, _ticket_id, attempt_id = await _attach_runtime_attempt(db, fresh=False)

    counts = await reconcile_startup_orphans(
        worker_node="replacement-container",
        cooperative_seconds=0.01,
        term_seconds=0.01,
    )

    assert counts == {"active": 0, "interrupted": 1, "unsafe": 0, "stale": 0}
    await db.rollback()
    job = await db.get(Job, job_id)
    attempt = await db.get(JobAttempt, attempt_id)
    assert job is not None and attempt is not None
    assert (job.phase, job.outcome, attempt.phase, attempt.outcome) == (
        "queued",
        None,
        "finished",
        "interrupted",
    )


async def test_expired_cancelled_attempt_is_terminalized_by_one_recovery_fence(db):
    """A dead cancelling writer is recovered only after liveness proof, once."""
    job_id, _ticket_id, attempt_id = await _attach_runtime_attempt(db, fresh=False)
    job = await db.get(Job, job_id)
    attempt = await db.get(JobAttempt, attempt_id)
    assert job is not None and attempt is not None
    job.desired_state = "cancel"
    job.phase = "stopping"
    attempt.phase = "stopping"
    await db.commit()

    candidate = (await _bounded_candidates("replacement-container", limit=10))[0]
    assert (
        await reconcile_candidate(candidate, cooperative_seconds=0.01, term_seconds=0.01)
        == "cancelled"
    )

    await db.rollback()
    db.expire_all()
    job = await db.get(Job, job_id)
    attempt = await db.get(JobAttempt, attempt_id)
    assert job is not None and attempt is not None
    assert (job.desired_state, job.phase, job.outcome, job.fence_token) == (
        "cancel",
        "terminal",
        "cancelled",
        2,
    )
    assert (attempt.phase, attempt.outcome, attempt.fence_token) == (
        "finished",
        "cancelled",
        1,
    )
    event = await db.scalar(
        select(JobEvent)
        .where(JobEvent.job_id == job_id, JobEvent.event_key == "job.cancelled")
        .order_by(JobEvent.id.desc())
    )
    assert event is not None
    assert event.detail["_canonical_version"] == 2
    assert event.detail["recovery_fence_version"] == 2


async def test_redelivery_atomically_supersedes_expired_replay_safe_attempt(db):
    job_id, ticket_id, attempt_id = await _attach_runtime_attempt(db, fresh=False)

    await deliver_control_job(_transport_job(job_id, ticket_id), _context())

    await db.rollback()
    job = await db.get(Job, job_id)
    prior_attempt = await db.get(JobAttempt, attempt_id)
    attempts = (
        await db.scalars(
            select(JobAttempt).where(JobAttempt.job_id == job_id).order_by(JobAttempt.number)
        )
    ).all()
    assert job is not None and prior_attempt is not None
    assert (job.phase, job.outcome, job.fence_token) == ("terminal", "succeeded", 2)
    assert (prior_attempt.phase, prior_attempt.outcome) == (
        "finished",
        "interrupted",
    )
    assert [(attempt.number, attempt.fence_token, attempt.outcome) for attempt in attempts] == [
        (1, 1, "interrupted"),
        (2, 2, "succeeded"),
    ]


async def test_expired_unsafe_mutation_is_terminal_and_transport_held(db):
    job_id, ticket_id, attempt_id = await _attach_runtime_attempt(
        db,
        fresh=False,
        job_type="poster_deploy",
        metrics={"publish_intent": {"destination_identity": "unprovable"}},
    )
    candidate = (await _bounded_candidates("replacement-container", limit=10))[0]

    assert (
        await reconcile_candidate(candidate, cooperative_seconds=0.01, term_seconds=0.01)
        == "unsafe"
    )
    await db.rollback()
    job = await db.get(Job, job_id)
    attempt = await db.get(JobAttempt, attempt_id)
    assert job is not None and attempt is not None
    assert (job.phase, job.outcome, attempt.phase, attempt.outcome) == (
        "terminal",
        "unsafe",
        "finished",
        "interrupted",
    )
    assert job.error["atomicity"]["uncertain_state"] is True
    with pytest.raises(DeliveryRejectedError, match="held for operator resolution"):
        await deliver_control_job(_transport_job(job_id, ticket_id), _context())


async def test_same_host_pid_reuse_or_foreign_identity_is_never_signaled_or_replayed(
    db, monkeypatch
):
    job_id, _ticket_id, _attempt_id = await _attach_runtime_attempt(
        db, fresh=False, with_process_identity=True
    )
    candidate = (await _bounded_candidates("replacement-container", limit=10))[0]
    monkeypatch.setattr(
        "marquee.core.jobs.orphan_reconciliation.terminate_verified_orphan",
        lambda *_args, **_kwargs: asyncio.sleep(0, result=IdentityStatus.MISMATCH),
    )

    assert (
        await reconcile_candidate(candidate, cooperative_seconds=0.01, term_seconds=0.01)
        == "unsafe"
    )
    await db.rollback()
    job = await db.get(Job, job_id)
    assert job is not None and (job.phase, job.outcome) == ("terminal", "unsafe")


async def test_recovery_rejects_stale_fence_without_changing_new_owner(db):
    job_id, _ticket_id, _attempt_id = await _attach_runtime_attempt(db, fresh=False)
    candidate = (await _bounded_candidates("replacement-container", limit=10))[0]
    await db.rollback()
    job = await db.get(Job, job_id)
    assert job is not None
    job.fence_token += 1
    await db.commit()

    assert (
        await reconcile_candidate(candidate, cooperative_seconds=0.01, term_seconds=0.01) == "stale"
    )
    await db.rollback()
    job = await db.get(Job, job_id)
    assert job is not None and (job.phase, job.outcome, job.fence_token) == ("running", None, 2)


async def test_retry_is_persisted_before_pgqueuer_signal_is_rethrown(db, monkeypatch):
    job_id, ticket_id = await _canonical_ticket(db)

    async def handler(execution):
        raise RetryRequested(timedelta(seconds=3), "transient")

    monkeypatch.setitem(delivery._EXECUTION_HANDLERS, "system_noop", handler)
    with pytest.raises(RetryRequested):
        await deliver_control_job(_transport_job(job_id, ticket_id), _context())
    await db.rollback()
    job = await db.get(Job, job_id)
    attempt = await db.scalar(select(JobAttempt).where(JobAttempt.job_id == job_id))
    assert job is not None and attempt is not None
    assert (job.phase, job.outcome, attempt.phase, attempt.outcome) == (
        "queued",
        None,
        "finished",
        "retrying",
    )


async def test_real_queue_manager_owns_retry_delay_and_second_attempt(
    db, installed_pgqueuer, monkeypatch
):
    job_id, ticket_id = await _canonical_ticket(db)
    calls = 0
    completed = asyncio.Event()

    async def retry_once(execution):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RetryRequested(timedelta(milliseconds=50), "transient")
        completed.set()
        return {"outcome": "succeeded", "summary": {"echo": "done"}}

    definitions = [
        replace(
            definition,
            retry_policy=RetryPolicy(
                max_attempts=3,
                transient_delays_seconds=(0.05, 0.1),
            ),
        )
        if definition.job_type == "system_noop"
        else definition
        for definition in JOB_DEFINITION_REGISTRY
    ]
    monkeypatch.setattr(delivery, "JOB_DEFINITION_REGISTRY", JobDefinitionRegistry(definitions))
    monkeypatch.setitem(delivery._EXECUTION_HANDLERS, "system_noop", retry_once)
    async with _get_engine().connect() as connection:
        raw = await connection.get_raw_connection()
        app = PgQueuer.from_asyncpg_connection(raw.driver_connection)

        @app.entrypoint("control", accepts_context=True, on_failure="hold")
        async def control(transport_job, context):
            await deliver_control_job(transport_job, context)

        manager = asyncio.create_task(
            app.qm.run(
                dequeue_timeout=timedelta(milliseconds=20),
                batch_size=1,
                max_concurrent_tasks=2,
                heartbeat_timeout=timedelta(seconds=1),
            )
        )
        await asyncio.wait_for(completed.wait(), timeout=5)
        app.shutdown.set()
        await asyncio.wait_for(manager, timeout=5)

    await db.rollback()
    job = await db.get(Job, job_id)
    attempts = list(
        await db.scalars(
            select(JobAttempt).where(JobAttempt.job_id == job_id).order_by(JobAttempt.number)
        )
    )
    assert calls == 2
    assert job is not None and (job.phase, job.outcome) == ("terminal", "succeeded")
    assert [(attempt.phase, attempt.outcome) for attempt in attempts] == [
        ("finished", "retrying"),
        ("finished", "succeeded"),
    ]
    assert attempts[0].error["diagnostics"]["delay_seconds"] == 0.05
    assert await installed_pgqueuer.job_status([ticket_id]) == [(ticket_id, "successful")]


async def test_failure_is_terminalized_before_exception_is_rethrown(db, monkeypatch):
    job_id, ticket_id = await _canonical_ticket(db)

    async def handler(execution):
        raise RuntimeError("bounded failure")

    monkeypatch.setitem(delivery._EXECUTION_HANDLERS, "system_noop", handler)
    with pytest.raises(RuntimeError, match="bounded failure"):
        await deliver_control_job(_transport_job(job_id, ticket_id), _context())
    await db.rollback()
    job = await db.get(Job, job_id)
    assert job is not None
    assert (job.phase, job.outcome, job.error["code"]) == (
        "terminal",
        "failed",
        "runtime_error",
    )


async def test_stale_ticket_and_cancelled_job_do_not_execute(db, monkeypatch):
    job_id, ticket_id = await _canonical_ticket(db)
    job = await db.get(Job, job_id)
    assert job is not None
    job.desired_state = "cancel"
    await db.commit()

    called = False

    async def handler(execution):
        nonlocal called
        called = True
        return {"outcome": "succeeded", "summary": {}}

    monkeypatch.setitem(delivery._EXECUTION_HANDLERS, "system_noop", handler)
    await deliver_control_job(_transport_job(job_id, ticket_id), _context())
    await db.rollback()
    db.expire_all()
    job = await db.get(Job, job_id)
    assert job is not None
    assert not called
    assert (job.phase, job.outcome) == ("terminal", "cancelled")


async def test_picked_cancellation_reaches_test_blocker(db, installed_pgqueuer, monkeypatch):
    job_id, ticket_id = await _canonical_ticket(db)
    started = asyncio.Event()

    async def blocker(execution):
        started.set()
        while not execution.cancellation.cancel_called:
            await asyncio.sleep(0.01)
        raise asyncio.CancelledError

    monkeypatch.setitem(delivery._EXECUTION_HANDLERS, "system_noop", blocker)
    async with _get_engine().connect() as connection:
        raw = await connection.get_raw_connection()
        queue_app = PgQueuer.from_asyncpg_connection(raw.driver_connection)

        @queue_app.entrypoint("control", accepts_context=True, on_failure="hold")
        async def control(transport_job, context):
            await deliver_control_job(transport_job, context)

        manager = asyncio.create_task(
            queue_app.qm.run(
                dequeue_timeout=timedelta(milliseconds=50),
                batch_size=1,
                max_concurrent_tasks=2,
                heartbeat_timeout=timedelta(seconds=1),
            )
        )
        await asyncio.wait_for(started.wait(), timeout=5)
        await db.rollback()
        db.expire_all()
        job = await db.get(Job, job_id)
        assert job is not None
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            response = await client.post(
                f"/api/jobs/{job_id}/cancel",
                json={"expected_fence_token": job.fence_token},
            )
        assert response.status_code == 200, response.text

        for _ in range(100):
            await db.rollback()
            db.expire_all()
            job = await db.get(Job, job_id)
            if job is not None and job.phase == "terminal":
                break
            await asyncio.sleep(0.02)
        queue_app.shutdown.set()
        await asyncio.wait_for(manager, timeout=5)

    assert job is not None
    assert (job.desired_state, job.phase, job.outcome) == (
        "cancel",
        "terminal",
        "cancelled",
    )
    assert await installed_pgqueuer.job_status([ticket_id]) == [(ticket_id, "canceled")]


async def test_public_cancel_keeps_active_delivery_fence_until_terminalization(
    db, installed_pgqueuer, monkeypatch
):
    """The public command may request cancellation, but delivery owns the terminal write."""
    from marquee.core.jobs.control import cancel

    job_id, ticket_id = await _canonical_ticket(db)
    started = asyncio.Event()

    async def blocker(execution):
        started.set()
        while not execution.cancellation.cancel_called:
            await asyncio.sleep(0.01)
        raise asyncio.CancelledError

    monkeypatch.setitem(delivery._EXECUTION_HANDLERS, "system_noop", blocker)
    async with _get_engine().connect() as connection:
        raw = await connection.get_raw_connection()
        app = PgQueuer.from_asyncpg_connection(raw.driver_connection)

        @app.entrypoint("control", accepts_context=True, on_failure="hold")
        async def control(transport_job, context):
            await deliver_control_job(transport_job, context)

        manager = asyncio.create_task(
            app.qm.run(
                dequeue_timeout=timedelta(milliseconds=50),
                batch_size=1,
                max_concurrent_tasks=2,
                heartbeat_timeout=timedelta(seconds=1),
            )
        )
        await asyncio.wait_for(started.wait(), timeout=5)
        job = await db.get(Job, job_id)
        assert job is not None
        expected_fence_token = job.fence_token
        await db.rollback()
        await cancel(db, job_id=job_id, expected_fence_token=expected_fence_token)

        for _ in range(100):
            await db.rollback()
            db.expire_all()
            job = await db.get(Job, job_id)
            if job is not None and job.phase == "terminal":
                break
            await asyncio.sleep(0.02)
        app.shutdown.set()
        await asyncio.wait_for(manager, timeout=5)

    assert job is not None
    assert (job.desired_state, job.phase, job.outcome, job.fence_token) == (
        "cancel",
        "terminal",
        "cancelled",
        expected_fence_token,
    )
    attempt = await db.scalar(select(JobAttempt).where(JobAttempt.job_id == job_id))
    assert attempt is not None
    assert (attempt.phase, attempt.outcome, attempt.fence_token) == (
        "finished",
        "cancelled",
        expected_fence_token,
    )
    assert await installed_pgqueuer.job_status([ticket_id]) == [(ticket_id, "canceled")]


async def test_real_gate_contention_defers_without_attempt_then_executes_once(
    db, installed_pgqueuer, monkeypatch
):
    """A PgQueuer delivery defers gate contention before creating an attempt."""

    job_id, _ticket_id = await _canonical_ticket(db)
    monkeypatch.setattr(settings, "JOB_ADMISSION_TIMEOUT_SECONDS", 0.05)
    blocker = await SafetyGateService().acquire(
        SafetyRequirements.exclusive_maintenance(),
        cancelled=lambda: False,
        deadline_seconds=1,
    )
    async with _get_engine().connect() as connection:
        raw = await connection.get_raw_connection()
        app = PgQueuer.from_asyncpg_connection(raw.driver_connection)

        @app.entrypoint("control", accepts_context=True, on_failure="hold")
        async def control(transport_job, context):
            await deliver_control_job(transport_job, context)

        manager = asyncio.create_task(
            app.qm.run(
                dequeue_timeout=timedelta(milliseconds=25),
                batch_size=1,
                max_concurrent_tasks=2,
                heartbeat_timeout=timedelta(seconds=1),
            )
        )
        job = None
        try:
            try:
                with anyio.move_on_after(10) as deferral_scope:
                    while True:
                        await db.rollback()
                        db.expire_all()
                        job = await db.get(Job, job_id)
                        if (
                            job is not None
                            and (job.attention or {}).get("code") == "admission_deferral_pending"
                        ):
                            break
                        await asyncio.sleep(0.02)
                assert not deferral_scope.cancel_called, (
                    "PgQueuer did not persist admission deferral within 10 seconds; "
                    f"last_phase={getattr(job, 'phase', None)!r}, "
                    f"last_outcome={getattr(job, 'outcome', None)!r}, "
                    f"last_attention={getattr(job, 'attention', None)!r}"
                )
                assert job is not None
                assert job.phase == "queued"
                attempts = list(
                    await db.scalars(select(JobAttempt).where(JobAttempt.job_id == job_id))
                )
                assert attempts == []
                assert (job.attention or {}).get("defer_count") == 1
            finally:
                await blocker.release()

            with anyio.move_on_after(15) as terminal_scope:
                while True:
                    await db.rollback()
                    db.expire_all()
                    job = await db.get(Job, job_id)
                    if job is not None and job.phase == "terminal":
                        break
                    await asyncio.sleep(0.02)
            assert not terminal_scope.cancel_called, (
                "PgQueuer did not redeliver the deferred job within 15 seconds; "
                f"last_phase={getattr(job, 'phase', None)!r}, "
                f"last_outcome={getattr(job, 'outcome', None)!r}, "
                f"last_attention={getattr(job, 'attention', None)!r}"
            )
        finally:
            app.shutdown.set()
            await asyncio.wait_for(manager, timeout=5)

    assert job is not None
    assert (job.phase, job.outcome) == ("terminal", "succeeded")
    attempts = list(await db.scalars(select(JobAttempt).where(JobAttempt.job_id == job_id)))
    assert len(attempts) == 1


async def test_public_cancel_during_gate_wait_creates_no_attempt(
    db,
    installed_pgqueuer,
) -> None:
    """Public cancellation resolves a picked gate waiter without giving it execution ownership."""
    job_id, ticket_id = await _canonical_ticket(db)
    blocker = await SafetyGateService().acquire(
        SafetyRequirements.exclusive_maintenance(),
        cancelled=lambda: False,
        deadline_seconds=1,
    )
    async with _get_engine().connect() as connection:
        raw = await connection.get_raw_connection()
        queue_app = PgQueuer.from_asyncpg_connection(raw.driver_connection)

        @queue_app.entrypoint("control", accepts_context=True, on_failure="hold")
        async def control(transport_job, context):
            await deliver_control_job(transport_job, context)

        manager = asyncio.create_task(
            queue_app.qm.run(
                dequeue_timeout=timedelta(milliseconds=25),
                batch_size=1,
                max_concurrent_tasks=2,
                heartbeat_timeout=timedelta(seconds=1),
            )
        )
        try:
            for _ in range(100):
                await db.rollback()
                db.expire_all()
                job = await db.get(Job, job_id)
                if job is not None and (job.attention or {}).get("code") == "safety_wait":
                    break
                await asyncio.sleep(0.02)
            assert job is not None
            assert (
                list(await db.scalars(select(JobAttempt).where(JobAttempt.job_id == job_id))) == []
            )

            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
            ) as client:
                cancelled = await client.post(
                    f"/api/jobs/{job_id}/cancel",
                    json={"expected_fence_token": job.fence_token},
                )
            assert cancelled.status_code == 200, cancelled.text

            for _ in range(100):
                await db.rollback()
                db.expire_all()
                job = await db.get(Job, job_id)
                if job is not None and job.phase == "terminal":
                    break
                await asyncio.sleep(0.02)
            assert job is not None and job.phase == "terminal"
        finally:
            await blocker.release()
            queue_app.shutdown.set()
            await asyncio.wait_for(manager, timeout=5)

    assert (job.desired_state, job.phase, job.outcome) == (
        "cancel",
        "terminal",
        "cancelled",
    )
    assert list(await db.scalars(select(JobAttempt).where(JobAttempt.job_id == job_id))) == []
    assert await installed_pgqueuer.job_status([ticket_id]) == [(ticket_id, "canceled")]


async def test_monitor_recovers_an_absent_deferred_ticket_on_the_same_dispatch(
    db, installed_pgqueuer
):
    """A callback crash may lose its PgQueuer row but not the canonical deferral."""
    job_id, ticket_id = await _canonical_ticket(db)
    now = datetime.now(UTC)
    job = await db.get(Job, job_id)
    dispatch = await db.scalar(
        select(JobDispatch).where(
            JobDispatch.job_id == job_id,
            JobDispatch.generation == 1,
        )
    )
    assert job is not None and dispatch is not None
    job.eligible_at = now + timedelta(seconds=1)
    dispatch.eligible_at = job.eligible_at
    job.attention = {
        "code": "admission_deferral_pending",
        "summary": "synthetic callback interruption",
        "cause": "safety_gate_timeout",
        "defer_count": 1,
        "delay_seconds": 1.0,
        "next_eligible_at": job.eligible_at.isoformat(),
    }
    await db.commit()

    # Simulate only the documented crash window: canonical intent committed,
    # transport row absent. Recovery still travels through the public gateway.
    await db.execute(text("DELETE FROM pgqueuer WHERE id = :ticket_id"), {"ticket_id": ticket_id})
    await db.commit()

    assert await TransportIntentMonitor(batch_size=10).run_once() == {
        "cancelled": 0,
        "retried": 1,
        "attention": 0,
    }
    await db.rollback()
    db.expire_all()
    job = await db.get(Job, job_id)
    dispatch = await db.scalar(
        select(JobDispatch).where(
            JobDispatch.job_id == job_id,
            JobDispatch.generation == 1,
        )
    )
    assert job is not None and dispatch is not None
    assert job.pgq_job_id is not None and job.pgq_job_id != ticket_id
    assert dispatch.pgq_job_id == job.pgq_job_id
    assert (job.attention or {}).get("code") == "admission_deferred"
    assert await installed_pgqueuer.job_status([job.pgq_job_id]) == [(job.pgq_job_id, "queued")]
    assert list(await db.scalars(select(JobAttempt).where(JobAttempt.job_id == job_id))) == []


async def test_transient_gate_connection_loss_defers_before_attempt(db, monkeypatch):
    job_id, ticket_id = await _canonical_ticket(db)

    async def lost_connection():
        raise asyncpg.ConnectionDoesNotExistError("synthetic lost gate connection")

    monkeypatch.setattr(
        delivery,
        "_SAFETY_GATES",
        SafetyGateService(connection_factory=lost_connection),
    )
    with pytest.raises(RetryRequested, match="safety-gate admission deferred"):
        await deliver_control_job(_transport_job(job_id, ticket_id), _context())

    await db.rollback()
    job = await db.get(Job, job_id)
    assert job is not None
    assert (job.phase, job.attention["code"], job.attention["cause"]) == (
        "queued",
        "admission_deferral_pending",
        "safety_gate_connection_lost",
    )
    assert list(await db.scalars(select(JobAttempt).where(JobAttempt.job_id == job_id))) == []


async def test_hard_gate_connection_error_is_held_without_admission_deferral(db, monkeypatch):
    job_id, ticket_id = await _canonical_ticket(db)

    async def invalid_connection():
        raise asyncpg.InvalidAuthorizationSpecificationError("synthetic invalid credentials")

    monkeypatch.setattr(
        delivery,
        "_SAFETY_GATES",
        SafetyGateService(connection_factory=invalid_connection),
    )
    with pytest.raises(asyncpg.InvalidAuthorizationSpecificationError):
        await deliver_control_job(_transport_job(job_id, ticket_id), _context())

    await db.rollback()
    job = await db.get(Job, job_id)
    assert job is not None
    assert job.phase == "queued"
    assert job.attention is None
    assert list(await db.scalars(select(JobAttempt).where(JobAttempt.job_id == job_id))) == []


async def test_gate_deferral_exhaustion_is_terminal_without_a_phantom_attempt(db, monkeypatch):
    job_id, ticket_id = await _canonical_ticket(db)

    class TimedOutGate:
        async def acquire(self, *_args, **_kwargs):
            raise SafetyGateTimeoutError("synthetic gate contention")

    monkeypatch.setattr(delivery, "_SAFETY_GATES", TimedOutGate())
    for _ in range(3):
        with pytest.raises(RetryRequested, match="safety-gate admission deferred"):
            await deliver_control_job(_transport_job(job_id, ticket_id), _context())
    with pytest.raises(DeliveryRejectedError, match="deferral budget exhausted"):
        await deliver_control_job(_transport_job(job_id, ticket_id), _context())

    await db.rollback()
    job = await db.get(Job, job_id)
    dispatch = await db.scalar(
        select(JobDispatch).where(
            JobDispatch.job_id == job_id,
            JobDispatch.generation == 1,
        )
    )
    assert job is not None and dispatch is not None
    assert (job.phase, job.outcome, job.error["code"], dispatch.disposition) == (
        "terminal",
        "failed",
        "admission_deferral_exhausted",
        "failed",
    )
    assert list(await db.scalars(select(JobAttempt).where(JobAttempt.job_id == job_id))) == []


async def test_unknown_canonical_job_is_held_without_legacy_resolution():
    with pytest.raises(DeliveryRejectedError, match="canonical job does not exist"):
        await deliver_control_job(_transport_job("0" * 32, 999999), _context())


async def test_real_queue_manager_delivers_control_and_acknowledges(db):
    job_id, _ = await _canonical_ticket(db)
    async with _get_engine().connect() as connection:
        raw = await connection.get_raw_connection()
        app: PgQueuer = create_worker(raw.driver_connection)
        await asyncio.wait_for(
            app.qm.run(
                dequeue_timeout=timedelta(milliseconds=50),
                batch_size=1,
                mode=QueueExecutionMode.drain,
                max_concurrent_tasks=2,
                heartbeat_timeout=timedelta(seconds=1),
            ),
            timeout=5,
        )

    await db.rollback()
    job = await db.get(Job, job_id)
    assert job is not None
    assert (job.phase, job.outcome) == ("terminal", "succeeded")


async def test_polling_fallback_delivers_without_matching_notification_channel(db):
    delivered = asyncio.Event()
    async with _get_engine().connect() as connection:
        raw = await connection.get_raw_connection()
        app = PgQueuer.from_asyncpg_connection(
            raw.driver_connection,
            channel="ch_notifications_intentionally_missing",
        )

        @app.entrypoint("control", accepts_context=True, on_failure="hold")
        async def control(transport_job, context):
            await deliver_control_job(transport_job, context)
            delivered.set()

        manager = asyncio.create_task(
            app.qm.run(
                dequeue_timeout=timedelta(milliseconds=100),
                batch_size=1,
                max_concurrent_tasks=2,
                heartbeat_timeout=timedelta(seconds=1),
            )
        )
        await asyncio.sleep(0.05)
        job_id, _ = await _canonical_ticket(db)
        await asyncio.wait_for(delivered.wait(), timeout=5)
        app.shutdown.set()
        await asyncio.wait_for(manager, timeout=5)

    await db.rollback()
    job = await db.get(Job, job_id)
    assert job is not None and (job.phase, job.outcome) == ("terminal", "succeeded")


async def test_real_queue_manager_holds_rejected_delivery(installed_pgqueuer):
    payload = (
        b'{"dispatch_generation":1,"job_id":"00000000000000000000000000000000","payload_version":1}'
    )
    ticket_ids = await installed_pgqueuer.enqueue(
        "control",
        payload,
        dedupe_key=f"rejected-{uuid4().hex}",
    )
    async with _get_engine().connect() as connection:
        raw = await connection.get_raw_connection()
        app = create_worker(raw.driver_connection)
        await asyncio.wait_for(
            app.qm.run(
                dequeue_timeout=timedelta(milliseconds=50),
                batch_size=1,
                mode=QueueExecutionMode.drain,
                max_concurrent_tasks=2,
                heartbeat_timeout=timedelta(seconds=1),
            ),
            timeout=5,
        )

    statuses = dict(await installed_pgqueuer.job_status(ticket_ids))
    assert statuses[ticket_ids[0]] == "failed"


async def test_stale_pick_is_recovered_by_pgqueuer_heartbeat(db, installed_pgqueuer):
    job_id, _ = await _canonical_ticket(db)
    async with _get_engine().connect() as connection:
        raw = await connection.get_raw_connection()
        app = create_worker(raw.driver_connection)
        picked = [
            job
            async for job in app.qm.fetch_jobs(
                batch_size=1,
                global_concurrency_limit=None,
                heartbeat_timeout=timedelta(milliseconds=50),
            )
        ]
        assert len(picked) == 1
        await asyncio.sleep(0.1)
        await asyncio.wait_for(
            app.qm.run(
                dequeue_timeout=timedelta(milliseconds=50),
                batch_size=1,
                mode=QueueExecutionMode.drain,
                max_concurrent_tasks=2,
                heartbeat_timeout=timedelta(milliseconds=50),
            ),
            timeout=5,
        )

    await db.rollback()
    job = await db.get(Job, job_id)
    assert job is not None
    assert (job.phase, job.outcome) == ("terminal", "succeeded")


async def test_two_scheduler_managers_reconcile_one_test_schedule(installed_pgqueuer):
    effects = 0
    fired = asyncio.Event()
    async with _get_engine().connect() as first, _get_engine().connect() as second:
        first_raw = await first.get_raw_connection()
        second_raw = await second.get_raw_connection()
        apps = [
            create_scheduler(first_raw.driver_connection),
            create_scheduler(second_raw.driver_connection),
        ]
        for app in apps:

            @app.schedule("contract_test_schedule", "*/1 * * * * *")
            async def scheduled_noop(schedule):
                nonlocal effects
                effects += 1
                fired.set()

        tasks = [asyncio.create_task(app.sm.run()) for app in apps]
        await asyncio.wait_for(fired.wait(), timeout=5)
        await asyncio.sleep(0.2)
        for app in apps:
            app.shutdown.set()
        await asyncio.wait_for(asyncio.gather(*tasks), timeout=5)

    rows = await installed_pgqueuer.peek_schedule()
    assert [row.entrypoint for row in rows].count("contract_test_schedule") == 1
    assert effects == 1


async def test_transport_diagnostics_are_bounded_aggregates(db):
    job_id, _ = await _canonical_ticket(db, payload={"echo": "do-not-expose"})

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/system/job-transport")

    assert response.status_code == 200
    body = response.json()
    assert body["connections"]["budget"]["within_budget"] is True
    assert body["connections"]["observed"] <= body["connections"]["budget"]["maximum"]
    assert body["queue"]
    serialized = response.text
    assert job_id not in serialized
    assert "pgq_job_id" not in serialized
    assert "do-not-expose" not in serialized
    assert "payload" not in serialized.lower()


async def test_non_mutation_no_change_survives_delivery_and_presentation(db, monkeypatch):
    job_id, ticket_id = await _canonical_ticket(db)

    async def handler(execution):
        return {"outcome": "no_change", "summary": {"reason": "already current"}}

    monkeypatch.setitem(delivery._EXECUTION_HANDLERS, "system_noop", handler)
    await deliver_control_job(_transport_job(job_id, ticket_id), _context())

    await db.rollback()
    job = await db.get(Job, job_id)
    attempt = await db.scalar(select(JobAttempt).where(JobAttempt.job_id == job_id))
    dispatch = await db.scalar(select(JobDispatch).where(JobDispatch.job_id == job_id))
    assert job is not None and attempt is not None and dispatch is not None
    assert (job.outcome, attempt.outcome, dispatch.disposition) == (
        "no_change",
        "succeeded",
        "succeeded",
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(f"/api/jobs/{job_id}/presentation")
    assert response.status_code == 200
    assert response.json()["status"]["outcome"] == "no_change"


@pytest.mark.parametrize(
    ("domain_outcome", "attempt_outcome", "dispatch_disposition"),
    (
        ("succeeded", "succeeded", "succeeded"),
        ("partially_succeeded", "succeeded", "succeeded"),
        ("failed", "succeeded", "succeeded"),
        ("cancelled", "cancelled", "cancelled"),
        ("superseded", "succeeded", "superseded"),
        ("unsafe", "succeeded", "succeeded"),
    ),
)
async def test_returned_domain_outcomes_use_separate_terminal_vocabularies(
    db,
    monkeypatch,
    domain_outcome,
    attempt_outcome,
    dispatch_disposition,
):
    job_id, ticket_id = await _canonical_ticket(db)

    async def handler(execution):
        return {"outcome": domain_outcome, "message": f"domain {domain_outcome}"}

    monkeypatch.setitem(delivery._EXECUTION_HANDLERS, "system_noop", handler)
    await deliver_control_job(_transport_job(job_id, ticket_id), _context())

    await db.rollback()
    job = await db.get(Job, job_id)
    attempt = await db.scalar(select(JobAttempt).where(JobAttempt.job_id == job_id))
    dispatch = await db.scalar(select(JobDispatch).where(JobDispatch.job_id == job_id))
    assert job is not None and attempt is not None and dispatch is not None
    assert (job.outcome, attempt.outcome, dispatch.disposition) == (
        domain_outcome,
        attempt_outcome,
        dispatch_disposition,
    )
    expected_attention = {
        "partially_succeeded": ("warning", "failed"),
        "failed": ("error", "failed"),
        "unsafe": ("error", "unsafe"),
    }.get(domain_outcome, ("normal", "none"))
    assert job.attention is not None
    assert (job.attention["level"], job.attention["reason"]) == expected_attention
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(f"/api/jobs/{job_id}/presentation")
    assert response.status_code == 200
    assert response.json()["status"]["outcome"] == domain_outcome
    assert (
        response.json()["attention"]["level"],
        response.json()["attention"]["reason"],
    ) == expected_attention


async def test_terminal_conflict_is_held_without_success_log_or_acknowledgement(db, monkeypatch):
    job_id, ticket_id = await _canonical_ticket(db)
    seals: list[tuple[str, str | None]] = []

    async def handler(execution):
        return {"outcome": "succeeded", "summary": {}}

    async def conflict(self, result, *, decision=None):
        return WriteDisposition.CONFLICT

    async def record_seal(log_sink, *, outcome, attempt_outcome=None, summary=None):
        seals.append((outcome, attempt_outcome))

    monkeypatch.setitem(delivery._EXECUTION_HANDLERS, "system_noop", handler)
    monkeypatch.setattr(FencedWriter, "succeed", conflict)
    monkeypatch.setattr(delivery, "_seal_attempt_log", record_seal)

    with pytest.raises(DeliveryRejectedError):
        await deliver_control_job(_transport_job(job_id, ticket_id), _context())
    assert seals == [("unsafe", "interrupted")]
    await db.rollback()
    job = await db.get(Job, job_id)
    attempt = await db.scalar(select(JobAttempt).where(JobAttempt.job_id == job_id))
    dispatch = await db.scalar(select(JobDispatch).where(JobDispatch.job_id == job_id))
    assert job is not None and attempt is not None and dispatch is not None
    assert (job.phase, job.outcome, attempt.phase, dispatch.disposition) == (
        "running",
        None,
        "running",
        "active",
    )


async def test_terminal_evidence_failure_is_repaired_without_rerunning_work(db, monkeypatch):
    job_id, ticket_id = await _canonical_ticket(db)
    executions = 0

    async def handler(execution):
        nonlocal executions
        executions += 1
        return {"outcome": "succeeded", "summary": {}}

    async def fail_registration(**kwargs):
        raise OSError("synthetic evidence outage")

    monkeypatch.setitem(delivery._EXECUTION_HANDLERS, "system_noop", handler)
    monkeypatch.setattr(delivery, "register_virtual_artifact", fail_registration)
    await deliver_control_job(_transport_job(job_id, ticket_id), _context())

    await db.rollback()
    job = await db.get(Job, job_id)
    degradation = await db.scalar(
        select(JobEvent).where(
            JobEvent.job_id == job_id,
            JobEvent.event_key == "artifact.failed",
        )
    )
    assert job is not None and (job.phase, job.outcome) == ("terminal", "succeeded")
    assert degradation is not None and degradation.detail["retryable"] is True

    repaired = await repair_terminal_virtual_artifacts(limit=10)
    assert repaired == {"examined": 1, "repaired": 1, "failed": 0}
    await db.rollback()
    artifact = await db.scalar(
        select(JobArtifact).where(
            JobArtifact.job_id == job_id,
            JobArtifact.kind == "canonical_result",
        )
    )
    assert artifact is not None and artifact.status == "available"
    assert executions == 1


async def test_log_seal_failure_records_degradation_without_rerunning_work(db, monkeypatch):
    job_id, ticket_id = await _canonical_ticket(db)
    executions = 0

    async def handler(execution):
        nonlocal executions
        executions += 1
        return {"outcome": "succeeded", "summary": {}}

    async def fail_seal(log_sink, *, outcome, attempt_outcome=None, summary=None):
        return False

    monkeypatch.setitem(delivery._EXECUTION_HANDLERS, "system_noop", handler)
    monkeypatch.setattr(delivery, "_seal_attempt_log", fail_seal)
    await deliver_control_job(_transport_job(job_id, ticket_id), _context())

    await db.rollback()
    job = await db.get(Job, job_id)
    degradation = await db.scalar(
        select(JobEvent).where(
            JobEvent.job_id == job_id,
            JobEvent.event_key == "log.truncated",
        )
    )
    assert job is not None and (job.phase, job.outcome) == ("terminal", "succeeded")
    assert degradation is not None and degradation.detail == {
        "source": "attempt_log",
        "retryable": True,
        "_canonical_version": 1,
    }
    assert executions == 1


async def test_terminal_durability_precedes_success_log_seal(db, monkeypatch):
    job_id, ticket_id = await _canonical_ticket(db)
    order: list[str] = []
    original_succeed = FencedWriter.succeed

    async def handler(execution):
        return {"outcome": "succeeded", "summary": {"echo": "ordered"}}

    async def record_terminal(self, result, *, decision=None):
        order.append("terminal")
        return await original_succeed(self, result, decision=decision)

    async def record_seal(log_sink, *, outcome, attempt_outcome=None, summary=None):
        order.append(f"seal:{outcome}")

    monkeypatch.setitem(delivery._EXECUTION_HANDLERS, "system_noop", handler)
    monkeypatch.setattr(FencedWriter, "succeed", record_terminal)
    monkeypatch.setattr(delivery, "_seal_attempt_log", record_seal)

    await deliver_control_job(_transport_job(job_id, ticket_id), _context())
    assert order == ["terminal", "seal:succeeded"]


async def test_definition_retry_budget_and_delays_bound_handler_hint(db, monkeypatch):
    job_id, ticket_id = await _canonical_ticket(db)

    async def handler(execution):
        raise RetryRequested(timedelta(seconds=999), "provider unavailable")

    monkeypatch.setitem(delivery._EXECUTION_HANDLERS, "system_noop", handler)
    for attempt_number, expected_delay in enumerate((5, 30)):
        with pytest.raises(RetryRequested) as caught:
            await deliver_control_job(
                _transport_job(job_id, ticket_id, attempts=attempt_number), _context()
            )
        assert caught.value.delay == timedelta(seconds=expected_delay)

    with pytest.raises(Exception) as exhausted:
        await deliver_control_job(_transport_job(job_id, ticket_id, attempts=2), _context())
    assert not isinstance(exhausted.value, RetryRequested)
    await db.rollback()
    job = await db.get(Job, job_id)
    attempts = list(
        await db.scalars(
            select(JobAttempt).where(JobAttempt.job_id == job_id).order_by(JobAttempt.id)
        )
    )
    assert job is not None
    assert (job.phase, job.outcome) == ("terminal", "failed")
    assert [attempt.outcome for attempt in attempts] == ["retrying", "retrying", "failed"]


async def test_definition_timeout_wraps_handler_execution(db, monkeypatch):
    job_id, ticket_id = await _canonical_ticket(db)
    definitions = [
        replace(definition, timeout=TimeoutPolicy(seconds=1))
        if definition.job_type == "system_noop"
        else definition
        for definition in JOB_DEFINITION_REGISTRY
    ]

    async def handler(execution):
        await asyncio.sleep(60)
        return {"outcome": "succeeded", "summary": {}}

    monkeypatch.setattr(delivery, "JOB_DEFINITION_REGISTRY", JobDefinitionRegistry(definitions))
    monkeypatch.setitem(delivery._EXECUTION_HANDLERS, "system_noop", handler)
    with pytest.raises(RetryRequested) as caught:
        await asyncio.wait_for(
            deliver_control_job(_transport_job(job_id, ticket_id), _context()), timeout=1.5
        )
    assert caught.value.delay == timedelta(seconds=5)
    await db.rollback()
    job = await db.get(Job, job_id)
    attempt = await db.scalar(select(JobAttempt).where(JobAttempt.job_id == job_id))
    assert job is not None and attempt is not None
    assert (job.phase, job.outcome, attempt.outcome) == ("queued", None, "retrying")


async def test_retry_execution_reuses_enqueue_time_configuration_snapshot(db, monkeypatch):
    definitions = [
        replace(
            definition,
            configuration_keys=frozenset({"K_NEIGHBORS"}),
            configuration_audit="snapshot",
            retry_policy=RetryPolicy(
                max_attempts=2,
                transient_delays_seconds=(0.01,),
                idempotency_proof="read-only noop",
            ),
        )
        if definition.job_type == "system_noop"
        else definition
        for definition in JOB_DEFINITION_REGISTRY
    ]
    registry = JobDefinitionRegistry(definitions)
    monkeypatch.setattr(submission, "JOB_DEFINITION_REGISTRY", registry)
    monkeypatch.setattr(delivery, "JOB_DEFINITION_REGISTRY", registry)
    snapshot_version = configuration_provider.state.version
    monkeypatch.setattr(
        configuration_provider,
        "snapshot_for",
        lambda _keys: ExecutionConfigurationSnapshot(
            version=snapshot_version, values={"K_NEIGHBORS": 7}
        ),
    )
    job_id, ticket_id = await _canonical_ticket(db)
    monkeypatch.setattr(
        configuration_provider,
        "snapshot_for",
        lambda _keys: ExecutionConfigurationSnapshot(
            version=snapshot_version, values={"K_NEIGHBORS": 99}
        ),
    )
    observed: list[dict[str, object]] = []

    async def handler(execution):
        observed.append(dict(execution.configuration))
        if len(observed) == 1:
            raise RetryRequested(timedelta(seconds=999), "transient")
        return {"outcome": "succeeded", "summary": {}}

    monkeypatch.setitem(delivery._EXECUTION_HANDLERS, "system_noop", handler)
    with pytest.raises(RetryRequested):
        await deliver_control_job(_transport_job(job_id, ticket_id), _context())
    await deliver_control_job(_transport_job(job_id, ticket_id, attempts=1), _context())

    await db.rollback()
    job = await db.get(Job, job_id)
    assert job is not None
    assert observed == [{"K_NEIGHBORS": 7}, {"K_NEIGHBORS": 7}]
    assert (job.configuration_version, job.configuration_snapshot) == (
        snapshot_version,
        {"K_NEIGHBORS": 7},
    )


async def test_kernel_uncertainty_is_held_with_interrupted_attempt(db, monkeypatch):
    job_id, ticket_id = await _canonical_ticket(db)

    async def handler(_execution):
        raise ClassifiedExecutionError(
            "mutation publication is uncertain", RetryClassification.UNSAFE
        )

    monkeypatch.setitem(delivery._EXECUTION_HANDLERS, "system_noop", handler)
    with pytest.raises(DeliveryRejectedError, match="execution safety is uncertain"):
        await deliver_control_job(_transport_job(job_id, ticket_id), _context())

    await db.rollback()
    job = await db.get(Job, job_id)
    attempt = await db.scalar(select(JobAttempt).where(JobAttempt.job_id == job_id))
    dispatch = await db.scalar(select(JobDispatch).where(JobDispatch.job_id == job_id))
    assert job is not None and attempt is not None and dispatch is not None
    assert (job.phase, job.outcome, attempt.outcome, dispatch.disposition) == (
        "terminal",
        "unsafe",
        "interrupted",
        "failed",
    )


@pytest.mark.parametrize("outcome", ["failed", "cancelled"])
async def test_public_retry_uses_definition_entrypoint_and_rejects_stale_replay(
    db,
    outcome: str,
) -> None:
    job_id, _ticket_id = await _canonical_ticket(db)
    job = await db.get(Job, job_id)
    assert job is not None
    job.phase = "terminal"
    job.outcome = outcome
    job.terminal_at = datetime.now(UTC)
    original_fence = job.fence_token
    await db.commit()

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        active = await client.post(
            f"/api/jobs/{job_id}/retry",
            json={"expected_fence_token": original_fence},
        )
        assert active.status_code == 200, active.text
        payload = active.json()
        assert payload["action"] == "retry"
        successor_id = payload["replacement_job_id"]
        assert successor_id and successor_id != job_id

        stale = await client.post(
            f"/api/jobs/{job_id}/retry",
            json={"expected_fence_token": original_fence},
        )
        assert stale.status_code == 409
        assert stale.json()["detail"]["code"] == "stale_job_version"

    await db.rollback()
    db.expire_all()
    original = await db.get(Job, job_id)
    successor = await db.get(Job, successor_id)
    successor_dispatch = await db.scalar(
        select(JobDispatch).where(JobDispatch.job_id == successor_id)
    )
    assert original is not None and successor is not None and successor_dispatch is not None
    assert original.fence_token == original_fence + 1
    assert successor.retry_of_job_id == original.id
    assert successor_dispatch.entrypoint == JOB_DEFINITION_REGISTRY.get(successor.type).entrypoint
    assert successor_dispatch.pgq_job_id is not None


async def test_public_retry_rejects_active_job_without_a_successor(db) -> None:
    job_id, _ticket_id = await _canonical_ticket(db)
    job = await db.get(Job, job_id)
    assert job is not None
    before_count = await db.scalar(select(func.count()).select_from(Job))

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.post(
            f"/api/jobs/{job_id}/retry",
            json={"expected_fence_token": job.fence_token},
        )
    assert response.status_code == 409, response.text

    await db.rollback()
    assert await db.scalar(select(func.count()).select_from(Job)) == before_count


async def test_public_retry_rejects_missing_subject_without_a_successor(db) -> None:
    job_id, _ticket_id = await _canonical_ticket(db)
    job = await db.get(Job, job_id)
    assert job is not None
    job.phase = "terminal"
    job.outcome = "failed"
    job.terminal_at = datetime.now(UTC)
    job.subject_reference = None
    await db.commit()
    before_count = await db.scalar(select(func.count()).select_from(Job))

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.post(
            f"/api/jobs/{job_id}/retry",
            json={"expected_fence_token": job.fence_token},
        )
    assert response.status_code == 409, response.text

    await db.rollback()
    assert await db.scalar(select(func.count()).select_from(Job)) == before_count
