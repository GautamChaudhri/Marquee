"""B1 canonical planned/confirmed mutation contracts on PostgreSQL.

These prove the transport-free ``planned`` phase, exactly-once confirmation
dispatch, and every typed conflict that must refuse to publish against drifted
media.  They use an already-certified enabled leaf; JMC5B's own leaves are
enabled and exercised from B2 onwards.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from pgqueuer import Queries
from sqlalchemy import func, select

from marquee.core.jobs.mutation_documents import MutationTargetV1
from marquee.core.jobs.mutation_planning import (
    MutationPlan,
    PlanConflictError,
    PlanValidationError,
    confirm_mutation,
    plan_mutation,
    plan_version,
)
from marquee.core.jobs.submission import Initiator, SubjectLocator
from marquee.database import _get_engine
from marquee.models import Job, JobDispatch, JobEvent, Movie
from marquee.models.job_evidence import MediaOperationDetail

SIGNATURE = "sha256:aaaa"


@pytest_asyncio.fixture(autouse=True)
async def installed_pgqueuer(db) -> Queries:
    async with _get_engine().connect() as connection:
        raw = await connection.get_raw_connection()
        queries = Queries.from_asyncpg_connection(raw.driver_connection)
        await queries.install()
        await db.begin()
        try:
            yield queries
        finally:
            await db.rollback()
            await queries.uninstall()


def _target(key: str, operation: str = "backup") -> MutationTargetV1:
    return MutationTargetV1(
        key=key,
        kind="poster",
        label=f"Poster {key}",
        operation=operation,
        selector_facts={"language": "eng"},
    )


async def _movie(db, radarr_id: int = 5001) -> Movie:
    movie = Movie(title="Planned Movie", year=2026, radarr_id=radarr_id, folder_path="/m/planned")
    db.add(movie)
    await db.flush()
    return movie


async def _plan(db, movie: Movie, key: str, *, ttl: timedelta = timedelta(minutes=15)):
    return await plan_mutation(
        db,
        job_type="poster_backup_subject",
        request={"target_kind": "movie", "target_id": movie.id, "retention": "recoverable_artwork"},
        subject=SubjectLocator(kind="movie", reference=str(movie.id)),
        initiator=Initiator(kind="user", identifier="operator"),
        idempotency_key=key,
        plan=MutationPlan(
            operation_kind="poster_backup",
            media_file_id=None,
            media_snapshot={"movie_id": movie.id},
            before_targets=(_target("before:1"),),
            requested_targets=(_target("poster:1"),),
            expected_targets=(_target("poster:1"),),
            input_signature=SIGNATURE,
            confirmation_requirements={"acknowledge_overwrite": True},
            ttl=ttl,
        ),
    )


@pytest.mark.asyncio
async def test_plan_creates_transport_free_job_with_immutable_documents(db) -> None:
    movie = await _movie(db)
    result = await _plan(db, movie, "poster_backup_subject:plan-1")

    assert result.disposition == "created"
    assert result.phase == "planned"
    job = await db.get(Job, result.job_id)
    assert job.phase == "planned"
    assert job.dispatch_generation == 0
    assert job.planned_at is not None
    assert job.queued_at is None

    # No transport authority exists for an unconfirmed plan.
    dispatches = await db.scalar(
        select(func.count()).select_from(JobDispatch).where(JobDispatch.job_id == job.id)
    )
    assert dispatches == 0
    tickets = await db.scalar(select(func.count()).select_from(Job).where(Job.pgq_job_id.isnot(None)))
    assert tickets == 0

    detail = await db.get(MediaOperationDetail, job.id)
    assert detail.input_signature == SIGNATURE
    assert detail.plan_expires_at > datetime.now(UTC)
    assert detail.confirmation is None
    assert [t["key"] for t in detail.requested_target["targets"]] == ["poster:1"]
    assert [t["key"] for t in detail.expected_target["targets"]] == ["poster:1"]
    assert [t["key"] for t in detail.target_snapshot["before"]] == ["before:1"]

    events = (
        await db.scalars(select(JobEvent).where(JobEvent.job_id == job.id).order_by(JobEvent.id))
    ).all()
    assert [event.event_key for event in events] == ["job.planned"]


@pytest.mark.asyncio
async def test_plan_requires_targets_signature_and_bounded_ttl(db) -> None:
    movie = await _movie(db, radarr_id=5002)
    with pytest.raises(PlanValidationError, match="at least one target"):
        await plan_mutation(
            db,
            job_type="poster_backup_subject",
            request={"target_kind": "movie", "target_id": movie.id, "retention": "recoverable_artwork"},
            subject=SubjectLocator(kind="movie", reference=str(movie.id)),
            initiator=None,
            idempotency_key="poster_backup_subject:no-targets",
            plan=MutationPlan(
                operation_kind="poster_backup",
                media_file_id=None,
                media_snapshot={},
                before_targets=(),
                requested_targets=(),
                expected_targets=(),
                input_signature=SIGNATURE,
            ),
        )
    with pytest.raises(PlanValidationError, match="ttl"):
        await _plan(db, movie, "poster_backup_subject:bad-ttl", ttl=timedelta(days=2))


@pytest.mark.asyncio
async def test_repeated_compatible_plan_is_idempotent(db) -> None:
    movie = await _movie(db, radarr_id=5003)
    first = await _plan(db, movie, "poster_backup_subject:plan-idem")
    second = await _plan(db, movie, "poster_backup_subject:plan-idem")
    assert second.disposition == "reused"
    assert second.job_id == first.job_id
    assert (
        await db.scalar(select(func.count()).select_from(MediaOperationDetail)) == 1
    )


@pytest.mark.asyncio
async def test_confirmation_dispatches_exactly_once_and_records_provenance(db) -> None:
    movie = await _movie(db, radarr_id=5004)
    planned = await _plan(db, movie, "poster_backup_subject:confirm-1")
    detail = await db.get(MediaOperationDetail, planned.job_id)
    version = plan_version(detail)

    confirmed = await confirm_mutation(
        db,
        job_id=planned.job_id,
        expected_plan_version=version,
        current_input_signature=SIGNATURE,
        confirmed_by=Initiator(kind="user", identifier="operator"),
    )
    assert confirmed.disposition == "created"

    job = await db.get(Job, planned.job_id, populate_existing=True)
    assert job.phase == "queued"
    assert job.dispatch_generation == 1
    assert job.retry_policy is not None
    dispatches = (
        await db.scalars(select(JobDispatch).where(JobDispatch.job_id == job.id))
    ).all()
    assert len(dispatches) == 1
    assert dispatches[0].generation == 1

    await db.refresh(detail)
    assert detail.confirmation["plan_version"] == version
    assert detail.confirmation["confirmed_by"]["identifier"] == "operator"
    # The requested operation is replayed verbatim; confirmation never rewrites it.
    assert detail.requested_target["targets"][0]["key"] == "poster:1"
    assert job.request["target_id"] == movie.id

    # Replaying a confirmation must not create a second dispatch.
    again = await confirm_mutation(
        db,
        job_id=planned.job_id,
        expected_plan_version=version,
        current_input_signature=SIGNATURE,
        confirmed_by=None,
    )
    assert again.disposition == "reused"
    assert (
        await db.scalar(
            select(func.count()).select_from(JobDispatch).where(JobDispatch.job_id == job.id)
        )
        == 1
    )


@pytest.mark.asyncio
async def test_stale_plan_version_refuses_confirmation(db) -> None:
    movie = await _movie(db, radarr_id=5005)
    planned = await _plan(db, movie, "poster_backup_subject:stale")
    with pytest.raises(PlanConflictError) as excinfo:
        await confirm_mutation(
            db,
            job_id=planned.job_id,
            expected_plan_version="0" * 64,
            current_input_signature=SIGNATURE,
            confirmed_by=None,
        )
    assert excinfo.value.reason == "stale_plan"
    job = await db.get(Job, planned.job_id, populate_existing=True)
    assert job.phase == "planned"
    assert job.dispatch_generation == 0


@pytest.mark.asyncio
async def test_changed_source_signature_refuses_confirmation(db) -> None:
    movie = await _movie(db, radarr_id=5006)
    planned = await _plan(db, movie, "poster_backup_subject:signature")
    detail = await db.get(MediaOperationDetail, planned.job_id)
    with pytest.raises(PlanConflictError) as excinfo:
        await confirm_mutation(
            db,
            job_id=planned.job_id,
            expected_plan_version=plan_version(detail),
            current_input_signature="sha256:changed",
            confirmed_by=None,
        )
    assert excinfo.value.reason == "signature_changed"
    job = await db.get(Job, planned.job_id, populate_existing=True)
    assert job.dispatch_generation == 0


@pytest.mark.asyncio
async def test_expired_plan_refuses_confirmation(db) -> None:
    movie = await _movie(db, radarr_id=5007)
    planned = await _plan(db, movie, "poster_backup_subject:expired")
    detail = await db.get(MediaOperationDetail, planned.job_id)
    version = plan_version(detail)
    detail.plan_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    await db.flush()

    with pytest.raises(PlanConflictError) as excinfo:
        await confirm_mutation(
            db,
            job_id=planned.job_id,
            expected_plan_version=version,
            current_input_signature=SIGNATURE,
            confirmed_by=None,
        )
    assert excinfo.value.reason == "expired"
    job = await db.get(Job, planned.job_id, populate_existing=True)
    assert job.phase == "planned"
    assert job.dispatch_generation == 0


@pytest.mark.asyncio
async def test_cancelled_plan_refuses_confirmation(db) -> None:
    movie = await _movie(db, radarr_id=5008)
    planned = await _plan(db, movie, "poster_backup_subject:cancelled")
    detail = await db.get(MediaOperationDetail, planned.job_id)
    version = plan_version(detail)
    job = await db.get(Job, planned.job_id)
    job.desired_state = "cancel"
    await db.flush()

    with pytest.raises(PlanConflictError) as excinfo:
        await confirm_mutation(
            db,
            job_id=planned.job_id,
            expected_plan_version=version,
            current_input_signature=SIGNATURE,
            confirmed_by=None,
        )
    assert excinfo.value.reason == "terminal"
    assert (
        await db.scalar(
            select(func.count()).select_from(JobDispatch).where(JobDispatch.job_id == job.id)
        )
        == 0
    )


@pytest.mark.asyncio
async def test_missing_plan_refuses_confirmation(db) -> None:
    with pytest.raises(PlanConflictError) as excinfo:
        await confirm_mutation(
            db,
            job_id="does-not-exist",
            expected_plan_version="0" * 64,
            current_input_signature=SIGNATURE,
            confirmed_by=None,
        )
    assert excinfo.value.reason == "missing"
