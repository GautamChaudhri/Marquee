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
    MlPublicationResultV1,
    PosterRescanRequestV1,
    PosterRescanResultV1,
    RankingResidualTrainRequestV1,
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
from marquee.ml import publication_catalog
from marquee.ml.residual import freeze_residual_evidence
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
        ("/api/taste/map/rebuild", "taste_map", "taste_map", "cpu"),
        ("/api/taste/enrich", "taste_enrich", "taste_profile", "cpu"),
        (
            "/api/taste/residual/retrain",
            "ranking_residual_train",
            "ranking_residual",
            "cpu",
        ),
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
        if job_type == "ranking_residual_train":
            # The worker recomputes this immutable revision before any residual
            # evaluation. A wall-clock token would make every manual request
            # stale at the execution boundary.
            assert job.request["evidence_revision"] == freeze_residual_evidence([]).digest
        assert (
            await db.scalar(
                select(MlActivePublication).where(MlActivePublication.family == f"{family}:movies")
            )
            is None
        )

    activation = await client.post("/api/taste/profiles/untrusted/activate")
    assert activation.status_code == 404

    canonical_only = await client.post("/api/taste/retrain")
    assert canonical_only.status_code == 409
    assert "canonical taste evidence" in canonical_only.json()["detail"]


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


def test_publication_documents_are_strict_bounded_and_nonmutating() -> None:
    assert TasteRebuildRequestV1(expected_generation=0).expected_generation == 0
    assert TasteMapRequestV1().seed == 0
    assert TasteEnrichRequestV1().library == "movies"
    assert RankingResidualTrainRequestV1().library == "movies"
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


