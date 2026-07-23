"""JMC6H intentional-red product-effect contracts (H0 / §2.5).

These prove that the *current* canonical poster and ML handlers are placeholders:
the poster handler does not run the real pipeline (no canonical ``PipelineRun``
projection), and taste publication does not produce a native artifact the real
loader can consume.  They fail on ``jmc6g-complete`` by design and are made green
by the real implementations in phases H2 (poster) and H4 (ML).

They must never be weakened, skipped, or xfailed; a later phase makes each pass by
delivering the real product effect it asserts.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import numpy as np
import pytest
from fastapi import Request
from httpx import ASGITransport, AsyncClient
from PIL import Image
from sqlalchemy import select

from marquee.core.jobs.artifact_service import physical_artifact_file
from marquee.core.jobs.execution_io import ExecutionIO
from marquee.core.jobs.handlers_ml import (
    execute_ranking_residual,
    execute_taste_enrich,
    execute_taste_map,
    execute_taste_rebuild,
)
from marquee.core.jobs.handlers_poster_mutations import execute_poster_deploy
from marquee.core.jobs.handlers_posters import execute_poster_analysis
from marquee.core.jobs.manifest import JOB_DEFINITION_REGISTRY
from marquee.core.jobs.pipeline_archives import load_pipeline_archive
from marquee.core.jobs.workspaces import AttemptWorkspaceManager
from marquee.core.pipeline_config import pipeline_settings
from marquee.database import _get_session_factory
from marquee.main import app
from marquee.ml.residual import ResidualArtifact, ResidualEvaluation
from marquee.models import Job, JobArtifact, Movie, PipelineRun
from marquee.models.job import JobAttempt
from marquee.models.ml_publication import MlActivePublication
from marquee.pipeline.scorer import ResidualScorer, WeightedScorer
from tests.support.jmc5b_harness import Fence

_FENCE = 7


async def _context(
    db,
    data_dir,
    *,
    job_type: str,
    request: dict,
    feature_area: str,
    subject_kind: str,
    subject_reference: str,
):
    """Build the poster/ML ExecutionContext surface the real handlers consume."""
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
            subject_kind=subject_kind,
            subject_reference=subject_reference,
            subject_snapshot={"version": 1, "kind": subject_kind},
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

    async def progress_stage(*_args, **_kwargs) -> None:
        return None

    return SimpleNamespace(
        delivery=SimpleNamespace(canonical_job_id=job_id),
        attempt=SimpleNamespace(attempt_id=attempt.id, fence_token=_FENCE),
        request=request,
        configuration={},
        subject={"version": 1, "kind": subject_kind, "reference": subject_reference},
        definition=JOB_DEFINITION_REGISTRY.get(job_type),
        cancellation=SimpleNamespace(cancel_called=False, is_cancelled=lambda: False),
        writer=fence,
        io=ExecutionIO(cancelled=lambda: False, owns_fence=owns_fence),
        workspace=workspace,
        process_launcher=None,
        progress=SimpleNamespace(stage=progress_stage),
        session_factory=_get_session_factory(),
    )


async def _read_artifact_bytes(artifact: JobArtifact) -> bytes:
    boundary, classified = physical_artifact_file(artifact)
    fd = boundary.open_read(classified)
    try:
        chunks = []
        while chunk := os.read(fd, 1 << 20):
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        os.close(fd)


def _is_native_numpy_artifact(data: bytes) -> bool:
    try:
        loaded = np.load(io.BytesIO(data), allow_pickle=False)
    except Exception:
        return False
    if isinstance(loaded, np.lib.npyio.NpzFile):
        with loaded:
            return len(loaded.files) > 0
    return isinstance(loaded, np.ndarray)


@pytest.mark.asyncio
async def test_poster_pipeline_writes_canonical_pipeline_run_projection(
    db, data_dir, installed_pgqueuer, monkeypatch
):
    """H10: a delivered poster_pipeline job writes a canonical PipelineRun projection.

    The real handler runs the pipeline through the contained runner; here the runner
    boundary is driven with a controlled outcome (the slow real-model subprocess is
    certified separately by ``test_jmc6h_poster_live_smoke.py``) so the handler's
    real projection logic — PipelineRun linkage + artifact registration — is
    exercised deterministically. RED on jmc6g-complete (placeholder wrote no run).
    """
    from marquee.core.jobs.internal_runner_host import RunnerFile, RunnerOutcome

    movie_folder = data_dir / "library" / "BR2049"
    movie_folder.mkdir(parents=True)
    movie = Movie(
        title="Blade Runner 2049",
        year=2017,
        folder_path=str(movie_folder),
        tmdb_id=335984,
    )
    db.add(movie)
    await db.flush()
    request = {
        "movie_id": movie.id,
        "title": "Blade Runner 2049",
        "source_descriptors": [{"provider": "tmdb", "reference": "111"}],
    }
    context = await _context(
        db,
        data_dir,
        job_type="poster_pipeline",
        request=request,
        feature_area="ai_posters",
        subject_kind="movie",
        subject_reference=str(movie.id),
    )
    job_id = context.delivery.canonical_job_id
    workspace_dir = (
        context.workspace.directory.root.resolved() / context.workspace.directory.key.value
    )

    outcome = RunnerOutcome(
        outcome="succeeded",
        summary={
            "pipeline_status": "completed",
            "run_id": "fixturerun0001",
            "counts": {"posters_found": 3, "ranked": 2, "style_gated": 1, "total": 4},
            "recommendation": {"orig_filename": "poster_a.jpg", "rank": 1, "final_score": 0.87},
            "scorer_name": "weighted",
            "source_count": 3,
            "candidate_count": 3,
            "candidate_files": {"poster_a.jpg": "candidate-000.jpg"},
        },
        files=(RunnerFile("run.json", "0" * 64, 2),),
        ready=True,
        exit_code=0,
    )

    async def fake_run(launcher, *, operation, manifest, **kwargs):
        (workspace_dir / "run.json").write_text(
            json.dumps(
                {
                    "run_id": "fixturerun0001",
                    "candidates": [
                        {
                            "orig_filename": "poster_a.jpg",
                            "image_path": str(workspace_dir / "candidate-000.jpg"),
                            "rank": 1,
                            "final_score": 0.87,
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        Image.new("RGB", (8, 12), color=(20, 40, 60)).save(
            workspace_dir / "candidate-000.jpg", format="JPEG"
        )
        return outcome

    monkeypatch.setattr("marquee.core.jobs.internal_runner_host.run_internal_operation", fake_run)

    result = await execute_poster_analysis(context)
    assert result["outcome"] == "succeeded"

    async with _get_session_factory()() as session:
        run = await session.scalar(select(PipelineRun).where(PipelineRun.job_id == job_id))
        assert run is not None, (
            "poster_pipeline must write a PipelineRun linked to the canonical job"
        )
        archive = await load_pipeline_archive(session, run)
        selected = await session.get(JobArtifact, run.selected_artifact_id)
        assert run.archive_artifact_id is not None
        assert archive is not None
        assert archive["candidates"][0]["artifact_id"] == run.selected_artifact_id
    assert run.attempt_id == context.attempt.attempt_id
    assert run.fence_token == context.attempt.fence_token
    assert run.movie_id == movie.id
    assert run.status == "completed"
    assert run.auto_pick_filename == "poster_a.jpg"
    assert run.scorer_name == "weighted"
    assert selected is not None
    assert selected.artifact_metadata["candidate_reference"] == "poster_a.jpg"

    from marquee.api.routes import feedback as feedback_route

    feedback = await feedback_route.apply_feedback_request(
        feedback_route.FeedbackRequest(
            run_id="fixturerun0001",
            action="approve",
            deploy=True,
            idempotency_key="fixture-review-deploy",
        ),
        Request({"type": "http", "method": "POST", "path": "/api/feedback", "headers": []}),
        db,
    )
    deployment_job = await db.get(Job, feedback["deployment_job"]["job_id"])
    assert deployment_job is not None
    assert deployment_job.request["candidate"]["artifact_id"] == selected.id

    deploy_context = await _context(
        db,
        data_dir,
        job_type="poster_deploy",
        request=deployment_job.request,
        feature_area="ai_posters",
        subject_kind="poster",
        subject_reference=f"movie:{movie.id}",
    )
    from marquee.config import settings

    monkeypatch.setattr(settings, "MEDIA_ROOTS", [str(data_dir)])
    monkeypatch.setattr(settings, "RADARR_MEDIA_PATH", None)
    monkeypatch.setattr(settings, "SONARR_MEDIA_PATH", None)
    deploy_result = await execute_poster_deploy(deploy_context)
    assert deploy_result["outcome"] == "succeeded"
    await db.refresh(movie)
    assert movie.poster_ai_selected is True
    assert movie.poster_user_approved is True
    assert movie.poster_path is not None
    assert Path(movie.poster_path).is_file()
    (workspace_dir / "candidate-000.jpg").unlink(missing_ok=True)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        refreshed = await client.get(f"/api/library/movies/{movie.id}")
        candidate_response = await client.get(
            "/api/pipeline/runs/fixturerun0001/posters/poster_a.jpg"
        )
    assert refreshed.status_code == 200
    assert refreshed.json()["poster_status"] == "approved"
    assert refreshed.json()["poster_url"] == f"/api/library/movies/{movie.id}/poster"
    assert candidate_response.status_code == 200
    assert candidate_response.content.startswith(b"\xff\xd8")


def _write_valid_taste_profile(path) -> None:
    """A minimal profile the production loader (NumpyTasteStore) accepts."""
    embeddings = np.zeros((2, 512), dtype=np.float32)
    embeddings[0, 0] = 0.1
    embeddings[1, 0] = 0.2
    np.savez(
        path,
        model_name="clip-vit-b-32",
        dino_model_name="dinov2-vits14",
        embeddings=embeddings,
        centroid_emb=embeddings.mean(axis=0),
        poster_names=np.array(["a.jpg", "b.jpg"]),
        asset_kinds=np.array(["movie", "movie"]),
    )


def _write_valid_taste_map(path) -> None:
    np.savez(
        path,
        coords_3d=np.zeros((2, 3), dtype=np.float32),
        coords_2d=np.zeros((2, 2), dtype=np.float32),
        poster_names=np.array(["a.jpg", "b.jpg"]),
        self_knn=np.array([1.0, 1.0], dtype=np.float32),
        movie_ids=np.array([0, 0], dtype=np.int64),
        movie_titles=np.array(["A", "B"]),
        projection_method=np.array("pca"),
        profile_mtime=np.float64(0),
        computed_at=np.array("2026-07-19T00:00:00+00:00"),
        cluster_ratio=np.float64(0.1),
        cluster_epsilon=np.float64(0.5),
        cluster_method=np.array("fixture"),
        unique_movie_count=np.int64(2),
        duplicate_group_count=np.int64(0),
        noise_count=np.int64(0),
    )


def _write_valid_enriched_profile(path) -> None:
    _write_valid_taste_profile(path)
    with np.load(path, allow_pickle=False) as data:
        payload = {key: data[key] for key in data.files}
    payload["genres_json"] = np.array(['["Science Fiction"]', '["Drama"]'])
    payload["years"] = np.array([2020, 2021], dtype=np.int64)
    payload["tmdb_ids"] = np.array([1, 2], dtype=np.int64)
    np.savez(path, **payload)


@pytest.mark.asyncio
async def test_taste_rebuild_publishes_native_loadable_profile_artifact(db, data_dir, monkeypatch):
    """H15/H18: taste_rebuild publishes a native artifact the real loader can read.

    The real handler runs taste_trainer.rebuild_profile in the contained runner; here
    the runner boundary is driven with a controlled outcome that stages a valid native
    .npz (the real CPU trainer is certified separately by
    ``test_jmc6h_taste_live_smoke.py``). The handler validates it with the production
    loader, registers it as a native octet-stream artifact, and activates it. RED on
    jmc6g-complete (placeholder wrote descriptive JSON).
    """
    from marquee.core.jobs.internal_runner_host import RunnerFile, RunnerOutcome

    request = {"source": "training_dir", "library": "movies", "expected_generation": 0, "seed": 0}
    context = await _context(
        db,
        data_dir,
        job_type="taste_rebuild",
        request=request,
        feature_area="ml_taste",
        subject_kind="model",
        subject_reference="taste_profile:movies",
    )
    workspace_dir = (
        context.workspace.directory.root.resolved() / context.workspace.directory.key.value
    )

    outcome = RunnerOutcome(
        outcome="succeeded",
        summary={"family": "taste_profile", "library": "movies", "exemplars": 2},
        files=(RunnerFile("profile.npz", "0" * 64, 1),),
        ready=True,
        exit_code=0,
    )

    async def fake_run(launcher, *, operation, manifest, **kwargs):
        _write_valid_taste_profile(workspace_dir / "profile.npz")
        return outcome

    monkeypatch.setattr("marquee.core.jobs.internal_runner_host.run_internal_operation", fake_run)

    result = await execute_taste_rebuild(context)
    assert result["outcome"] == "succeeded"
    assert result["activated"] is True

    async with _get_session_factory()() as session:
        active = await session.scalar(
            select(MlActivePublication).where(MlActivePublication.family == "taste_profile:movies")
        )
        assert active is not None, "taste_rebuild must activate a taste_profile:movies publication"
        artifact = await session.get(JobArtifact, active.artifact_id)
    assert artifact is not None

    assert artifact.content_type == "application/octet-stream", (
        "the active taste profile must be a native artifact, not a descriptive JSON document"
    )
    data = await _read_artifact_bytes(artifact)
    assert _is_native_numpy_artifact(data), (
        "the production taste-profile loader must be able to load the activated artifact"
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        listing = await client.get("/api/taste/profiles")
        assert listing.status_code == 200, listing.text
        profiles = listing.json()["profiles"]
        assert [profile["id"] for profile in profiles] == [str(artifact.id)]
        assert profiles[0]["status"] == "active"
        assert profiles[0]["summary"]["exemplars"] == 2
        detail = await client.get(f"/api/taste/profiles/{artifact.id}")
        assert detail.status_code == 200, detail.text
        assert [movie["title"] for movie in detail.json()["movies"]] == ["a", "b"]
        exemplars = await client.get(f"/api/taste/profiles/{artifact.id}/exemplars")
        assert exemplars.status_code == 200, exemplars.text
        assert [row["name"] for row in exemplars.json()["exemplars"]] == ["a.jpg", "b.jpg"]
        neighbors = await client.get("/api/taste/exemplars/a.jpg/neighbors")
        assert neighbors.status_code == 200, neighbors.text
        assert neighbors.json()["neighbors"][0]["name"] == "b.jpg"


@pytest.mark.asyncio
async def test_taste_map_consumes_active_profile_and_publishes_native_map(
    db, data_dir, monkeypatch
):
    """H16/H18: map input/output both cross the active-publication authority."""
    from marquee.core.jobs.internal_runner_host import RunnerFile, RunnerOutcome

    profile_context = await _context(
        db,
        data_dir,
        job_type="taste_rebuild",
        request={
            "source": "training_dir",
            "library": "movies",
            "expected_generation": 0,
            "seed": 0,
        },
        feature_area="ml_taste",
        subject_kind="model",
        subject_reference="taste_profile:movies",
    )
    profile_workspace = (
        profile_context.workspace.directory.root.resolved()
        / profile_context.workspace.directory.key.value
    )

    async def fake_profile(*_args, **_kwargs):
        _write_valid_taste_profile(profile_workspace / "profile.npz")
        return RunnerOutcome(
            outcome="succeeded",
            summary={"family": "taste_profile", "library": "movies", "exemplars": 2},
            files=(RunnerFile("profile.npz", "0" * 64, 1),),
            ready=True,
            exit_code=0,
        )

    monkeypatch.setattr(
        "marquee.core.jobs.internal_runner_host.run_internal_operation", fake_profile
    )
    assert (await execute_taste_rebuild(profile_context))["activated"] is True

    map_context = await _context(
        db,
        data_dir,
        job_type="taste_map",
        request={"library": "movies", "expected_generation": 0, "seed": 0},
        feature_area="ml_taste",
        subject_kind="model",
        subject_reference="taste_map:movies",
    )
    map_workspace = (
        map_context.workspace.directory.root.resolved() / map_context.workspace.directory.key.value
    )

    async def fake_map(*_args, **_kwargs):
        assert (map_workspace / "profile.npz").is_file()
        _write_valid_taste_map(map_workspace / "map.npz")
        return RunnerOutcome(
            outcome="succeeded",
            summary={"family": "taste_map", "library": "movies", "exemplars": 2},
            files=(RunnerFile("map.npz", "0" * 64, 1),),
            ready=True,
            exit_code=0,
        )

    monkeypatch.setattr("marquee.core.jobs.internal_runner_host.run_internal_operation", fake_map)
    result = await execute_taste_map(map_context)
    assert result["activated"] is True

    async with _get_session_factory()() as session:
        active = await session.get(MlActivePublication, "taste_map:movies")
        assert active is not None
        artifact = await session.get(JobArtifact, active.artifact_id)
    assert artifact is not None
    assert artifact.content_type == "application/octet-stream"
    assert _is_native_numpy_artifact(await _read_artifact_bytes(artifact))


@pytest.mark.asyncio
async def test_taste_enrichment_advances_the_consumed_profile_authority(db, data_dir, monkeypatch):
    """H16: enrichment creates a native taste-profile successor, not a dead registry."""
    from marquee.core.jobs.internal_runner_host import RunnerFile, RunnerOutcome

    profile_context = await _context(
        db,
        data_dir,
        job_type="taste_rebuild",
        request={
            "source": "training_dir",
            "library": "movies",
            "expected_generation": 0,
            "seed": 0,
        },
        feature_area="ml_taste",
        subject_kind="model",
        subject_reference="taste_profile:movies",
    )
    first_workspace = (
        profile_context.workspace.directory.root.resolved()
        / profile_context.workspace.directory.key.value
    )

    async def fake_profile(*_args, **_kwargs):
        _write_valid_taste_profile(first_workspace / "profile.npz")
        return RunnerOutcome(
            outcome="succeeded",
            summary={"family": "taste_profile", "library": "movies", "exemplars": 2},
            files=(RunnerFile("profile.npz", "0" * 64, 1),),
            ready=True,
            exit_code=0,
        )

    monkeypatch.setattr(
        "marquee.core.jobs.internal_runner_host.run_internal_operation", fake_profile
    )
    assert (await execute_taste_rebuild(profile_context))["active_generation"] == 1

    enrich_context = await _context(
        db,
        data_dir,
        job_type="taste_enrich",
        request={"library": "movies", "expected_generation": 1, "seed": 0},
        feature_area="ml_taste",
        subject_kind="model",
        subject_reference="taste_profile:movies",
    )
    second_workspace = (
        enrich_context.workspace.directory.root.resolved()
        / enrich_context.workspace.directory.key.value
    )

    async def fake_enrichment(*_args, **_kwargs):
        assert (second_workspace / "source-profile.npz").is_file()
        _write_valid_enriched_profile(second_workspace / "profile.npz")
        return RunnerOutcome(
            outcome="succeeded",
            summary={
                "family": "taste_profile",
                "operation": "enrichment",
                "library": "movies",
                "exemplars": 2,
                "resolved": 2,
            },
            files=(RunnerFile("profile.npz", "0" * 64, 1),),
            ready=True,
            exit_code=0,
        )

    monkeypatch.setattr(
        "marquee.core.jobs.internal_runner_host.run_internal_operation", fake_enrichment
    )
    result = await execute_taste_enrich(enrich_context)
    assert result["family"] == "taste_profile"
    assert result["active_generation"] == 2
    assert result["activated"] is True
    async with _get_session_factory()() as session:
        assert await session.get(MlActivePublication, "taste_enrichment:movies") is None
        active = await session.get(MlActivePublication, "taste_profile:movies")
        assert active is not None
        artifact = await session.get(JobArtifact, active.artifact_id)
    assert artifact is not None
    assert artifact.kind == "taste_profile"
    assert _is_native_numpy_artifact(await _read_artifact_bytes(artifact))


@pytest.mark.asyncio
async def test_ranking_residual_publishes_native_loadable_artifact(db, data_dir, monkeypatch):
    """H16/H18: residual training activates the exact native artifact the scorer loads."""
    from marquee.core.jobs.internal_runner_host import RunnerFile, RunnerOutcome

    context = await _context(
        db,
        data_dir,
        job_type="ranking_residual_train",
        request={
            "library": "movies",
            "expected_generation": 0,
            "seed": 0,
            "evidence_revision": "manual:test",
            "mutation": "manual",
        },
        feature_area="ml_taste",
        subject_kind="model",
        subject_reference="ranking_residual:movies",
    )
    workspace = context.workspace.directory.root.resolved() / context.workspace.directory.key.value
    profile_checksum = "a" * 64

    async def fake_resolve(_session, *, family):
        if family == "taste_profile:movies":
            return SimpleNamespace(checksum=profile_checksum)
        return None

    monkeypatch.setattr(
        "marquee.core.jobs.handlers_ml.resolve_active_publication", fake_resolve
    )

    async def fake_residual(*_args, **kwargs):
        assert json.loads((workspace / "preference-events.json").read_text()) == []
        params = kwargs["manifest"]["params"]
        residual = ResidualArtifact(
            namespace="movies",
            feature_names=["knn_sim"],
            weights=np.asarray([0.2], dtype=np.float64),
            bias=0.0,
            alpha=0.5,
            delta_max=1.0,
            baseline_signature=params["baseline_signature"],
            profile_checksum=profile_checksum,
            evidence_revision=hashlib.sha256(b"[]").hexdigest(),
            seed=0,
            evaluation=ResidualEvaluation(0.5, 0.75, 0.25, 5, 20, 0.1, 0.2),
            trained_at=datetime.now(UTC).isoformat(),
        )
        residual.save(workspace / "residual.npz")
        return RunnerOutcome(
            outcome="succeeded",
            summary={
                "family": "ranking_residual",
                "event_rows": 0,
                "subjects": 5,
                "pairs": 20,
                "evaluation": {"improvement": 0.25},
            },
            files=(RunnerFile("residual.npz", "0" * 64, 1),),
            ready=True,
            exit_code=0,
        )

    monkeypatch.setattr(
        "marquee.core.jobs.internal_runner_host.run_internal_operation", fake_residual
    )
    result = await execute_ranking_residual(context)
    assert result["activated"] is True
    assert result["active_generation"] == 1

    async with _get_session_factory()() as session:
        active = await session.get(MlActivePublication, "ranking_residual:movies")
        assert active is not None
        artifact = await session.get(JobArtifact, active.artifact_id)
    assert artifact is not None
    assert artifact.kind == "ranking_residual"
    assert artifact.content_type == "application/octet-stream"
    _, classified = physical_artifact_file(artifact)
    artifact_path = classified.root.resolved() / classified.key.value
    loaded = ResidualArtifact.load(artifact_path)
    assert loaded.feature_names == ["knn_sim"]
    scorer = ResidualScorer(WeightedScorer(pipeline_settings), loaded)
    score, _ = scorer.score(SimpleNamespace(normalized={"knn_sim": 1.0}))
    assert 0.0 < score < 1.0

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        listing = await client.get("/api/taste/residuals")
        assert listing.status_code == 200, listing.text
        residuals = listing.json()["residuals"]
        assert [item["id"] for item in residuals] == [str(artifact.id)]
        assert residuals[0]["status"] == "active"
        detail = await client.get(f"/api/taste/residuals/{artifact.id}")
        assert detail.status_code == 200, detail.text
        assert detail.json()["summary"]["top_features"][0]["name"] == "knn_sim"
        assert detail.json()["summary"]["evaluation"]["improvement"] == 0.25
