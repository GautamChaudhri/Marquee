from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import anyio
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from pgqueuer import PgQueuer, Queries, RetryRequested
from pgqueuer.models import Context
from pgqueuer.models import Job as PgQueuerJob
from pgqueuer.types import QueueExecutionMode
from sqlalchemy import select

from marquee.core.jobs import delivery
from marquee.core.jobs.commands import create_system_noop
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
from marquee.core.jobs.pgqueuer_gateway import pgqueuer_gateway
from marquee.core.jobs.pgqueuer_scheduler import create_scheduler
from marquee.core.jobs.pgqueuer_worker import create_worker
from marquee.core.jobs.process_identity import IdentityStatus, read_boot_id
from marquee.core.jobs.progress_service import progress_writer
from marquee.core.jobs.safety_gates import SafetyGateService, SafetyRequirements
from marquee.database import _get_engine
from marquee.main import app
from marquee.models import RuntimeInstance
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
        payload=payload or {"echo": "jmc1"},
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


def _transport_job(job_id: str, pgq_job_id: int, *, attempts: int = 0) -> PgQueuerJob:
    now = datetime.now(UTC)
    return PgQueuerJob(
        id=pgq_job_id,
        priority=50,
        created=now,
        updated=now,
        heartbeat=now,
        execute_after=now,
        status="picked",
        entrypoint="control",
        payload=(
            f'{{"dispatch_generation":1,"job_id":"{job_id}","payload_version":1}}'.encode()
        ),
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
    attempts = list(
        await db.scalars(select(JobAttempt).where(JobAttempt.job_id == job_id))
    )
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
        {"outcome": "succeeded", "message": None, "summary": {"echo": "jmc1"}},
    )
    assert len(attempts) == 1
    assert (attempts[0].phase, attempts[0].outcome) == ("finished", "succeeded")
    assert job.progress_sequence == 2
    assert job.progress["freshness"] == "terminal"
    assert [event.detail["progress_sequence"] for event in progress_events] == [1, 2]


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
    task = asyncio.create_task(
        deliver_control_job(_transport_job(job_id, ticket_id), context)
    )
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
    attempts = list(
        await db.scalars(select(JobAttempt).where(JobAttempt.job_id == job_id))
    )
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
    task = asyncio.create_task(
        deliver_control_job(_transport_job(job_id, ticket_id), _context())
    )
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
    disposition = await writer.succeed(
        {"outcome": "succeeded", "summary": {"echo": "stale"}}
    )
    assert disposition == WriteDisposition.STALE

    release.set()
    await task
    await db.rollback()
    job = await db.get(Job, job_id)
    assert job is not None and (job.phase, job.outcome) == ("running", None)


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
    task = asyncio.create_task(
        deliver_control_job(_transport_job(job_id, ticket_id), _context())
    )
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
    attempts = list(
        await db.scalars(select(JobAttempt).where(JobAttempt.job_id == job_id))
    )
    assert job is not None
    assert (job.phase, job.desired_state, job.pgq_job_id) == ("queued", "pause", None)
    assert attempts == []


async def test_serial_redelivery_after_terminal_commit_is_noop(db):
    job_id, ticket_id = await _canonical_ticket(db)
    delivery = _transport_job(job_id, ticket_id)
    await deliver_control_job(delivery, _context())
    await deliver_control_job(delivery, _context())

    await db.rollback()
    attempts = list(
        await db.scalars(select(JobAttempt).where(JobAttempt.job_id == job_id))
    )
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
    attempts = list(
        await db.scalars(select(JobAttempt).where(JobAttempt.job_id == job_id))
    )
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
    first = asyncio.create_task(
        deliver_control_job(_transport_job(job_id, ticket_id), _context())
    )
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
            select(JobAttempt)
            .where(JobAttempt.job_id == job_id)
            .order_by(JobAttempt.number)
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
) -> tuple[str, int, int]:
    job_id, ticket_id = await _canonical_ticket(db)
    now = datetime.now(UTC)
    runtime_id = str(uuid4())
    runtime = RuntimeInstance(
        id=runtime_id,
        role="worker",
        node_label="remote-runtime",
        build="jmc6d-test",
        host_boot_id=read_boot_id() if with_process_identity else "remote-boot",
        process_id=42420,
        process_start_ticks=202,
        process_group_id=42420,
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
        process_id=42420 if with_process_identity else None,
        process_group_id=42420 if with_process_identity else None,
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

    assert counts == {"active": 1, "interrupted": 0, "unsafe": 0, "stale": 0}
    await db.rollback()
    job = await db.get(Job, job_id)
    attempt = await db.get(JobAttempt, attempt_id)
    assert job is not None and attempt is not None
    assert (job.phase, attempt.phase) == ("running", "running")


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
        await reconcile_candidate(candidate, cooperative_seconds=0.01, term_seconds=0.01)
        == "stale"
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
            select(JobAttempt)
            .where(JobAttempt.job_id == job_id)
            .order_by(JobAttempt.number)
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
        async with db.begin():
            await pgqueuer_gateway.cancel_known_ticket(db, job_id=job_id)

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
    assert (job.desired_state, job.phase, job.outcome) == (
        "cancel",
        "terminal",
        "cancelled",
    )
    assert await installed_pgqueuer.job_status([ticket_id]) == [(ticket_id, "canceled")]


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
            channel="ch_jmc1_notifications_intentionally_missing",
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
        b'{"dispatch_generation":1,"job_id":"00000000000000000000000000000000",'
        b'"payload_version":1}'
    )
    ticket_ids = await installed_pgqueuer.enqueue(
        "control",
        payload,
        dedupe_key=f"jmc1-rejected-{uuid4().hex}",
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

            @app.schedule("jmc1_test_schedule", "*/1 * * * * *")
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
    assert [row.entrypoint for row in rows].count("jmc1_test_schedule") == 1
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
