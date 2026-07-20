"""C4 certification for immutable ML publication and non-destructive rescan."""

from __future__ import annotations

import ast
import asyncio
import hashlib
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from pgqueuer import Queries
from sqlalchemy import select

from marquee.config import settings
from marquee.core.jobs.delivery import EXECUTION_HANDLERS
from marquee.core.jobs.documents import (
    LearnedHeadTrainRequestV1,
    MlPublicationResultV1,
    PosterRescanRequestV1,
    PosterRescanResultV1,
    TasteEnrichRequestV1,
    TasteMapRequestV1,
    TasteRebuildRequestV1,
)
from marquee.core.jobs.handlers_ml import execute_taste_rebuild
from marquee.core.jobs.handlers_rescan import execute_poster_rescan
from marquee.core.jobs.ml_publication import MlPublicationError, activate_immutable_artifact
from marquee.core.jobs.workspaces import AttemptWorkspaceManager
from marquee.database import _get_engine, _get_session_factory
from marquee.main import app
from marquee.models import Job, JobArtifact, JobAttempt, MlActivePublication, Movie


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as value:
        yield value


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


@pytest.mark.asyncio
async def test_ml_routes_submit_generation_snapshots_without_manual_activation(client, db) -> None:
    routes = (
        ("/api/taste/retrain", "taste_rebuild", "taste_profile", "gpu"),
        ("/api/taste/map/rebuild", "taste_map", "taste_map", "cpu"),
        ("/api/taste/enrich", "taste_enrich", "taste_profile", "cpu"),
        ("/api/taste/head/retrain", "learned_head_train", "learned_head", "cpu"),
    )
    for route, job_type, family, entrypoint in routes:
        response = await client.post(route)
        assert response.status_code == 202, response.text
        job = await db.get(Job, response.json()["job_id"])
        assert job is not None
        assert job.type == job_type
        assert job.plan["entrypoint"] == entrypoint
        assert job.plan["effect_safety"] == "read_only"
        assert job.subject_kind == "model_profile_training"
        assert job.request["expected_generation"] == 0
        assert job.request["seed"] == 0
        assert (
            await db.scalar(
                select(MlActivePublication).where(MlActivePublication.family == f"{family}:movies")
            )
            is None
        )

    activation = await client.post("/api/taste/profiles/untrusted/activate")
    assert activation.status_code == 404


@pytest.mark.asyncio
async def test_rescan_route_is_canonical_media_read_submission(client, db) -> None:
    response = await client.post("/api/pipeline/rescan-posters")
    assert response.status_code == 202, response.text
    job = await db.get(Job, response.json()["job_id"])
    assert job is not None
    assert job.type == "poster_rescan"
    assert job.request == {"scope": "all"}
    assert job.subject_kind == "poster_candidate_set"
    assert job.plan["entrypoint"] == "media_read"
    assert job.plan["effect_safety"] == "read_only"


def test_c4_documents_are_strict_bounded_and_nonmutating() -> None:
    assert TasteRebuildRequestV1().expected_generation == 0
    assert TasteMapRequestV1().seed == 0
    assert TasteEnrichRequestV1().library == "movies"
    assert LearnedHeadTrainRequestV1().library == "movies"
    assert PosterRescanRequestV1(scope="movie", movie_id=1).movie_id == 1
    assert not {
        "deploy",
        "delete",
        "heal",
        "restore",
        "replacement",
    } & set(PosterRescanResultV1.model_fields)
    assert {"version", "checksum", "active_generation", "activated"} <= set(
        MlPublicationResultV1.model_fields
    )


def test_c4_has_one_executor_and_no_legacy_publication_bypass() -> None:
    expected = {
        "taste_rebuild",
        "taste_map",
        "taste_enrich",
        "learned_head_train",
        "poster_rescan",
    }
    assert expected <= set(EXECUTION_HANDLERS)
    assert not Path("marquee/core/jobs/builtin_handlers.py").exists()

    ml_source = Path("marquee/core/jobs/ml_publication.py").read_text()
    assert "pg_advisory_xact_lock" in ml_source
    assert "owns_current_attempt" in ml_source
    assert ml_source.count("cancellation.cancel_called") >= 2

    rescan_source = Path("marquee/core/jobs/handlers_rescan.py").read_text()
    tree = ast.parse(rescan_source)
    calls = {
        getattr(node.func, "attr", None) for node in ast.walk(tree) if isinstance(node, ast.Call)
    }
    assert not {"unlink", "replace", "remove", "heal", "deploy"} & calls