def test_publication_has_one_executor_and_no_legacy_bypass() -> None:
    expected = {
        "taste_rebuild",
        "taste_map",
        "taste_enrich",
        "ranking_residual_train",
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
    job_id = hashlib.sha256(f"ml-publication-{suffix}".encode()).hexdigest()[:32]
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
    job_id = hashlib.sha256(b"poster-rescan-handler").hexdigest()[:32]
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


@pytest.mark.asyncio
async def test_tv_retrain_submits_a_library_scan_without_asking_the_coordinator(client, db) -> None:
    """Movies need canonical evidence to be due; TV trains on artwork already on disk."""
    movies = await client.post("/api/taste/retrain", json={"library": "movies"})
    assert movies.status_code == 409
    assert "canonical taste evidence" in movies.json()["detail"]

    response = await client.post("/api/taste/retrain", json={"library": "tv"})

    assert response.status_code == 202, response.text
    job = await db.get(Job, response.json()["job_id"])
    assert job is not None
    assert job.type == "taste_rebuild"
    assert job.request["source"] == "library"
    assert job.request["library"] == "tv"
    assert job.request["expected_generation"] == 0
    # No frozen evidence revision and no coordinator build lineage: pressing the
    # button is the whole trigger.
    assert job.request.get("revision") is None
    assert job.request.get("profile_build_id") is None
    assert job.subject_reference == "taste_profile:tv"


def _write_profile(path: Path, *, names: list[str], **extra) -> Path:
    """Minimal loadable taste profile: enough keys for NumpyTasteStore, nothing more."""
    import numpy as np

    from marquee.core.pipeline_config import pipeline_settings

    count = len(names)
    embeddings = np.zeros((count, 512), dtype=np.float32)
    for index in range(count):
        embeddings[index, 0] = 0.1 * (index + 1)
    np.savez(
        path,
        model_name=pipeline_settings.AI_MODEL,
        embeddings=embeddings,
        centroid_emb=embeddings.mean(axis=0),
        poster_names=np.array(names),
        **extra,
    )
    return path


def test_profile_payload_groups_tv_posters_by_series_not_by_filename(tmp_path) -> None:
    """A show plus its seasons is one subject with several assets, not several movies."""
    import numpy as np

    path = _write_profile(
        tmp_path / "profile.npz",
        names=["show-1.jpg", "season-1-01.jpg", "season-1-02.jpg", "show-9.jpg"],
        series_titles=np.array(
            ["Dark Matter (2024)", "Dark Matter (2024)", "Dark Matter (2024)", "Moon Knight"]
        ),
        season_numbers=np.array([-1, 1, 2, -1], dtype=np.int32),
        asset_kinds=np.array(["show", "season", "season", "show"]),
    )

    summary, subjects, duplicates = publication_catalog._profile_payload(path)

    assert summary["unique_subjects"] == 2
    assert summary["total_assets"] == 4
    assert summary["by_kind"] == {"show": 2, "season": 2}
    # The staged filenames carry series ids; the payload must carry titles.
    assert [subject["title"] for subject in subjects] == ["Dark Matter", "Moon Knight"]
    dark_matter = subjects[0]
    assert dark_matter["year"] == 2024
    assert dark_matter["contribution_count"] == 3
    assert dark_matter["asset_summary"] == "1 show · 2 seasons"
    assert [asset["label"] for asset in dark_matter["assets"]] == [
        "Dark Matter (2024)",
        "Dark Matter (2024) · Season 1",
        "Dark Matter (2024) · Season 2",
    ]
    # A show and its seasons share a title but are different assets.
    assert duplicates == []


def test_profile_payload_collapses_the_legacy_copy_counter(tmp_path) -> None:
    """"Title (Year) - 2.jpg" is a second copy of one film, not a second film."""
    path = _write_profile(
        tmp_path / "profile.npz",
        names=["Alien (1979).jpg", "Alien (1979) - 2.jpg", "Alien (1979) - 3.jpg", "Dune (2021).jpg"],
    )

    summary, subjects, duplicates = publication_catalog._profile_payload(path)

    assert summary["unique_subjects"] == 2
    assert summary["duplicate_groups"] == 1
    assert summary["duplicate_exemplars"] == 2
    assert [(subject["title"], subject["contribution_count"]) for subject in subjects] == [
        ("Alien", 3),
        ("Dune", 1),
    ]
    assert duplicates[0]["label"] == "Alien (1979)"
    assert duplicates[0]["count"] == 3


def test_profile_payload_resolves_opaque_exemplar_filenames(tmp_path) -> None:
    """Frozen-evidence builds name posters after exemplar ids; the snapshot names the film."""
    path = _write_profile(tmp_path / "profile.npz", names=["deadbeef.jpg", "cafe1234.jpg"])
    resolver = {
        "deadbeef": {"title": "Heat", "year": 1995, "tmdb_id": 949, "movie_id": 3},
        "cafe1234": {"title": "Heat", "year": 1995, "tmdb_id": 949, "movie_id": 3},
    }

    summary, subjects, duplicates = publication_catalog._profile_payload(path, resolver)

    assert summary["unique_subjects"] == 1
    assert subjects[0]["title"] == "Heat"
    assert subjects[0]["contribution_count"] == 2
    assert duplicates[0]["count"] == 2


def test_profile_payload_still_falls_back_to_the_filename(tmp_path) -> None:
    """No series title, no resolver entry: the filename is all there is, and it works."""
    path = _write_profile(tmp_path / "profile.npz", names=["a.jpg", "b.jpg"])

    summary, subjects, duplicates = publication_catalog._profile_payload(path)

    assert [subject["title"] for subject in subjects] == ["a", "b"]
    assert summary["unique_movies"] == 2
    assert duplicates == []


@pytest.mark.asyncio
async def test_taste_status_omits_genres_for_tv_and_reads_gates_from_settings(
    client, monkeypatch
) -> None:
    """`series` has no genres column, so TV must report none rather than movie genres."""
    from marquee.core.pipeline_config import pipeline_settings

    monkeypatch.setattr(pipeline_settings, "RESIDUAL_MIN_SUBJECTS", 7, raising=False)
    monkeypatch.setattr(pipeline_settings, "RESIDUAL_MIN_PAIRS", 11, raising=False)

    tv = (await client.get("/api/taste/status", params={"library": "tv"})).json()
    assert tv["labels"]["genres"] == {}
    assert tv["labels"]["genres_available"] is False
    assert tv["labels"]["subjects"] == tv["labels"]["movies"]
    # The gauges must quote the same thresholds the trainer gates on.
    assert tv["ranking_residual"]["activation"]["subjects"]["need"] == 7
    assert tv["ranking_residual"]["activation"]["pairs"]["need"] == 11

    movies = (await client.get("/api/taste/status", params={"library": "movies"})).json()
    assert movies["labels"]["genres_available"] is True


@pytest.mark.asyncio
async def test_seeding_bundle_retrain_bypasses_the_coordinator_for_movies_only(
    client, db
) -> None:
    """TEMPORARY (seeding bundle): the one caller-chosen training source."""
    blocked = await client.post(
        "/api/taste/retrain", json={"library": "tv", "source": "seeding_bundle"}
    )
    assert blocked.status_code == 400
    assert "movies profile" in blocked.json()["detail"]

    response = await client.post(
        "/api/taste/retrain", json={"library": "movies", "source": "seeding_bundle"}
    )

    assert response.status_code == 202, response.text
    job = await db.get(Job, response.json()["job_id"])
    assert job is not None
    assert job.type == "taste_rebuild"
    assert job.request["source"] == "seeding_bundle"
    assert job.request["library"] == "movies"
    assert job.subject_reference == "taste_profile:movies"
    # No frozen evidence and no coordinator lineage, exactly like the TV button.
    assert job.request.get("revision") is None
    assert job.request.get("profile_build_id") is None
    # The document the handler parses must accept what the route submitted.
    assert TasteRebuildRequestV1.model_validate(job.request).source == "seeding_bundle"


@pytest.mark.asyncio
async def test_seeding_bundle_staging_skips_dotfiles_and_enforces_a_floor(
    tmp_path, monkeypatch
) -> None:
    """A stray .actors.jpg sidecar must not become an exemplar named ".actors"."""
    from marquee.core.jobs.handlers_ml import _stage_seeding_bundle
    from marquee.core.pipeline_config import pipeline_settings

    bundle = tmp_path / "seeding" / "movies"
    bundle.mkdir(parents=True)
    (bundle / ".actors.jpg").write_bytes(b"sidecar")
    (bundle / ".genre_cache.json").write_text("{}")
    for index in range(3):
        (bundle / f"Film {index} (200{index}).jpg").write_bytes(b"poster")

    monkeypatch.setattr(pipeline_settings, "TASTE_SEEDING_DIR", tmp_path / "seeding")
    monkeypatch.setattr(pipeline_settings, "TASTE_SEEDING_MIN_POSTERS", 3)

    class _Io:
        async def copy(self, source: Path, destination: Path):
            destination.write_bytes(source.read_bytes())

    workspace_dir = tmp_path / "workspace"
    context = SimpleNamespace(io=_Io())
    staged = await _stage_seeding_bundle(context, workspace_dir, library="movies")

    assert staged == 3
    names = sorted(path.name for path in (workspace_dir / "training").iterdir())
    assert names == ["Film 0 (2000).jpg", "Film 1 (2001).jpg", "Film 2 (2002).jpg"]

    monkeypatch.setattr(pipeline_settings, "TASTE_SEEDING_MIN_POSTERS", 50)
    with pytest.raises(RuntimeError, match="at least 50 posters"):
        await _stage_seeding_bundle(context, tmp_path / "w2", library="movies")

    with pytest.raises(RuntimeError, match="not found"):
        await _stage_seeding_bundle(context, tmp_path / "w3", library="tv")
