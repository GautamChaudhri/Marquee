import os
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from marquee.core.jobs.artifact_service import artifact_boundary
from marquee.core.jobs.fenced_writer import AttemptOwnership, FencedWriter
from marquee.core.jobs.internal_runner import group_progress_frame
from marquee.core.jobs.manifest import JOB_DEFINITION_REGISTRY
from marquee.core.jobs.poster_cancellation import cleanup_cancelled_poster_attempt
from marquee.core.jobs.poster_pipeline import _STAGE_MAP
from marquee.core.jobs.runner_progress import RunnerProgressBridge, RunnerProgressFrame
from marquee.core.jobs.work_items import PosterWorkItemTracker
from marquee.database import _get_session_factory
from marquee.models import Job, JobArtifact, JobAttempt, JobWorkItem, Movie, PipelineRun
from marquee.pipeline.poster_group_runner import GroupProgressEvent

NOW = datetime(2026, 7, 30, 12, 0, tzinfo=UTC)


def _member(index: int) -> tuple[str, dict]:
    identifier = index + 1
    return (
        f"movie:{identifier}",
        {
            "version": 1,
            "kind": "movie",
            "display_id": f"movie:{identifier}",
            "display_name": f"Movie {identifier}",
            "snapshot_at": NOW.isoformat(),
            "movie_id": identifier,
            "title": f"Movie {identifier}",
            "media_kind": "movie",
        },
    )


async def _running_context(db, *, job_id: str, members: list[tuple[str, dict]]):
    definition = JOB_DEFINITION_REGISTRY.get("poster_pipeline_group")
    job = Job(
        id=job_id,
        type="poster_pipeline_group",
        request={
            "library": "movies",
            "chunk_index": 0,
            "batch_mode": "all_at_once",
            "members": [
                {"movie_id": index + 1, "title": snapshot["title"]}
                for index, (_key, snapshot) in enumerate(members)
            ],
        },
        phase="running",
        desired_state="run",
        fence_token=1,
        priority=50,
        eligible_at=NOW,
        dispatch_generation=1,
        root_id=job_id,
        trigger_kind="manual",
        feature_area="ai_posters",
        presentation_family="ai_posters",
        subject_kind="poster_subject_group",
        subject_reference="movies-0",
        subject_snapshot={
            "version": 1,
            "kind": "poster_subject_group",
            "display_id": "poster-group:movies:0",
            "display_name": "Poster analysis · movies",
            "snapshot_at": NOW.isoformat(),
            "library": "movies",
            "chunk_index": 0,
            "batch_mode": "all_at_once",
            "members": [{"subject_key": key, "subject": snapshot} for key, snapshot in members],
        },
        progress_sequence=0,
        work_item_sequence=0,
        created_at=NOW,
        started_at=NOW,
    )
    db.add(job)
    await db.flush()
    attempt = JobAttempt(
        job_id=job.id,
        number=1,
        fence_token=1,
        phase="running",
        started_at=NOW,
    )
    db.add(attempt)
    await db.flush()
    job.current_attempt_id = attempt.id
    await db.commit()
    ownership = AttemptOwnership(
        job_id=job.id,
        attempt_id=attempt.id,
        fence_token=1,
        dispatch_generation=1,
    )
    return SimpleNamespace(
        delivery=SimpleNamespace(canonical_job_id=job.id),
        attempt=SimpleNamespace(attempt_id=attempt.id, fence_token=1),
        definition=definition,
        session_factory=_get_session_factory(),
        writer=FencedWriter(ownership, definition),
    )


def _frame(
    *,
    scope: str | None,
    done: int | None = None,
    total: int | None = None,
    state: str = "progress",
    stage: str = "gate-resolution",
    unit: str | None = "candidates",
    survivors: int | None = None,
    member_scope: str | None = None,
    member_done: int | None = None,
    member_total: int | None = None,
):
    return RunnerProgressFrame(
        stage=stage,
        state=state,
        scope=scope,
        subject=None,
        done=float(done) if done is not None else None,
        total=float(total) if total is not None else None,
        unit=unit,
        survivors=survivors,
        message=None,
        cursor=1,
        member_scope=member_scope,
        member_done=member_done,
        member_total=member_total,
    )