async def _publication_attempt(db, suffix: str, *, fence_token: int = 1):
    job_id = hashlib.sha256(f"jmc4c-ml-{suffix}".encode()).hexdigest()[:32]
    job = Job(
        id=job_id,
        type="taste_rebuild",
        request={},
        phase="running",
        root_id=job_id,
        subject_kind="model_profile_training",
        subject_reference="taste_profile:movies",
        subject_snapshot={
            "version": 1,
            "kind": "model_profile_training",
            "display_id": "ml:taste_profile:movies",
            "display_name": "Taste profile (movies)",
            "subject_type": "training",
            "name": "Taste profile",
        },
        fence_token=fence_token,
        started_at=datetime.now(UTC),
    )
    db.add(job)
    await db.flush()
    attempt = JobAttempt(
        job_id=job.id,
        number=1,
        fence_token=fence_token,
        phase="running",
    )
    db.add(attempt)
    await db.flush()
    job.current_attempt_id = attempt.id
    artifacts = [
        JobArtifact(
            job_id=job.id,
            attempt_id=attempt.id,
            kind="taste_profile",
            name=f"version-{index}",
            status="available",
            virtual_source={"test": index},
            checksum=str(index) * 64,
        )
        for index in (1, 2, 3)
    ]
    db.add_all(artifacts)
    await db.commit()
    return job, attempt, artifacts


@pytest.mark.asyncio
async def test_ml_activation_is_fenced_cas_and_preserves_prior_version(db) -> None:
    job, attempt, artifacts = await _publication_attempt(db, "cas")

    class Writer:
        async def owns_current_attempt(self, _session) -> bool:
            return True

    context = SimpleNamespace(
        cancellation=SimpleNamespace(cancel_called=False),
        delivery=SimpleNamespace(canonical_job_id=job.id),
        attempt=SimpleNamespace(attempt_id=attempt.id, fence_token=1),
        session_factory=_get_session_factory(),
        writer=Writer(),
    )
    first = await activate_immutable_artifact(
        context,
        family="taste_profile:movies",
        expected_generation=0,
        version="v1-first",
        artifact=artifacts[0],
    )
    assert first.activated is True
    assert first.generation == 1

    conflict = await activate_immutable_artifact(
        context,
        family="taste_profile:movies",
        expected_generation=0,
        version="v1-conflict",
        artifact=artifacts[1],
    )
    assert conflict.activated is False
    assert conflict.version == "v1-first"

    context.cancellation.cancel_called = True
    with pytest.raises(asyncio.CancelledError):
        await activate_immutable_artifact(
            context,
            family="taste_profile:movies",
            expected_generation=1,
            version="v1-cancelled",
            artifact=artifacts[2],
        )
    active = await db.get(MlActivePublication, "taste_profile:movies")
    assert active is not None
    assert active.generation == 1
    assert active.artifact_id == artifacts[0].id


@pytest.mark.asyncio
async def test_ml_activation_rejects_stale_writer_before_pointer_change(db) -> None:
    job, attempt, artifacts = await _publication_attempt(db, "stale", fence_token=4)

    class StaleWriter:
        async def owns_current_attempt(self, _session) -> bool:
            return False

    context = SimpleNamespace(
        cancellation=SimpleNamespace(cancel_called=False),
        delivery=SimpleNamespace(canonical_job_id=job.id),
        attempt=SimpleNamespace(attempt_id=attempt.id, fence_token=4),
        session_factory=_get_session_factory(),
        writer=StaleWriter(),
    )
    with pytest.raises(MlPublicationError, match="stale attempt"):
        await activate_immutable_artifact(
            context,
            family="taste_profile:tv",
            expected_generation=0,
            version="v1-stale",
            artifact=artifacts[0],
        )
    assert await db.get(MlActivePublication, "taste_profile:tv") is None


@pytest.mark.asyncio
async def test_ml_handler_registers_loadable_immutable_artifact_then_activates(
    db, monkeypatch
) -> None:
    import numpy as np

    from marquee.core.jobs.internal_runner_host import RunnerFile, RunnerOutcome

    job, attempt, _artifacts = await _publication_attempt(db, "handler")

    class Writer:
        async def owns_current_attempt(self, _session) -> bool:
            return True

    workspace = AttemptWorkspaceManager.for_data_dir(settings.data_dir_path).create(
        job_id=job.id,
        attempt_id=attempt.id,
        fence_token=1,
    )
    workspace_dir = workspace.directory.root.resolved() / workspace.directory.key.value

    async def _fake_run(launcher, *, operation, manifest, **kwargs):
        emb = np.zeros((2, 512), dtype=np.float32)
        emb[0, 0], emb[1, 0] = 0.1, 0.2
        np.savez(
            workspace_dir / "profile.npz",
            model_name="clip-vit-b-32",
            dino_model_name="dinov2-vits14",
            embeddings=emb,
            centroid_emb=emb.mean(axis=0),
            poster_names=np.array(["a.jpg", "b.jpg"]),
            asset_kinds=np.array(["movie", "movie"]),
        )
        return RunnerOutcome(
            outcome="succeeded",
            summary={"family": "taste_profile", "library": "movies", "exemplars": 2},
            files=(RunnerFile("profile.npz", "0" * 64, 1),),
            ready=True,
            exit_code=0,
        )

    monkeypatch.setattr("marquee.core.jobs.internal_runner_host.run_internal_operation", _fake_run)

    context = SimpleNamespace(
        cancellation=SimpleNamespace(cancel_called=False),
        delivery=SimpleNamespace(canonical_job_id=job.id),
        attempt=SimpleNamespace(attempt_id=attempt.id, fence_token=1),
        session_factory=_get_session_factory(),
        writer=Writer(),
        workspace=workspace,
        process_launcher=None,
        request={
            "source": "training_dir",
            "library": "movies",
            "expected_generation": 0,
            "seed": 7,
        },
        configuration={"CLIP_MODEL": "test-model"},
        subject={"kind": "model_profile_training", "name": "Taste profile"},
    )
    result = await execute_taste_rebuild(context)
    assert result["outcome"] == "succeeded"
    assert result["activated"] is True
    assert result["active_generation"] == 1
    assert result["checksum"]

    active = await db.get(MlActivePublication, "taste_profile:movies")
    assert active is not None
    artifact = await db.get(JobArtifact, active.artifact_id)
    assert artifact is not None
    assert artifact.kind == "taste_profile"
    assert artifact.status == "available"
    stored = settings.data_dir_path / artifact.storage_key
    assert stored.is_file()
    assert stored.stat().st_mode & 0o777 == 0o400


