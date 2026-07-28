"""Canonical job batches: fixed and dynamic parents, projection, and parent commands.

A batch parent is ticketless and owns only projection state. Covers atomic fixed batches
with ordered children, dynamic open/append/seal, the locked parent outcome matrix,
compare-and-set projection repair under concurrency, and parent-scoped priority, cancel,
and retry."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from pgqueuer import Queries
from sqlalchemy import func, inspect, select, text

import marquee.core.jobs.batches as batch_module
import marquee.core.jobs.control as control_module
import marquee.core.jobs.pgqueuer_gateway as gateway_module
import marquee.core.jobs.submission as submission_module
from marquee.core.jobs.batches import (
    BatchScope,
    append_dynamic_child,
    create_fixed_batch,
    open_dynamic_batch,
    project_active_child,
    project_batch,
    project_terminal_child,
    seal_dynamic_batch,
)
from marquee.core.jobs.contracts import TriggerKind
from marquee.core.jobs.control import JobControlError, cancel, change_priority, retry, set_paused
from marquee.core.jobs.definitions import JobDefinitionRegistry
from marquee.core.jobs.manifest import JOB_DEFINITION_REGISTRY
from marquee.core.jobs.policies import ParentAggregationPolicy
from marquee.core.jobs.submission import (
    IdempotencyConflictError,
    SubjectLocator,
    SubmissionIntent,
    SubmissionValidationError,
    submit_job,
)
from marquee.database import _get_engine, _get_session_factory
from marquee.main import app
from marquee.models import Job, JobAttempt, JobBatch, JobDispatch, JobEvent


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


@pytest_asyncio.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as value:
        yield value


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


@pytest.fixture
def batch_registry(monkeypatch) -> JobDefinitionRegistry:
    registry = _batch_registry()
    monkeypatch.setattr(batch_module, "JOB_DEFINITION_REGISTRY", registry)
    monkeypatch.setattr(submission_module, "JOB_DEFINITION_REGISTRY", registry)
    monkeypatch.setattr(gateway_module, "JOB_DEFINITION_REGISTRY", registry)
    return registry


def _children(prefix: str, count: int = 3) -> tuple[SubmissionIntent, ...]:
    return tuple(
        SubmissionIntent(
            job_type="system_noop",
            request={"echo": {"ordinal": index}},
            subject=SubjectLocator(kind="system_work", reference="system_noop"),
            trigger=TriggerKind.BATCH,
            initiator=None,
            idempotency_key=f"system_noop:{prefix}-{index}",
            priority=40 + index,
        )
        for index in range(count)
    )


async def _create(db, key: str, children: tuple[SubmissionIntent, ...]):
    return await create_fixed_batch(
        db,
        parent_job_type="poster_pipeline_batch",
        parent_request={"scope": "all", "selection_count": 0},
        scope=BatchScope(
            reference=f"scope:{key.rsplit(':', 1)[-1]}",
            display_name="A2 fixed batch",
            summary="Three deterministic no-op children",
        ),
        trigger=TriggerKind.BATCH,
        initiator=None,
        idempotency_key=key,
        children=children,
    )


@pytest.mark.asyncio
async def test_fixed_batch_is_atomic_ticketless_parent_with_ordered_children(
    db, client, batch_registry
) -> None:
    observer = await _get_engine().connect()
    try:
        transaction = await db.begin()
        result = await _create(
            db,
            "poster_pipeline_batch:fixed-success",
            _children("fixed-success"),
        )
        parent = await db.get(Job, result.parent.job_id)
        projection = await db.get(JobBatch, result.parent.job_id)
        children = [await db.get(Job, child.job_id) for child in result.children]

        assert result.parent.disposition == "created"
        assert result.sealed_child_total == 3
        assert parent is not None and parent.phase == "queued"
        assert parent.pgq_job_id is None and parent.dispatch_generation == 0
        assert parent.current_attempt_id is None
        assert projection is not None
        assert (projection.mode, projection.sealed, projection.sealed_child_total) == (
            "fixed",
            True,
            3,
        )
        assert projection.created_total == 3
        assert projection.terminal_total == 0
        assert all(child is not None and child.pgq_job_id is not None for child in children)
        assert [child.request["echo"]["ordinal"] for child in children] == [0, 1, 2]
        assert all(
            child.parent_id == parent.id
            and child.root_id == parent.id
            and child.correlation_id == parent.id
            for child in children
        )
        assert (
            await db.scalar(
                select(func.count(JobDispatch.id)).where(JobDispatch.job_id == parent.id)
            )
            == 0
        )
        assert (
            await db.scalar(select(func.count(JobAttempt.id)).where(JobAttempt.job_id == parent.id))
            == 0
        )
        assert (
            await observer.scalar(
                text("SELECT count(*) FROM jobs WHERE id = :parent"), {"parent": parent.id}
            )
            == 0
        )
        await transaction.commit()

        batch_response = await client.get(f"/api/jobs/{parent.id}/batch")
        assert batch_response.status_code == 200
        assert batch_response.json() == {
            "job_id": parent.id,
            "mode": "fixed",
            "sealed": True,
            "sealed_at": projection.sealed_at.isoformat().replace("+00:00", "Z"),
            "sealed_child_total": 3,
            "created_total": 3,
            "terminal_total": 0,
            "outcomes": {
                "succeeded": 0,
                "partially_succeeded": 0,
                "no_change": 0,
                "failed": 0,
                "cancelled": 0,
                "superseded": 0,
                "dead_letter": 0,
                "unsafe": 0,
            },
            "failure_summary": None,
            "attention_summary": None,
            "projection_sequence": 1,
            "updated_at": projection.updated_at.isoformat().replace("+00:00", "Z"),
        }
        first_page = await client.get(f"/api/jobs/{parent.id}/children?limit=2")
        assert first_page.status_code == 200
        assert len(first_page.json()["items"]) == 2
        assert first_page.json()["next_cursor"] is not None
        assert all("pgq_job_id" not in item for item in first_page.json()["items"])
    finally:
        await observer.close()


@pytest.mark.asyncio
async def test_empty_fixed_batch_is_terminal_no_change_and_transport_free(
    db, client, batch_registry
) -> None:
    async with db.begin():
        created = await _create(db, "poster_pipeline_batch:empty", ())
    parent = await db.get(Job, created.parent.job_id)
    projection = await db.get(JobBatch, created.parent.job_id)

    assert parent is not None
    assert (parent.phase, parent.outcome, parent.pgq_job_id) == (
        "terminal",
        "no_change",
        None,
    )
    assert parent.terminal_at is not None
    assert parent.attention["message"] == "No matching work was found."
    assert projection is not None and projection.sealed_child_total == 0
    assert await db.scalar(text("SELECT count(*) FROM pgqueuer")) == 0
    assert await db.scalar(select(func.count(JobDispatch.id))) == 0
    assert await db.scalar(select(func.count(JobAttempt.id))) == 0

    response = await client.get(f"/api/jobs/{parent.id}/batch")
    assert response.status_code == 200
    assert response.json()["sealed_child_total"] == 0


@pytest.mark.asyncio
async def test_fixed_batch_exact_retry_reuses_and_semantic_change_conflicts(
    db, batch_registry
) -> None:
    children = _children("fixed-reuse", 2)
    async with db.begin():
        created = await _create(db, "poster_pipeline_batch:reuse", children)
    async with db.begin():
        reused = await _create(db, "poster_pipeline_batch:reuse", children)
    assert reused.parent.job_id == created.parent.job_id
    assert reused.parent.disposition == "reused"
    assert {child.job_id for child in reused.children} == {
        child.job_id for child in created.children
    }
    assert await db.scalar(select(func.count(JobBatch.parent_job_id))) == 1
    assert await db.scalar(text("SELECT count(*) FROM pgqueuer")) == 2
    await db.rollback()

    changed = list(children)
    changed[0] = replace(changed[0], request={"echo": "different"})
    with pytest.raises(SubmissionValidationError, match="different child intent"):
        async with db.begin():
            await _create(
                db,
                "poster_pipeline_batch:reuse",
                tuple(changed),
            )


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_point", ["parent_event", "after_bulk"])
async def test_fixed_batch_failure_rolls_back_parent_projection_children_and_tickets(
    db, batch_registry, monkeypatch, failure_point
) -> None:
    if failure_point == "parent_event":
        original = batch_module.job_event_writer.append

        async def fail_parent_event(*args, **kwargs):
            await original(*args, **kwargs)
            raise RuntimeError("parent event failure")

        monkeypatch.setattr(batch_module.job_event_writer, "append", fail_parent_event)
    else:
        original = batch_module.submit_jobs

        async def fail_after_bulk(*args, **kwargs):
            await original(*args, **kwargs)
            raise RuntimeError("after bulk failure")

        monkeypatch.setattr(batch_module, "submit_jobs", fail_after_bulk)

    with pytest.raises(RuntimeError, match="failure"):
        async with db.begin():
            await _create(
                db,
                f"poster_pipeline_batch:rollback-{failure_point}",
                _children(f"rollback-{failure_point}", 2),
            )
    assert await db.scalar(select(func.count(Job.id))) == 0
    assert await db.scalar(select(func.count(JobBatch.parent_job_id))) == 0
    assert await db.scalar(select(func.count(JobDispatch.id))) == 0
    assert await db.scalar(select(func.count(JobEvent.id))) == 0
    assert await db.scalar(text("SELECT count(*) FROM pgqueuer")) == 0
    assert await db.scalar(text("SELECT count(*) FROM pgqueuer_log")) == 0


@pytest.mark.asyncio
async def test_job_batch_schema_is_strict_and_contains_no_transport_authority(db) -> None:
    connection = await db.connection()

    def inspect_table(sync_connection):
        inspector = inspect(sync_connection)
        return (
            inspector.get_columns("job_batches"),
            inspector.get_check_constraints("job_batches"),
            inspector.get_foreign_keys("job_batches"),
            inspector.get_pk_constraint("job_batches"),
        )

    columns, checks, foreign_keys, primary_key = await connection.run_sync(inspect_table)
    names = [column["name"] for column in columns]
    assert names == list(JobBatch.__table__.columns.keys())
    assert not {
        "pgq_job_id",
        "claim",
        "lease",
        "heartbeat",
        "retry_at",
        "schedule_id",
        "worker_id",
    } & set(names)
    assert {check["name"] for check in checks} == {
        constraint.name
        for constraint in JobBatch.__table__.constraints
        if constraint.__class__.__name__ == "CheckConstraint"
    }
    assert primary_key["constrained_columns"] == ["parent_job_id"]
    assert foreign_keys[0]["referred_table"] == "jobs"
    assert foreign_keys[0]["options"]["ondelete"] == "CASCADE"


def test_batch_projection_model_has_bounded_json_and_counter_surface() -> None:
    columns = JobBatch.__table__.columns
    assert columns.failure_summary.type.python_type is dict
    assert columns.attention_summary.type.python_type is dict
    assert json.loads('{"projection":"semantic-only"}') == {"projection": "semantic-only"}


def _registry(*, fixed: bool, retry_children: str = "failed") -> JobDefinitionRegistry:
    child = replace(
        JOB_DEFINITION_REGISTRY.get("system_noop"),
        trigger_kinds=frozenset({TriggerKind.BATCH}),
    )
    source = JOB_DEFINITION_REGISTRY.get("poster_pipeline_batch")
    parent = replace(
        source,
        parent_policy=ParentAggregationPolicy(fixed_children=fixed, retry_children=retry_children),
        child_job_types=frozenset({"system_noop"}),
    )
    return JobDefinitionRegistry((child, parent))


def _install_registry(monkeypatch, registry: JobDefinitionRegistry) -> None:
    monkeypatch.setattr(batch_module, "JOB_DEFINITION_REGISTRY", registry)
    monkeypatch.setattr(submission_module, "JOB_DEFINITION_REGISTRY", registry)
    monkeypatch.setattr(gateway_module, "JOB_DEFINITION_REGISTRY", registry)
    monkeypatch.setattr(control_module, "JOB_DEFINITION_REGISTRY", registry)


@pytest.fixture
def dynamic_registry(monkeypatch) -> JobDefinitionRegistry:
    registry = _registry(fixed=False)
    _install_registry(monkeypatch, registry)
    return registry


def _child(key: str, ordinal: int = 0) -> SubmissionIntent:
    return SubmissionIntent(
        job_type="system_noop",
        request={"echo": {"ordinal": ordinal}},
        subject=SubjectLocator(kind="system_work", reference="system_noop"),
        trigger=TriggerKind.BATCH,
        initiator=None,
        idempotency_key=f"system_noop:{key}",
        priority=40 + ordinal,
    )


@pytest.mark.parametrize(
    ("outcomes", "parent_cancelled", "expected"),
    [
        (("no_change", "no_change"), False, "no_change"),
        (("succeeded", "no_change"), False, "succeeded"),
        (("cancelled", "cancelled"), True, "cancelled"),
        (("superseded", "superseded"), False, "superseded"),
        (("failed", "dead_letter"), False, "failed"),
        (("failed", "unsafe"), False, "unsafe"),
        (("succeeded", "failed"), False, "partially_succeeded"),
        (("partially_succeeded", "no_change"), False, "partially_succeeded"),
        (("succeeded", "superseded"), False, "partially_succeeded"),
    ],
)
def test_locked_parent_outcome_matrix(outcomes, parent_cancelled, expected) -> None:
    names = (
        "succeeded",
        "partially_succeeded",
        "no_change",
        "failed",
        "cancelled",
        "superseded",
        "dead_letter",
        "unsafe",
    )
    counts = {name: outcomes.count(name) for name in names}
    assert batch_module._aggregate_outcome(counts, parent_cancelled=parent_cancelled) == expected


async def _open(db, key: str):
    return await open_dynamic_batch(
        db,
        parent_job_type="poster_pipeline_batch",
        parent_request={"scope": "all", "selection_count": 0},
        scope=BatchScope(
            reference=f"scope:{key}",
            display_name="A3 dynamic batch",
            summary="Fenced dynamic children",
        ),
        trigger=TriggerKind.BATCH,
        initiator=None,
        idempotency_key=f"poster_pipeline_batch:{key}",
    )


@pytest.mark.asyncio
async def test_dynamic_open_append_retry_and_permanent_seal(db, dynamic_registry) -> None:
    async with db.begin():
        opened = await _open(db, "dynamic-lifecycle")
        independent = await _open(db, "dynamic-lifecycle-independent")
        assert independent.generation != opened.generation
        first = await append_dynamic_child(
            db,
            parent_id=opened.parent.job_id,
            generation=opened.generation,
            child=_child("dynamic-lifecycle-0"),
        )
        repeated = await append_dynamic_child(
            db,
            parent_id=opened.parent.job_id,
            generation=opened.generation,
            child=_child("dynamic-lifecycle-0"),
        )
        assert first.disposition == "created"
        assert repeated == replace(first, disposition="reused", idempotent=True)
        with pytest.raises(IdempotencyConflictError, match="semantic intent"):
            await append_dynamic_child(
                db,
                parent_id=opened.parent.job_id,
                generation=opened.generation,
                child=replace(_child("dynamic-lifecycle-0"), request={"echo": "different"}),
            )
        sealed = await seal_dynamic_batch(
            db, parent_id=opened.parent.job_id, generation=opened.generation
        )
        repeated_seal = await seal_dynamic_batch(
            db, parent_id=opened.parent.job_id, generation=opened.generation
        )
        assert repeated_seal.projection_sequence == sealed.projection_sequence
        with pytest.raises(SubmissionValidationError, match="permanently sealed"):
            await append_dynamic_child(
                db,
                parent_id=opened.parent.job_id,
                generation=opened.generation,
                child=_child("dynamic-lifecycle-late", 1),
            )
        with pytest.raises(SubmissionValidationError, match="generation is stale"):
            await seal_dynamic_batch(
                db, parent_id=opened.parent.job_id, generation=opened.generation + 1
            )

    parent = await db.get(Job, opened.parent.job_id)
    projection = await db.get(JobBatch, opened.parent.job_id)
    assert parent is not None and parent.pgq_job_id is None
    assert parent.current_attempt_id is None and parent.dispatch_generation == 0
    assert projection is not None
    assert (projection.sealed, projection.created_total, projection.sealed_child_total) == (
        True,
        1,
        1,
    )
    assert (
        await db.scalar(select(func.count(JobDispatch.id)).where(JobDispatch.job_id == parent.id))
        == 0
    )
    assert (
        await db.scalar(select(func.count(JobAttempt.id)).where(JobAttempt.job_id == parent.id))
        == 0
    )


@pytest.mark.asyncio
async def test_dynamic_empty_seal_is_no_change_without_transport(db, dynamic_registry) -> None:
    async with db.begin():
        opened = await _open(db, "dynamic-empty")
        result = await seal_dynamic_batch(
            db, parent_id=opened.parent.job_id, generation=opened.generation
        )
    parent = await db.get(Job, opened.parent.job_id)
    assert result.outcome == "no_change"
    assert parent is not None
    assert (parent.phase, parent.outcome, parent.pgq_job_id) == (
        "terminal",
        "no_change",
        None,
    )
    assert await db.scalar(text("SELECT count(*) FROM pgqueuer")) == 0


@pytest.mark.asyncio
async def test_open_batch_cannot_terminalize_and_seal_applies_locked_outcome(
    db, dynamic_registry
) -> None:
    async with db.begin():
        opened = await _open(db, "dynamic-aggregate")
        child_results = []
        for ordinal in range(2):
            child_results.append(
                await append_dynamic_child(
                    db,
                    parent_id=opened.parent.job_id,
                    generation=opened.generation,
                    child=_child(f"dynamic-aggregate-{ordinal}", ordinal),
                )
            )
        first_child = await db.get(Job, child_results[0].job_id)
        assert first_child is not None
        first_child.phase = "running"
        first_child.started_at = datetime.now(UTC)
        await project_active_child(db, first_child)
        parent = await db.get(Job, opened.parent.job_id)
        assert parent is not None
        assert (parent.phase, parent.outcome) == ("running", None)
        assert parent.started_at is not None
        for result, outcome in zip(child_results, ("succeeded", "failed"), strict=True):
            child = await db.get(Job, result.job_id)
            assert child is not None
            child.phase = "terminal"
            child.outcome = outcome
            child.terminal_at = datetime.now(UTC)
            await project_terminal_child(db, child)
        parent = await db.get(Job, opened.parent.job_id)
        assert parent is not None
        assert (parent.phase, parent.outcome) == ("running", None)
        sealed = await seal_dynamic_batch(db, parent_id=parent.id, generation=opened.generation)
        assert (sealed.phase, sealed.outcome) == ("terminal", "partially_succeeded")

    projection = await db.get(JobBatch, opened.parent.job_id)
    assert projection is not None
    assert projection.terminal_total == 2
    assert projection.succeeded_total == 1 and projection.failed_total == 1
    assert projection.failure_summary["items"][0]["outcome"] == "failed"
    assert projection.attention_summary["level"] == "warning"


@pytest.mark.asyncio
async def test_projection_repair_is_compare_and_set_and_event_sparse(db, dynamic_registry) -> None:
    async with db.begin():
        opened = await _open(db, "dynamic-repair")
        child = await append_dynamic_child(
            db,
            parent_id=opened.parent.job_id,
            generation=opened.generation,
            child=_child("dynamic-repair-0"),
        )
        job = await db.get(Job, child.job_id)
        assert job is not None
        job.phase = "terminal"
        job.outcome = "no_change"
        job.terminal_at = datetime.now(UTC)
        await seal_dynamic_batch(db, parent_id=opened.parent.job_id, generation=opened.generation)
        projection = await db.get(JobBatch, opened.parent.job_id)
        assert projection is not None
        projection.terminal_total = 0
        projection.no_change_total = 0
        projection.projection_sequence += 1
        expected = projection.projection_sequence
        repaired = await project_batch(
            db,
            parent_id=opened.parent.job_id,
            expected_projection_sequence=expected,
            repair=True,
        )
        assert repaired.changed and repaired.outcome == "no_change"
        event_total = await db.scalar(
            select(func.count(JobEvent.id)).where(
                JobEvent.job_id == opened.parent.job_id,
                JobEvent.event_key == "batch.repaired",
            )
        )
        unchanged = await project_batch(
            db,
            parent_id=opened.parent.job_id,
            expected_projection_sequence=repaired.projection_sequence,
            repair=True,
        )
        assert not unchanged.changed
        assert (
            await db.scalar(
                select(func.count(JobEvent.id)).where(
                    JobEvent.job_id == opened.parent.job_id,
                    JobEvent.event_key == "batch.repaired",
                )
            )
            == event_total
        )
        with pytest.raises(SubmissionValidationError, match="changed before repair"):
            await project_batch(
                db,
                parent_id=opened.parent.job_id,
                expected_projection_sequence=expected,
                repair=True,
            )


@pytest.mark.asyncio
async def test_concurrent_terminal_projection_counts_each_child_once(db, monkeypatch) -> None:
    registry = _registry(fixed=True)
    _install_registry(monkeypatch, registry)
    async with db.begin():
        created = await create_fixed_batch(
            db,
            parent_job_type="poster_pipeline_batch",
            parent_request={"scope": "all", "selection_count": 0},
            scope=BatchScope(reference="scope:terminal-race", display_name="Terminal race"),
            trigger=TriggerKind.BATCH,
            initiator=None,
            idempotency_key="poster_pipeline_batch:terminal-race",
            children=(_child("terminal-race-0"), _child("terminal-race-1", 1)),
        )

    async def terminalize(job_id: str, outcome: str) -> None:
        factory = _get_session_factory()
        async with factory() as session, session.begin():
            child = await session.scalar(select(Job).where(Job.id == job_id).with_for_update())
            assert child is not None
            child.phase = "terminal"
            child.outcome = outcome
            child.terminal_at = datetime.now(UTC)
            await project_terminal_child(session, child)

    await asyncio.gather(
        terminalize(created.children[0].job_id, "succeeded"),
        terminalize(created.children[1].job_id, "no_change"),
    )
    db.expire_all()
    parent = await db.get(Job, created.parent.job_id)
    projection = await db.get(JobBatch, created.parent.job_id)
    assert parent is not None and (parent.phase, parent.outcome) == (
        "terminal",
        "succeeded",
    )
    assert projection is not None
    assert projection.terminal_total == 2
    assert projection.succeeded_total == 1 and projection.no_change_total == 1


@pytest.mark.asyncio
async def test_concurrent_dynamic_append_and_seal_are_serialized(db, dynamic_registry) -> None:
    async with db.begin():
        opened = await _open(db, "dynamic-race")

    async def append(child: SubmissionIntent) -> str:
        factory = _get_session_factory()
        try:
            async with factory() as session, session.begin():
                result = await append_dynamic_child(
                    session,
                    parent_id=opened.parent.job_id,
                    generation=opened.generation,
                    child=child,
                )
                return result.disposition
        except SubmissionValidationError as exc:
            assert "permanently sealed" in str(exc)
            return "sealed"

    same_key_results = await asyncio.gather(
        append(_child("dynamic-race-same")),
        append(_child("dynamic-race-same")),
    )
    assert sorted(same_key_results) == ["created", "reused"]

    async def seal() -> None:
        factory = _get_session_factory()
        async with factory() as session, session.begin():
            await seal_dynamic_batch(
                session,
                parent_id=opened.parent.job_id,
                generation=opened.generation,
            )

    late_result, _ = await asyncio.gather(
        append(_child("dynamic-race-late", 1)),
        seal(),
    )
    db.expire_all()
    projection = await db.get(JobBatch, opened.parent.job_id)
    assert projection is not None and projection.sealed
    assert projection.sealed_child_total == projection.created_total
    assert projection.created_total == (2 if late_result == "created" else 1)
    assert (
        await db.scalar(select(func.count(Job.id)).where(Job.parent_id == opened.parent.job_id))
        == projection.created_total
    )


@pytest.mark.asyncio
async def test_parent_priority_cancel_and_retry_target_only_direct_children(
    db, monkeypatch
) -> None:
    registry = _registry(fixed=True, retry_children="failed")
    _install_registry(monkeypatch, registry)
    async with db.begin():
        created = await create_fixed_batch(
            db,
            parent_job_type="poster_pipeline_batch",
            parent_request={"scope": "all", "selection_count": 0},
            scope=BatchScope(reference="scope:commands", display_name="Parent commands"),
            trigger=TriggerKind.BATCH,
            initiator=None,
            idempotency_key="poster_pipeline_batch:commands",
            children=(_child("commands-0"), _child("commands-1", 1)),
        )
        cousin = await submit_job(
            db,
            job_type="system_noop",
            request={"echo": "correlation-only"},
            subject=SubjectLocator(kind="system_work", reference="system_noop"),
            trigger=TriggerKind.BATCH,
            initiator=None,
            idempotency_key="system_noop:commands-correlation-only",
            priority=22,
        )
        cousin_job = await db.get(Job, cousin.job_id)
        assert cousin_job is not None
        cousin_job.correlation_id = created.parent.job_id
    changed = await change_priority(
        db, job_id=created.parent.job_id, expected_fence_token=0, priority=73
    )
    assert changed.job.priority == 73
    child_rows = (await db.scalars(select(Job).where(Job.parent_id == created.parent.job_id))).all()
    assert {child.priority for child in child_rows} == {73}
    child_ids = {child.id for child in child_rows}
    await db.rollback()

    with pytest.raises(JobControlError, match="not available"):
        await set_paused(
            db,
            job_id=created.parent.job_id,
            expected_fence_token=1,
            paused=True,
        )

    cancelled = await cancel(db, job_id=created.parent.job_id, expected_fence_token=1)
    assert (cancelled.job.phase, cancelled.job.outcome) == ("terminal", "cancelled")
    await db.rollback()
    cousin_job = await db.get(Job, cousin.job_id)
    assert cousin_job is not None
    assert (cousin_job.phase, cousin_job.desired_state, cousin_job.priority) == (
        "queued",
        "run",
        22,
    )
    await db.rollback()
    successor = await retry(db, job_id=created.parent.job_id, expected_fence_token=2)
    assert successor.original_job_id == created.parent.job_id
    replacement = await db.get(Job, successor.replacement_job_id)
    assert replacement is not None
    assert replacement.retry_of_job_id == created.parent.job_id
    replacement_children = (
        await db.scalars(select(Job).where(Job.parent_id == replacement.id))
    ).all()
    assert len(replacement_children) == 2
    assert {child.retry_of_job_id for child in replacement_children} == child_ids