@pytest.mark.asyncio
async def test_tracker_initializes_the_bounded_500_member_projection(db):
    members = [_member(index) for index in range(500)]
    context = await _running_context(
        db,
        job_id="work-items-500-00000000000000001",
        members=members,
    )

    tracker = await PosterWorkItemTracker.create(context, members)
    await tracker.close()

    factory = _get_session_factory()
    async with factory() as session:
        count = await session.scalar(
            select(func.count())
            .select_from(JobWorkItem)
            .where(JobWorkItem.job_id == context.delivery.canonical_job_id)
        )
        job = await session.get(Job, context.delivery.canonical_job_id)
    assert count == 500
    assert job is not None
    assert job.work_item_summary["total"] == 500
    assert job.work_item_summary["counts"]["pending"] == 500


@pytest.mark.asyncio
async def test_tracker_advances_stage_major_and_scoped_members_with_coalescing(db):
    members = [_member(0), _member(1), _member(2)]
    context = await _running_context(
        db,
        job_id="work-items-progress-000000000001",
        members=members,
    )
    tracker = await PosterWorkItemTracker.create(context, members)

    await tracker.observe(_frame(scope="m01", done=2, total=10), "validating")
    factory = _get_session_factory()
    async with factory() as session:
        before = await session.get(Job, context.delivery.canonical_job_id)
        assert before is not None
        assert before.work_item_sequence == 1
    await tracker.flush()

    async with factory() as session:
        scoped = list(
            (
                await session.scalars(
                    select(JobWorkItem)
                    .where(JobWorkItem.job_id == context.delivery.canonical_job_id)
                    .order_by(JobWorkItem.ordinal)
                )
            ).all()
        )
    assert [row.status for row in scoped] == ["pending", "running", "pending"]
    assert scoped[1].stage_number == 4
    assert (scoped[1].completed, scoped[1].total) == (2, 10)

    # A union stage measures one pooled workload. Its stage advance is shared,
    # but its numbers belong to nobody in particular: copying them onto every
    # row is what made all eight subjects in a group report the group's count.
    await tracker.observe(_frame(scope=None, done=4, total=12), "extracting")
    await tracker.flush()
    await tracker.close()
    async with factory() as session:
        collective = list(
            (
                await session.scalars(
                    select(JobWorkItem)
                    .where(JobWorkItem.job_id == context.delivery.canonical_job_id)
                    .order_by(JobWorkItem.ordinal)
                )
            ).all()
        )
    assert {row.status for row in collective} == {"running"}
    assert {row.stage_number for row in collective} == {6}
    assert {(row.completed, row.total) for row in collective} == {(None, None)}


@pytest.mark.asyncio
async def test_union_sample_measures_only_the_member_it_names(db):
    members = [_member(0), _member(1), _member(2)]
    context = await _running_context(
        db,
        job_id="work-items-member-00000000001",
        members=members,
    )
    tracker = await PosterWorkItemTracker.create(context, members)

    # Each subject learns its own share of the pooled OCR workload up front.
    for ordinal, share in enumerate((10, 20, 30)):
        await tracker.observe(
            _frame(scope=f"m{ordinal:02d}", state="start", stage="ocr", total=share),
            "validating",
        )
    # One pooled sample: 21 of 60 images done overall, of which 7 were m01's.
    await tracker.observe(
        _frame(
            scope=None,
            stage="ocr",
            done=21,
            total=60,
            member_scope="m01",
            member_done=7,
            member_total=20,
        ),
        "validating",
    )
    await tracker.flush()
    await tracker.close()

    factory = _get_session_factory()
    async with factory() as session:
        rows = list(
            (
                await session.scalars(
                    select(JobWorkItem)
                    .where(JobWorkItem.job_id == context.delivery.canonical_job_id)
                    .order_by(JobWorkItem.ordinal)
                )
            ).all()
        )
    assert [(row.completed, row.total) for row in rows] == [(0, 10), (7, 20), (0, 30)]
    # The denominators partition the group's pooled total rather than repeating it.
    assert sum(row.total for row in rows) == 60
    # Runner frames carry no unit; the definition's own vocabulary supplies it.
    assert {row.unit for row in rows} == {"candidates"}


