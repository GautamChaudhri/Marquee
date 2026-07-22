"""JMC6I intentional-red runner-progress contracts (I0 / plan §3.4).

The revalidated §2 findings: the poster host adapter forwards only recognized
stage-*start* labels (dropping the runner's ``done``/``total``/``survivors``), and
the non-head ML handlers attach no progress callback at all, so trainer
``processed``/``total`` measurements never reach the typed durable progress model.

These contracts freeze the required behavior: real runner measurements must reach
durable, fenced, typed ``JobProgress`` snapshots — a determinate *current* scope
with true counts, survivor counts as bounded metrics, and a monotonic overall
scope — without fabricated percentages. RED at the JMC6I plan base by design;
phases I2/I3 make them green. They must never be weakened, skipped, or xfailed.

Learned-head progress is deliberately NOT asserted here: its behavior is reserved
to JMC6J and must remain unchanged by JMC6I.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import select

from marquee.core.jobs.execution_io import ExecutionIO
from marquee.core.jobs.execution_progress import ExecutionProgress
from marquee.core.jobs.handlers_ml import execute_taste_rebuild
from marquee.core.jobs.handlers_posters import execute_poster_analysis
from marquee.core.jobs.manifest import JOB_DEFINITION_REGISTRY
from marquee.core.jobs.progress import JobProgress, MeasurementMode
from marquee.core.jobs.progress_service import progress_writer
from marquee.core.jobs.workspaces import AttemptWorkspaceManager
from marquee.database import _get_session_factory
from marquee.models import Job, Movie
from marquee.models.job import JobAttempt
from tests.support.jmc5b_harness import Fence

_FENCE = 11


async def _progress_context(
    db,
    data_dir,
    *,
    job_type: str,
    request: dict,
    feature_area: str,
    subject: dict,
):
    """The product-effect harness surface, but with a *real* ExecutionProgress."""
    job_id = uuid4().hex
    db.add(
        Job(
            id=job_id,
            type=job_type,
            payload_version=1,
            request=request,
            phase="running",
            desired_state="run",
            fence_token=_FENCE,
            root_id=job_id,
            trigger_kind="manual",
            feature_area=feature_area,
            presentation_family=feature_area,
            subject_kind=subject["kind"],
            subject_reference=subject["display_id"],
            subject_snapshot=subject,
        )
    )
    await db.flush()
    attempt = JobAttempt(
        job_id=job_id,
        number=1,
        fence_token=_FENCE,
        phase="running",
        started_at=datetime.now(UTC),
    )
    db.add(attempt)
    await db.flush()
    job = await db.get(Job, job_id)
    assert job is not None
    job.current_attempt_id = attempt.id
    await db.commit()

    workspace = AttemptWorkspaceManager.for_data_dir(str(data_dir)).create(
        job_id=job_id, attempt_id=attempt.id, fence_token=_FENCE
    )
    fence = Fence()

    async def owns_fence() -> bool:
        return await fence.owns_current_attempt(None)

    definition = JOB_DEFINITION_REGISTRY.get(job_type)
    progress = ExecutionProgress.create(
        job_id=job_id,
        attempt_id=attempt.id,
        fence_token=_FENCE,
        definition=definition,
        subject=subject,
    )
    return SimpleNamespace(
        delivery=SimpleNamespace(canonical_job_id=job_id),
        attempt=SimpleNamespace(attempt_id=attempt.id, fence_token=_FENCE),
        request=request,
        configuration={},
        subject=subject,
        definition=definition,
        cancellation=SimpleNamespace(cancel_called=False, is_cancelled=lambda: False),
        writer=fence,
        io=ExecutionIO(cancelled=lambda: False, owns_fence=owns_fence),
        workspace=workspace,
        process_launcher=None,
        progress=progress,
        session_factory=_get_session_factory(),
    )


def _spy_durable_writes(monkeypatch) -> list[JobProgress]:
    """Record every *accepted* durable progress snapshot without changing behavior."""
    accepted: list[JobProgress] = []
    original = progress_writer.write

    async def recording_write(**kwargs):
        result = await original(**kwargs)
        accepted.append(result)
        return result

    monkeypatch.setattr(progress_writer, "write", recording_write)
    return accepted


def _movie_subject(movie_id: int) -> dict:
    return {
        "version": 1,
        "kind": "movie",
        "display_id": f"movie:{movie_id}",
        "display_name": "Blade Runner 2049",
        "movie_id": movie_id,
        "title": "Blade Runner 2049",
    }


_ML_SUBJECT = {
    "version": 1,
    "kind": "model_profile_training",
    "display_id": "taste_profile:movies",
    "display_name": "Movie taste profile",
    "subject_type": "profile",
    "name": "taste_profile:movies",
}


@pytest.mark.asyncio
async def test_poster_runner_counts_reach_durable_progress(db, data_dir, monkeypatch) -> None:
    """§6.1: real poster done/total and survivor counts must reach typed durable
    snapshots — not collapse to bare stage-start labels."""
    from marquee.core.jobs.internal_runner_host import RunnerFile, RunnerOutcome

    movie = Movie(title="Blade Runner 2049", year=2017, folder_path=str(data_dir), tmdb_id=1)
    db.add(movie)
    await db.flush()
    context = await _progress_context(
        db,
        data_dir,
        job_type="poster_pipeline",
        request={
            "movie_id": movie.id,
            "title": "Blade Runner 2049",
            "source_descriptors": [{"provider": "tmdb", "reference": "111"}],
        },
        feature_area="ai_posters",
        subject=_movie_subject(movie.id),
    )
    workspace_dir = (
        context.workspace.directory.root.resolved() / context.workspace.directory.key.value
    )
    accepted = _spy_durable_writes(monkeypatch)

    async def fake_run(launcher, *, operation, manifest, on_progress=None, **kwargs):
        # The exact measurement sequence the real contained pipeline emits.
        assert on_progress is not None
        await on_progress(
            {"v": 1, "type": "progress", "stage": "fetch", "state": "start", "total": 6}
        )
        await on_progress(
            {"v": 1, "type": "progress", "stage": "ocr", "state": "start", "total": 12}
        )
        await on_progress(
            {"v": 1, "type": "progress", "stage": "ocr", "state": "progress",
             "done": 5, "total": 12}
        )
        await on_progress(
            {"v": 1, "type": "progress", "stage": "ocr", "state": "end", "survivors": 9}
        )
        await on_progress(
            {"v": 1, "type": "progress", "stage": "rank", "state": "start", "total": 9}
        )
        (workspace_dir / "run.json").write_text('{"run_id": "progressrun01", "candidates": []}')
        return RunnerOutcome(
            outcome="succeeded",
            summary={
                "pipeline_status": "completed",
                "run_id": "progressrun01",
                "counts": {"posters_found": 6, "ranked": 2},
                "recommendation": None,
                "scorer_name": "weighted",
                "source_count": 6,
                "candidate_count": 2,
                "candidate_files": {},
            },
            files=(RunnerFile("run.json", "0" * 64, 2),),
            ready=True,
            exit_code=0,
        )

    monkeypatch.setattr("marquee.core.jobs.internal_runner_host.run_internal_operation", fake_run)
    result = await execute_poster_analysis(context)
    assert result["outcome"] in {"succeeded", "review_required", "no_change"}

    determinate_current = [
        snapshot
        for snapshot in accepted
        if snapshot.current.mode == MeasurementMode.DETERMINATE
        and snapshot.current.completed == 5
        and snapshot.current.total == 12
    ]
    assert determinate_current, (
        "the runner's real done=5/total=12 measurement must reach a durable "
        "determinate current scope; stage-start labels alone are not progress"
    )
    survivor_writes = [
        snapshot for snapshot in accepted if snapshot.metrics.items_survived == 9
    ]
    assert survivor_writes, (
        "the runner's survivor count must reach durable snapshots as a bounded metric"
    )
    policy = context.definition.progress_policy
    for snapshot in accepted:
        assert snapshot.stage.key in policy.stage_keys
    overall_percents = [
        snapshot.overall.percent for snapshot in accepted if snapshot.overall.percent is not None
    ]
    assert overall_percents == sorted(overall_percents), (
        "overall progress must remain monotonic within the attempt"
    )

    async with _get_session_factory()() as session:
        job = await session.scalar(
            select(Job).where(Job.id == context.delivery.canonical_job_id)
        )
        assert job is not None and job.progress is not None
        stored = JobProgress.model_validate(job.progress)
    assert stored.fence_token == _FENCE


@pytest.mark.asyncio
async def test_taste_rebuild_trainer_counts_reach_durable_progress(
    db, data_dir, monkeypatch
) -> None:
    """§6.2: taste rebuild must attach the shared progress adapter and forward the
    trainer's processed/total measurements to durable typed snapshots."""
    import numpy as np

    from marquee.core.jobs.internal_runner_host import RunnerFile, RunnerOutcome

    context = await _progress_context(
        db,
        data_dir,
        job_type="taste_rebuild",
        request={"source": "training_dir", "library": "movies", "expected_generation": 0,
                 "seed": 0},
        feature_area="ml_taste",
        subject=_ML_SUBJECT,
    )
    workspace_dir = (
        context.workspace.directory.root.resolved() / context.workspace.directory.key.value
    )
    accepted = _spy_durable_writes(monkeypatch)

    def _write_profile(path) -> None:
        embeddings = np.zeros((2, 512), dtype=np.float32)
        embeddings[0, 0] = 0.1
        embeddings[1, 0] = 0.2
        np.savez(
            path,
            model_name="clip-vit-b-32",
            embeddings=embeddings,
            centroid_emb=embeddings.mean(axis=0),
            poster_names=np.array(["a.jpg", "b.jpg"]),
            asset_kinds=np.array(["movie", "movie"]),
        )

    async def fake_run(launcher, *, operation, manifest, on_progress=None, **kwargs):
        assert on_progress is not None, (
            "taste_rebuild must attach the shared runner progress adapter"
        )
        # The trainer's real numeric emission (CLIP embedding then calibration counts).
        await on_progress(
            {"v": 1, "type": "progress", "stage": "clip", "state": "progress",
             "done": 2, "total": 3}
        )
        await on_progress(
            {"v": 1, "type": "progress", "stage": "calibration", "state": "progress",
             "done": 1, "total": 3}
        )
        _write_profile(workspace_dir / "profile.npz")
        return RunnerOutcome(
            outcome="succeeded",
            summary={"family": "taste_profile", "library": "movies", "exemplars": 2},
            files=(RunnerFile("profile.npz", "0" * 64, 1),),
            ready=True,
            exit_code=0,
        )

    monkeypatch.setattr("marquee.core.jobs.internal_runner_host.run_internal_operation", fake_run)
    result = await execute_taste_rebuild(context)
    assert result["outcome"] == "succeeded"

    determinate_current = [
        snapshot
        for snapshot in accepted
        if snapshot.current.mode == MeasurementMode.DETERMINATE
        and snapshot.current.completed in {1, 2}
        and snapshot.current.total == 3
    ]
    assert determinate_current, (
        "the trainer's processed/total measurements must reach a durable determinate "
        "current scope; today the handler attaches no progress callback at all"
    )
    policy = context.definition.progress_policy
    for snapshot in accepted:
        assert snapshot.stage.key in policy.stage_keys
