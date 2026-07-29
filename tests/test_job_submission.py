"""A1 canonical single/bulk submission contracts on PostgreSQL."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from pgqueuer import Queries
from sqlalchemy import func, select, text

import marquee.core.jobs.pgqueuer_gateway as gateway_module
import marquee.core.jobs.submission as submission_module
from marquee.core.jobs.contracts import TriggerKind
from marquee.core.jobs.definitions import JobDefinitionRegistry
from marquee.core.jobs.manifest import JOB_DEFINITION_REGISTRY
from marquee.core.jobs.pgqueuer_gateway import pgqueuer_gateway
from marquee.core.jobs.subjects import PosterSubjectGroupSnapshot
from marquee.core.jobs.submission import (
    ActiveOverlapConflictError,
    IdempotencyConflictError,
    ParentBinding,
    SubjectLocator,
    SubmissionIntent,
    SubmissionValidationError,
    submit_job,
    submit_jobs,
)
from marquee.database import _get_engine, _get_session_factory
from marquee.models import Job, JobDispatch, JobEvent, Movie


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


async def _submit_noop(session, key: str, echo: object = "ready"):
    return await submit_job(
        session,
        job_type="system_noop",
        request={"echo": echo},
        subject=SubjectLocator(kind="system_work", reference="system_noop"),
        trigger=TriggerKind.SYSTEM,
        initiator=None,
        idempotency_key=key,
    )


@pytest.mark.asyncio
async def test_poster_group_submission_resolves_each_member_from_live_state(db) -> None:
    transaction = await db.begin()
    movie = Movie(
        title="Arrival",
        year=2016,
        folder_path="/private/movies/Arrival",
        movie_file_path="/private/movies/Arrival/Arrival.mkv",
    )
    db.add(movie)
    await db.flush()

    result = await submit_job(
        db,
        job_type="poster_pipeline_group",
        request={
            "library": "movies",
            "chunk_index": 3,
            "members": [{"movie_id": movie.id, "title": "Stale caller title"}],
        },
        subject=SubjectLocator(kind="poster_subject_group", reference="batch-test-003"),
        trigger=TriggerKind.MANUAL,
        initiator=None,
        idempotency_key="poster_pipeline_group:batch-test-003",
    )

    job = await db.get(Job, result.job_id)
    assert job is not None
    snapshot = PosterSubjectGroupSnapshot.model_validate(job.subject_snapshot)
    assert snapshot.library == "movies"
    assert snapshot.chunk_index == 3
    assert snapshot.members[0].subject_key == f"movie:{movie.id}"
    assert snapshot.members[0].subject.display_name == "Arrival"
    assert "/private/" not in str(job.subject_snapshot)
    await transaction.rollback()


@pytest.mark.asyncio
async def test_submit_job_requires_caller_transaction_and_returns_api_safe_links(db) -> None:
    with pytest.raises(SubmissionValidationError, match="active transaction"):
        await _submit_noop(db, "system_noop:no-transaction")

    observer = await _get_engine().connect()
    try:
        transaction = await db.begin()
        result = await _submit_noop(db, "system_noop:caller-owned")
        job = await db.get(Job, result.job_id)

        assert result.disposition == "created"
        assert result.phase == "queued"
        assert result.snapshot_link == f"/api/jobs/{result.job_id}/snapshot"
        assert result.detail_link == f"/projection-room/jobs/{result.job_id}"
        assert result.activity_link == f"/projection-room?view=queue&job={result.job_id}"
        assert result.idempotent is False
        assert not hasattr(result, "pgq_job_id")
        assert job is not None and job.pgq_job_id is not None
        assert job.root_id == job.correlation_id == job.id
        assert job.plan == {
            "version": 1,
            "entrypoint": "control",
            "execution_class": "control",
            "timeout_seconds": 30,
            "effect_safety": "read_only",
            "presenter_key": "jobs.system_noop",
            "progress_policy": {
                "strategy": "none",
                "overall_unit": None,
                "denominator_source": "none",
                "current_unit": None,
                "aggregation_strategy": "none",
                "stages": [["execute", "jobs.system_noop.progress.execute"]],
                "tool_adapter": None,
                "persistence_cadence_seconds": 5,
                "meaningful_delta_percent": None,
                "max_snapshot_staleness_seconds": 15,
                "eta_capability": False,
                "eta_requires_rate": True,
                "allow_concurrent_subjects": False,
            },
            "action_policy": {
                "cancel": True,
                "pause": False,
                "change_priority": True,
                "retry": True,
                "logs": False,
                "artifacts": False,
                "detail": True,
            },
            "overlap_policy": {
                "mode": "allow",
                "include_configuration": True,
                "include_parent_scope": True,
            },
        }
        assert job.retry_policy == {
            "max_attempts": 3,
            "transient_delays_seconds": [5, 30],
            "idempotency_proof": None,
        }
        assert job.execution_policy_id == "definition:system_noop:v1"
        assert job.priority == JOB_DEFINITION_REGISTRY.get("system_noop").default_priority
        assert JOB_DEFINITION_REGISTRY.get("system_noop").default_eligibility_delay_seconds == 0
        assert (
            await observer.scalar(
                text("SELECT count(*) FROM jobs WHERE id = :job_id"), {"job_id": job.id}
            )
            == 0
        )
        assert (
            await observer.scalar(
                text("SELECT count(*) FROM pgqueuer WHERE id = :ticket"),
                {"ticket": job.pgq_job_id},
            )
            == 0
        )

        await transaction.commit()
        assert (
            await observer.scalar(
                text("SELECT count(*) FROM jobs WHERE id = :job_id"), {"job_id": job.id}
            )
            == 1
        )
        payload = await observer.scalar(
            text("SELECT payload FROM pgqueuer WHERE id = :ticket"),
            {"ticket": job.pgq_job_id},
        )
        assert set(json.loads(bytes(payload))) == {
            "job_id",
            "payload_version",
            "dispatch_generation",
        }
    finally:
        await observer.close()


@pytest.mark.asyncio
async def test_submit_job_caller_rollback_removes_canonical_and_transport_rows(db) -> None:
    transaction = await db.begin()
    result = await _submit_noop(db, "system_noop:caller-rollback")
    await transaction.rollback()

    assert await db.scalar(select(func.count(Job.id)).where(Job.id == result.job_id)) == 0
    assert await db.scalar(select(func.count(JobDispatch.id))) == 0
    assert await db.scalar(select(func.count(JobEvent.id))) == 0
    assert await db.scalar(text("SELECT count(*) FROM pgqueuer")) == 0
    assert await db.scalar(text("SELECT count(*) FROM pgqueuer_log")) == 0


@pytest.mark.asyncio
async def test_submit_job_reuses_exact_intent_and_types_semantic_conflict(db) -> None:
    async with db.begin():
        first = await _submit_noop(db, "system_noop:typed-idempotency", {"b": 2, "a": 1})
    async with db.begin():
        reused = await _submit_noop(db, "system_noop:typed-idempotency", {"a": 1, "b": 2})
    assert reused.job_id == first.job_id
    assert reused.disposition == "reused"

    with pytest.raises(IdempotencyConflictError, match="semantic intent"):
        async with db.begin():
            await _submit_noop(db, "system_noop:typed-idempotency", "different")


@pytest.mark.asyncio
async def test_submit_job_serializes_concurrent_canonical_idempotency() -> None:
    factory = _get_session_factory()

    async def create():
        async with factory() as session, session.begin():
            return await _submit_noop(session, "system_noop:direct-race", "same")

    first, second = await asyncio.gather(create(), create())
    assert first.job_id == second.job_id
    assert {first.disposition, second.disposition} == {"created", "reused"}
    async with factory() as session:
        assert (
            await session.scalar(
                select(func.count(Job.id)).where(Job.idempotency_key == "system_noop:direct-race")
            )
            == 1
        )
        assert await session.scalar(text("SELECT count(*) FROM pgqueuer")) == 1


@pytest.mark.asyncio
async def test_submission_rejects_disabled_webhook_and_policy_injection_without_leaks(db) -> None:
    async with db.begin():
        with pytest.raises(SubmissionValidationError, match="not enabled"):
            await submit_job(
                db,
                job_type="poster_heal",
                request={},
                subject=SubjectLocator(kind="movie", reference="1"),
                trigger=TriggerKind.MANUAL,
                initiator=None,
                idempotency_key="poster_heal:disabled",
            )
        with pytest.raises(SubmissionValidationError, match="webhook"):
            await submit_job(
                db,
                job_type="system_noop",
                request={"echo": "safe"},
                subject=SubjectLocator(kind="system_work", reference="system_noop"),
                trigger=TriggerKind.WEBHOOK,
                initiator=None,
                idempotency_key="system_noop:webhook",
            )
        with pytest.raises(SubmissionValidationError, match="job request is invalid") as error:
            await submit_job(
                db,
                job_type="system_noop",
                request={"echo": "safe", "execution_policy_id": "client-secret-value"},
                subject=SubjectLocator(kind="system_work", reference="system_noop"),
                trigger=TriggerKind.SYSTEM,
                initiator=None,
                idempotency_key="system_noop:policy-injection",
            )
        assert "client-secret-value" not in str(error.value)
    assert await db.scalar(select(func.count(Job.id))) == 0


def _batch_registry() -> JobDefinitionRegistry:
    child = replace(
        JOB_DEFINITION_REGISTRY.get("system_noop"),
        trigger_kinds=frozenset({TriggerKind.BATCH}),
    )
    parent = replace(
        JOB_DEFINITION_REGISTRY.get("poster_pipeline_batch"),
        child_job_types=frozenset({"system_noop"}),
    )
    return JobDefinitionRegistry((child, parent))


def _parent(parent_id: str) -> Job:
    now = datetime.now(UTC)
    return Job(
        id=parent_id,
        type="poster_pipeline_batch",
        request={},
        phase="queued",
        desired_state="run",
        priority=50,
        eligible_at=now,
        dispatch_generation=0,
        root_id=parent_id,
        correlation_id=f"batch:{parent_id}",
        trigger_kind="batch",
        feature_area="ai_posters",
        presentation_family="ai_posters",
        subject_kind="aggregate_batch",
        subject_reference=parent_id,
        subject_snapshot={
            "version": 1,
            "kind": "aggregate_batch",
            "display_id": f"batch:{parent_id}",
            "display_name": "A1 test batch",
            "snapshot_at": now.isoformat(),
            "batch_type": "a1_test",
            "sealed": True,
        },
        queued_at=now,
    )


@pytest.mark.asyncio
async def test_submit_jobs_bulk_links_ordered_children_and_rolls_back_as_one_unit(
    db, monkeypatch
) -> None:
    registry = _batch_registry()
    monkeypatch.setattr(submission_module, "JOB_DEFINITION_REGISTRY", registry)
    monkeypatch.setattr(gateway_module, "JOB_DEFINITION_REGISTRY", registry)
    parent = _parent("a1batchparent000000000000000001")
    original_enqueue_many = pgqueuer_gateway.enqueue_many
    calls = 0

    async def fail_after_enqueue(*args, **kwargs):
        nonlocal calls
        calls += 1
        result = await original_enqueue_many(*args, **kwargs)
        raise RuntimeError(f"rollback {result}")

    monkeypatch.setattr(pgqueuer_gateway, "enqueue_many", fail_after_enqueue)
    intents = tuple(
        SubmissionIntent(
            job_type="system_noop",
            request={"echo": index},
            subject=SubjectLocator(kind="system_work", reference="system_noop"),
            trigger=TriggerKind.BATCH,
            initiator=None,
            idempotency_key=f"system_noop:a1-bulk-{index}",
            parent=ParentBinding(
                parent_id=parent.id,
                root_id=parent.root_id,
                correlation_id=parent.correlation_id,
            ),
        )
        for index in range(3)
    )

    with pytest.raises(RuntimeError, match="rollback"):
        async with db.begin():
            db.add(parent)
            await db.flush()
            await submit_jobs(db, intents=intents)
    assert calls == 1
    assert await db.scalar(select(func.count(Job.id))) == 0
    assert await db.scalar(text("SELECT count(*) FROM pgqueuer")) == 0
    assert await db.scalar(text("SELECT count(*) FROM pgqueuer_log")) == 0


@pytest.mark.asyncio
async def test_submit_jobs_bulk_success_preserves_input_and_ticket_order(db, monkeypatch) -> None:
    registry = _batch_registry()
    monkeypatch.setattr(submission_module, "JOB_DEFINITION_REGISTRY", registry)
    monkeypatch.setattr(gateway_module, "JOB_DEFINITION_REGISTRY", registry)
    parent = _parent("a1batchparent000000000000000002")
    intents = tuple(
        SubmissionIntent(
            job_type="system_noop",
            request={"echo": index},
            subject=SubjectLocator(kind="system_work", reference="system_noop"),
            trigger=TriggerKind.BATCH,
            initiator=None,
            idempotency_key=f"system_noop:a1-success-{index}",
            priority=40 + index,
            parent=ParentBinding(parent_id=parent.id),
        )
        for index in range(3)
    )

    async with db.begin():
        db.add(parent)
        await db.flush()
        results = await submit_jobs(db, intents=intents)
        jobs = [await db.get(Job, result.job_id) for result in results]
        rows = (
            await db.execute(
                text(
                    "SELECT id, priority, payload FROM pgqueuer "
                    "WHERE id = ANY(:ids) ORDER BY array_position(:ids, id)"
                ),
                {"ids": [job.pgq_job_id for job in jobs]},
            )
        ).all()

    assert [result.disposition for result in results] == ["created"] * 3
    assert [job.request["echo"] for job in jobs] == [0, 1, 2]
    assert [row.priority for row in rows] == [40, 41, 42]
    assert [json.loads(bytes(row.payload))["job_id"] for row in rows] == [
        result.job_id for result in results
    ]


@pytest.mark.asyncio
async def test_submit_jobs_rejects_empty_duplicates_and_unbound_children(db) -> None:
    async with db.begin():
        assert await submit_jobs(db, intents=(), allow_empty=True) == ()
        unbound = SubmissionIntent(
            job_type="system_noop",
            request={"echo": "x"},
            subject=SubjectLocator(kind="system_work", reference="system_noop"),
            trigger=TriggerKind.SYSTEM,
            initiator=None,
            idempotency_key="system_noop:unbound",
        )
        with pytest.raises(SubmissionValidationError, match="common parent"):
            await submit_jobs(db, intents=(unbound,))
        bound = replace(unbound, parent=ParentBinding(parent_id="missing"))
        with pytest.raises(SubmissionValidationError, match="duplicate idempotency"):
            await submit_jobs(db, intents=(bound, bound))


@pytest.mark.asyncio
async def test_active_overlap_coalesces_equivalent_requests_with_different_client_keys(db) -> None:
    factory = _get_session_factory()

    async def create(key: str):
        async with factory() as session, session.begin():
            return await submit_job(
                session,
                job_type="taste_map",
                request={"library": "movies", "expected_generation": 0, "seed": 0},
                subject=SubjectLocator(
                    kind="model_profile_training",
                    reference="taste_map:movies",
                ),
                trigger=TriggerKind.MANUAL,
                initiator=None,
                idempotency_key=key,
            )

    first, second = await asyncio.gather(
        create("taste_map:overlap-a"),
        create("taste_map:overlap-b"),
    )

    assert first.job_id == second.job_id
    assert {first.disposition, second.disposition} == {"created", "reused"}
    assert {first.idempotent, second.idempotent} == {False, True}
    async with factory() as session:
        assert (
            await session.scalar(
                select(func.count(Job.id)).where(
                    Job.type == "taste_map",
                    Job.subject_reference == "taste_map:movies",
                )
            )
            == 1
        )


@pytest.mark.asyncio
async def test_active_overlap_rejects_conflicting_unsafe_request(db) -> None:
    async with db.begin():
        active = await submit_job(
            db,
            job_type="backup_create",
            request={"reason": "manual"},
            subject=SubjectLocator(
                kind="maintenance_scope",
                reference="backup-create",
            ),
            trigger=TriggerKind.MANUAL,
            initiator=None,
            idempotency_key="backup_create:overlap-a",
        )

    with pytest.raises(ActiveOverlapConflictError) as raised:
        async with db.begin():
            await submit_job(
                db,
                job_type="backup_create",
                request={"reason": "pre_maintenance"},
                subject=SubjectLocator(
                    kind="maintenance_scope",
                    reference="backup-create",
                ),
                trigger=TriggerKind.MANUAL,
                initiator=None,
                idempotency_key="backup_create:overlap-b",
            )

    assert raised.value.active_job_id == active.job_id
    assert raised.value.snapshot_link == f"/api/jobs/{active.job_id}/snapshot"
    assert raised.value.detail_link == f"/projection-room/jobs/{active.job_id}"


@pytest.mark.asyncio
async def test_terminal_job_releases_active_overlap_scope(db) -> None:
    async with db.begin():
        first = await submit_job(
            db,
            job_type="backup_create",
            request={"reason": "manual"},
            subject=SubjectLocator(kind="maintenance_scope", reference="backup-create"),
            trigger=TriggerKind.MANUAL,
            initiator=None,
            idempotency_key="backup_create:terminal-release-a",
        )
        job = await db.get(Job, first.job_id)
        assert job is not None
        job.phase = "terminal"
        job.outcome = "succeeded"
        job.terminal_at = datetime.now(UTC)

    async with db.begin():
        successor = await submit_job(
            db,
            job_type="backup_create",
            request={"reason": "pre_maintenance"},
            subject=SubjectLocator(kind="maintenance_scope", reference="backup-create"),
            trigger=TriggerKind.MANUAL,
            initiator=None,
            idempotency_key="backup_create:terminal-release-b",
        )

    assert successor.job_id != first.job_id
    assert successor.disposition == "created"