@pytest.mark.asyncio
async def test_poster_rescan_records_changed_then_unchanged_without_deleting(
    db, tmp_path, monkeypatch
) -> None:
    poster = tmp_path / "movie" / "poster.jpg"
    poster.parent.mkdir(parents=True)
    poster.write_bytes(b"bounded-poster-evidence")
    movie = Movie(
        title="Observed",
        year=2026,
        folder_path=str(poster.parent),
        movie_file_path=str(poster.parent / "movie.mkv"),
        tmdb_id=424242,
    )
    db.add(movie)
    await db.flush()
    job_id = hashlib.sha256(b"jmc4c-poster-rescan-handler").hexdigest()[:32]
    job = Job(
        id=job_id,
        type="poster_rescan",
        request={"scope": "all"},
        phase="running",
        root_id=job_id,
        subject_kind="poster_candidate_set",
        subject_reference="all",
        subject_snapshot={
            "version": 1,
            "kind": "poster_candidate_set",
            "display_id": "posters:all",
            "display_name": "All poster subjects",
            "media_kind": "movie",
            "subject_id": 0,
            "title": "All poster subjects",
        },
        fence_token=1,
        started_at=datetime.now(UTC),
    )
    db.add(job)
    await db.flush()
    attempt = JobAttempt(job_id=job.id, number=1, fence_token=1, phase="running")
    db.add(attempt)
    await db.flush()
    job.current_attempt_id = attempt.id
    await db.commit()

    monkeypatch.setattr(settings, "MEDIA_ROOTS", [str(tmp_path)])
    monkeypatch.setattr(settings, "RADARR_PATH_PREFIX", None)
    monkeypatch.setattr(settings, "RADARR_MEDIA_PATH", None)

    class Writer:
        async def owns_current_attempt(self, _session) -> bool:
            return True

    context = SimpleNamespace(
        cancellation=SimpleNamespace(cancel_called=False),
        delivery=SimpleNamespace(canonical_job_id=job.id),
        attempt=SimpleNamespace(attempt_id=attempt.id, fence_token=1),
        session_factory=_get_session_factory(),
        writer=Writer(),
        workspace=AttemptWorkspaceManager.for_data_dir(settings.data_dir_path).create(
            job_id=job.id,
            attempt_id=attempt.id,
            fence_token=1,
        ),
        request={"scope": "all"},
    )
    changed = await execute_poster_rescan(context)
    assert changed["outcome"] == "succeeded"
    assert changed["changed"] == 1
    assert changed["missing"] == 0
    assert changed["artifact_ids"]
    await db.refresh(movie)
    assert movie.poster_path == str(poster)
    assert poster.is_file()

    attempt2 = JobAttempt(job_id=job.id, number=2, fence_token=2, phase="running")
    db.add(attempt2)
    await db.flush()
    job.current_attempt_id = attempt2.id
    job.fence_token = 2
    await db.commit()
    context2 = SimpleNamespace(
        cancellation=SimpleNamespace(cancel_called=False),
        delivery=SimpleNamespace(canonical_job_id=job.id),
        attempt=SimpleNamespace(attempt_id=attempt2.id, fence_token=2),
        session_factory=_get_session_factory(),
        writer=Writer(),
        workspace=AttemptWorkspaceManager.for_data_dir(settings.data_dir_path).create(
            job_id=job.id,
            attempt_id=attempt2.id,
            fence_token=2,
        ),
        request={"scope": "all"},
    )
    unchanged = await execute_poster_rescan(context2)
    assert unchanged["outcome"] == "no_change"
    assert unchanged["changed"] == 0
    assert poster.is_file()