@pytest.mark.asyncio
async def test_pipeline_events_survive_the_control_channel_into_roster_rows(db):
    """Walk one real measurement end to end: pipeline event → frame → row.

    The three layers name their fields independently, so a rename in any one of
    them would silently stop attributing work without failing anything. This
    holds all three against a single sample.
    """
    members = [_member(0), _member(1)]
    context = await _running_context(
        db,
        job_id="work-items-seam-000000000001",
        members=members,
    )
    tracker = await PosterWorkItemTracker.create(context, members)
    definition = JOB_DEFINITION_REGISTRY.get("poster_pipeline_group")
    observed: list = []
    bridge = RunnerProgressBridge(
        SimpleNamespace(
            job_id=context.delivery.canonical_job_id,
            definition=definition,
            observe=lambda *args, **kwargs: observed.append((args, kwargs)) or _noop(),
        ),
        stage_map=_STAGE_MAP,
        work_item_observer=tracker.observe,
    )

    events = [
        GroupProgressEvent(stage="fetch", state="end", scope="m01", survivors=47),
        GroupProgressEvent(stage="ocr", state="start", scope="m01", total=38),
        GroupProgressEvent(
            stage="ocr",
            state="progress",
            done=21,
            total=60,
            member_scope="m01",
            member_done=7,
            member_total=38,
        ),
    ]
    for cursor, event in enumerate(events, start=1):
        await bridge.on_frame(group_progress_frame(event, cursor=cursor))
    await tracker.flush()
    await tracker.close()

    assert bridge.degraded_frames == 0
    factory = _get_session_factory()
    async with factory() as session:
        rows = list(
            (
                await session.scalars(
                    select(JobWorkItem)
                    .where(JobWorkItem.job_id == context.delivery.canonical_job_id)
                    .order_by(JobWorkItem.ordinal)
                )
            ).all()
        )
    assert rows[1].source_count == 47
    assert (rows[1].completed, rows[1].total, rows[1].unit) == (7, 38, "candidates")
    # The pooled 21/60 belongs to the job, never to the subject beside it.
    assert (rows[0].completed, rows[0].total) == (None, None)


async def _noop() -> None:
    return None


@pytest.mark.asyncio
async def test_tracker_records_stage_totals_and_the_work_each_subject_brought_in(db):
    members = [_member(0), _member(1)]
    context = await _running_context(
        db,
        job_id="work-items-source-00000000001",
        members=members,
    )
    tracker = await PosterWorkItemTracker.create(context, members)

    # The download stage closes with the candidate count this subject pulled.
    await tracker.observe(
        _frame(scope="m00", state="end", stage="fetch", survivors=47),
        "downloading",
    )
    # A later gate narrows the per-stage total; the source count must not follow.
    await tracker.observe(
        _frame(scope="m00", state="start", stage="gate-style", total=38),
        "validating",
    )
    await tracker.observe(
        _frame(scope="m00", state="end", stage="gate-style", survivors=12),
        "validating",
    )
    await tracker.flush()
    await tracker.close()

    factory = _get_session_factory()
    async with factory() as session:
        rows = list(
            (
                await session.scalars(
                    select(JobWorkItem)
                    .where(JobWorkItem.job_id == context.delivery.canonical_job_id)
                    .order_by(JobWorkItem.ordinal)
                )
            ).all()
        )
    assert rows[0].source_count == 47
    assert (rows[0].completed, rows[0].total) == (38, 38)
    assert rows[1].source_count is None
    # The declared stage catalogue owns the denominator, not the column default.
    assert {row.stage_total for row in rows} == {9}


@pytest.mark.asyncio
async def test_tracker_stops_writing_after_fence_loss(db):
    members = [_member(0), _member(1)]
    context = await _running_context(
        db,
        job_id="work-items-fence-000000000000001",
        members=members,
    )
    tracker = await PosterWorkItemTracker.create(context, members)
    job = await db.get(Job, context.delivery.canonical_job_id)
    assert job is not None
    job.fence_token = 2
    await db.commit()

    await tracker.observe(_frame(scope=None, done=1, total=2), "validating")
    await tracker.flush()
    await tracker.close()

    factory = _get_session_factory()
    async with factory() as session:
        rows = list(
            (
                await session.scalars(
                    select(JobWorkItem).where(
                        JobWorkItem.job_id == context.delivery.canonical_job_id
                    )
                )
            ).all()
        )
    assert {row.status for row in rows} == {"pending"}


@pytest.mark.asyncio
async def test_retry_attempt_reinitializes_the_same_stable_members_under_its_new_fence(db):
    members = [_member(0), _member(1)]
    context = await _running_context(
        db,
        job_id="work-items-retry-00000000000001",
        members=members,
    )
    first = await PosterWorkItemTracker.create(context, members)
    await first.stage("validating")
    await first.flush()
    await first.close()

    job = await db.get(Job, context.delivery.canonical_job_id)
    old_attempt = await db.get(JobAttempt, context.attempt.attempt_id)
    assert job is not None and old_attempt is not None
    old_attempt.phase = "finished"
    old_attempt.outcome = "failed"
    old_attempt.finished_at = NOW
    job.fence_token = 2
    second_attempt = JobAttempt(
        job_id=job.id,
        number=2,
        fence_token=2,
        phase="running",
        started_at=NOW,
    )
    db.add(second_attempt)
    await db.flush()
    job.current_attempt_id = second_attempt.id
    await db.commit()

    ownership = AttemptOwnership(
        job_id=job.id,
        attempt_id=second_attempt.id,
        fence_token=2,
        dispatch_generation=1,
    )
    retry_context = SimpleNamespace(
        delivery=SimpleNamespace(canonical_job_id=job.id),
        attempt=SimpleNamespace(attempt_id=second_attempt.id, fence_token=2),
        definition=context.definition,
        session_factory=_get_session_factory(),
        writer=FencedWriter(ownership, context.definition),
    )
    retry = await PosterWorkItemTracker.create(retry_context, members)
    await retry.close()

    factory = _get_session_factory()
    async with factory() as session:
        rows = list(
            (
                await session.scalars(
                    select(JobWorkItem)
                    .where(JobWorkItem.job_id == job.id)
                    .order_by(JobWorkItem.ordinal)
                )
            ).all()
        )
        refreshed = await session.get(Job, job.id)
    assert [row.subject_key for row in rows] == ["movie:1", "movie:2"]
    assert {row.attempt_id for row in rows} == {second_attempt.id}
    assert {row.fence_token for row in rows} == {2}
    assert {row.status for row in rows} == {"pending"}
    assert refreshed is not None
    assert refreshed.work_item_summary["counts"]["pending"] == 2


@pytest.mark.asyncio
async def test_tracker_reconciles_exact_terminal_member_states(db):
    members = [_member(0), _member(1), _member(2)]
    context = await _running_context(
        db,
        job_id="work-items-terminal-000000000001",
        members=members,
    )
    tracker = await PosterWorkItemTracker.create(context, members)

    await tracker.reconcile(
        {
            "movie:1": ("succeeded", "Poster analysis completed."),
            "movie:2": ("review_required", "Poster candidates are ready for review."),
            "movie:3": ("failed", "TMDB returned no usable response."),
        }
    )
    await tracker.close()

    factory = _get_session_factory()
    async with factory() as session:
        rows = list(
            (
                await session.scalars(
                    select(JobWorkItem)
                    .where(JobWorkItem.job_id == context.delivery.canonical_job_id)
                    .order_by(JobWorkItem.ordinal)
                )
            ).all()
        )
    assert [row.status for row in rows] == ["succeeded", "review_required", "failed"]
    assert rows[2].message == "TMDB returned no usable response."
    assert {row.stage_number for row in rows} == {9}


@pytest.mark.asyncio
async def test_cancel_cleanup_is_attempt_scoped_idempotent_and_closes_work_items(db, tmp_path):
    members = [_member(0)]
    context = await _running_context(
        db,
        job_id="ca" * 16,
        members=members,
    )
    tracker = await PosterWorkItemTracker.create(context, members)
    await tracker.close()
    job = await db.get(Job, context.delivery.canonical_job_id)
    assert job is not None
    job.phase = "terminal"
    job.outcome = "cancelled"
    job.terminal_at = NOW
    attempt = await db.get(JobAttempt, context.attempt.attempt_id)
    assert attempt is not None
    attempt.phase = "finished"
    attempt.outcome = "cancelled"
    attempt.finished_at = NOW
    boundary = artifact_boundary(tmp_path)
    artifact_key = f"jobs/evidence/artifacts/{job.id}/{attempt.id}/archive.json"
    boundary.create_directory(
        boundary.from_key("data", f"jobs/evidence/artifacts/{job.id}/{attempt.id}"),
        parents=True,
    )
    artifact_path = boundary.from_key("data", artifact_key)
    descriptor = boundary.create_file(artifact_path)
    try:
        os.write(descriptor, b'{"candidate":"temporary"}\n')
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    artifact = JobArtifact(
        job_id=job.id,
        attempt_id=attempt.id,
        kind="command_report",
        name="archive.json",
        status="available",
        storage_key=artifact_key,
        content_type="application/json",
        artifact_metadata={"family": "poster_pipeline"},
        retention_class="extended",
    )
    db.add(artifact)
    await db.flush()
    db.add(Movie(id=1, title="Movie 1", folder_path="/movies/movie-1"))
    await db.flush()
    db.add(
        PipelineRun(
            run_id="cancelledrun00000000000000000001",
            job_id=job.id,
            subject_key="movie:1",
            attempt_id=attempt.id,
            fence_token=1,
            movie_id=1,
            media_type="movie",
            subject_snapshot=members[0][1],
            status="completed",
            archive_artifact_id=artifact.id,
        )
    )
    await db.commit()

    first = await cleanup_cancelled_poster_attempt(
        data_dir=tmp_path,
        job_id=job.id,
        attempt_id=attempt.id,
        fence_token=1,
    )
    second = await cleanup_cancelled_poster_attempt(
        data_dir=tmp_path,
        job_id=job.id,
        attempt_id=attempt.id,
        fence_token=1,
    )

    assert first.complete is True
    assert first.projections_removed == 1
    assert first.artifacts_expired == 1
    assert second.complete is True
    assert second.projections_removed == 0
    assert second.artifacts_expired == 0
    assert not (tmp_path / artifact_key).exists()
    factory = _get_session_factory()
    async with factory() as session:
        assert (
            await session.scalar(
                select(func.count()).select_from(PipelineRun).where(PipelineRun.job_id == job.id)
            )
            == 0
        )
        item = await session.scalar(select(JobWorkItem).where(JobWorkItem.job_id == job.id))
        stored_artifact = await session.get(JobArtifact, artifact.id)
    assert item is not None
    assert item.status == "cancelled"
    assert stored_artifact is not None
    assert stored_artifact.status == "expired"
