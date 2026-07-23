"""JMC6I §7 — executable behavioral certification (I4).

Every in-scope behavioral claim is proven by executing the declared chain in the
*current* pytest session:

    canonical producer → PgQueuer delivery/execution context → fixed runner
    → real progress/evidence/result → terminal state → declared API consumer

The scenarios below drive the real route/command producers, the real delivery
kernel (admission, safety gates, workspace, launcher, fenced writer, terminal
decision), the shared typed progress bridge, and the declared consumers. The
model-heavy runner interior is driven with a controlled outcome that emits the
real frame shapes; the fixed-runner transport itself executes as a real contained
subprocess in the canary scenario, and the opt-in live smokes execute the real
models. The final report distinguishes executed, unavailable live-capability,
deferred-JMC6J, and failed evidence — an unavailable capability is never
reported as passed.

The conftest evidence recorder runs this module last; the report test consumes
the session's executed-node evidence. Negative controls prove that nonexistent,
unexecuted, skipped, stale, and consumerless claims fail certification.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

import anyio
import numpy as np
import pytest
from httpx import ASGITransport, AsyncClient
from pgqueuer.models import Context
from pgqueuer.models import Job as PgQueuerJob
from sqlalchemy import select

from marquee.core.jobs.commands import create_system_noop
from marquee.core.jobs.delivery import deliver_job
from marquee.core.jobs.progress import JobProgress
from marquee.database import _get_session_factory
from marquee.main import app
from marquee.models import (
    Job,
    JobArtifact,
    Movie,
    PipelineRun,
    PosterPreferenceEvent,
    TasteExemplar,
)
from marquee.models.job import JobDispatch
from marquee.models.ml_publication import MlActivePublication
from tests.support.jmc6i_certification import (
    ExecutionEvidence,
    load_classification,
    resolve_node,
    verify_claims,
)

IN_SCOPE_DEFINITIONS = frozenset(
    {"poster_pipeline", "taste_rebuild", "taste_map", "taste_enrich", "system_noop"}
)


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")


def _transport(job_id: str, pgq_job_id: int, *, entrypoint: str) -> PgQueuerJob:
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
        payload=(
            f'{{"dispatch_generation":1,"job_id":"{job_id}","payload_version":1}}'.encode()
        ),
        attempts=0,
        queue_manager_id=uuid4(),
        headers=None,
    )


async def _deliver(db, job_id: str, *, entrypoint: str) -> None:
    """Deliver the canonical dispatch ticket through the real execution kernel."""
    await db.rollback()
    dispatch = await db.scalar(
        select(JobDispatch).where(JobDispatch.job_id == job_id, JobDispatch.generation == 1)
    )
    assert dispatch is not None and dispatch.pgq_job_id is not None
    pgq_job_id = dispatch.pgq_job_id
    await db.rollback()
    await deliver_job(
        _transport(job_id, pgq_job_id, entrypoint=entrypoint),
        Context(cancellation=anyio.CancelScope()),
        expected_entrypoint=entrypoint,
    )
    await db.rollback()
    db.expire_all()


async def _terminal_job(job_id: str) -> Job:
    async with _get_session_factory()() as session:
        job = await session.scalar(select(Job).where(Job.id == job_id))
        assert job is not None
        return job


async def _seed_canonical_taste_evidence(db, data_dir: Path, *, count: int = 50) -> None:
    """Create the minimum trusted preference evidence required by the rebuild route."""
    job_id = uuid4().hex
    source_job = Job(
        id=job_id,
        type="poster_deploy",
        payload_version=1,
        request={},
        phase="running",
        desired_state="run",
        fence_token=1,
        root_id=job_id,
        trigger_kind="manual",
        feature_area="posters",
        presentation_family="posters",
        subject_kind="movie",
        subject_reference="jmc6i-taste-evidence",
        subject_snapshot={"version": 1, "kind": "movie", "title": "JMC6I taste evidence"},
    )
    db.add(source_job)
    await db.flush()
    for index in range(count):
        payload = f"jmc6i canonical taste evidence {index}".encode()
        digest = sha256(payload).hexdigest()
        storage_key = f"jmc6i/taste-evidence/{index}.jpg"
        physical_artifact = data_dir / storage_key
        physical_artifact.parent.mkdir(parents=True, exist_ok=True)
        physical_artifact.write_bytes(payload)
        artifact = JobArtifact(
            job_id=source_job.id,
            kind="taste_exemplar",
            name=f"example-{index}.jpg",
            status="available",
            storage_key=storage_key,
            content_type="image/jpeg",
            size_bytes=len(payload),
            checksum=digest,
            retention_class="pinned",
            artifact_metadata={"fixture": "jmc6i"},
        )
        db.add(artifact)
        await db.flush()
        event = PosterPreferenceEvent(
            id=uuid4().hex,
            version=1,
            idempotency_key=f"jmc6i:taste-evidence:{index}",
            namespace="global",
            subject_kind="movie",
            subject_reference=f"movie-{index}",
            subject_snapshot={"id": index, "title": f"Movie {index}"},
            action="selection",
            exposed_candidates=[{"candidate_id": f"candidate-{index}"}],
            presentation_order=[f"candidate-{index}"],
            training_context={"fixture": "jmc6i"},
            confidence="explicit",
            initiator={"kind": "test"},
        )
        db.add(event)
        db.add(
            TasteExemplar(
                id=uuid4().hex,
                version=1,
                namespace="global",
                polarity="positive",
                evidence_weight=1.0,
                evidence_source="explicit_selection",
                subject_kind="movie",
                subject_reference=f"movie-{index}",
                subject_snapshot={"id": index, "title": f"Movie {index}"},
                retained_artifact_id=artifact.id,
                preference_event_id=event.id,
                deployment_job_id=source_job.id,
                initiator={"kind": "test"},
                asset_key=artifact.storage_key,
                checksum=digest,
                content_type="image/jpeg",
                status="active",
                activated_at=datetime.now(UTC),
            )
        )
    await db.commit()


def _write_valid_taste_profile(path: Path) -> None:
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


def _write_valid_taste_map(path: Path) -> None:
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


def _workspace_of(monkeypatch_target: dict) -> Path:
    workspace = monkeypatch_target.get("workspace")
    assert workspace is not None, "controlled runner ran before the workspace was known"
    return workspace


def _controlled_runner(monkeypatch, *, stage_files, frames=(), outcome_summary, files):
    """Substitute the model-heavy runner interior with a controlled outcome.

    The controlled callable still receives the real handler-provided bridge
    callback and emits the declared real frame shapes through it, and stages the
    declared workspace files, so everything around the runner boundary — bridge,
    validation, registration, projection, activation, terminal decision — runs
    for real. The transport itself is certified by the contained canary scenario
    and the opt-in live smokes.
    """
    from marquee.core.jobs.internal_runner_host import RunnerFile, RunnerOutcome

    captured: dict = {}

    async def fake_run(launcher, *, operation, manifest, on_progress=None, **kwargs):
        workspace = _workspace_of(captured)
        for frame in frames:
            if on_progress is not None:
                await on_progress(frame)
        stage_files(workspace)
        return RunnerOutcome(
            outcome="succeeded",
            summary=outcome_summary,
            files=tuple(RunnerFile(name, "0" * 64, 1) for name in files),
            ready=True,
            exit_code=0,
        )

    monkeypatch.setattr("marquee.core.jobs.internal_runner_host.run_internal_operation", fake_run)
    return captured


def _capture_workspace(monkeypatch, captured: dict) -> None:
    """Record each attempt's confined workspace directory as delivery creates it."""
    from marquee.core.jobs.workspaces import AttemptWorkspaceManager

    original = AttemptWorkspaceManager.create

    def recording_create(self, **kwargs):
        workspace = original(self, **kwargs)
        directory = workspace.directory
        captured["workspace"] = directory.root.resolved() / directory.key.value
        return workspace

    monkeypatch.setattr(AttemptWorkspaceManager, "create", recording_create)


# --------------------------------------------------------------------------- #
# Executable scenarios (producer → delivery → runner → progress → consumer)
# --------------------------------------------------------------------------- #


async def test_noop_scenario_producer_to_consumer(db, data_dir, installed_pgqueuer) -> None:
    """system_noop: canonical command producer → control delivery → terminal
    success → presentation consumer with a terminal snapshot."""
    job = await create_system_noop(
        db, payload={"echo": "jmc6i"}, idempotency_key=f"system_noop:jmc6i-{uuid4().hex}"
    )
    job_id = job.id
    await _deliver(db, job_id, entrypoint="control")

    terminal = await _terminal_job(job_id)
    assert (terminal.phase, terminal.outcome) == ("terminal", "succeeded")
    assert terminal.result["summary"] == {"echo": "jmc6i"}
    async with _client() as client:
        presentation = await client.get(f"/api/jobs/{job_id}/presentation")
    assert presentation.status_code == 200
    progress = presentation.json()["progress"]
    assert progress["freshness"] == "terminal"


async def test_runner_canary_scenario_contained_transport(tmp_path: Path) -> None:
    """The fixed-runner transport leg executes as a real contained subprocess:
    ready/progress/result frames, confined validated output, tree death."""
    from marquee.core.filesystem import FilesystemBoundary, RootSpec
    from marquee.core.jobs.internal_runner_host import run_internal_operation
    from marquee.core.jobs.process_identity import ProcessIdentity, process_group_exists
    from marquee.core.jobs.process_launcher import ProcessLauncher
    from marquee.core.jobs.runner_protocol import RunnerOperation

    pids: list[int] = []

    async def record(identity: ProcessIdentity) -> bool:
        pids.append(identity.process_group_id)
        return True

    work = tmp_path / "work"
    work.mkdir()
    boundary = FilesystemBoundary(
        {"test": RootSpec(name="test", path=tmp_path, purpose="test", access="read_write")}
    )
    launcher = ProcessLauncher(
        worker_node="jmc6i-cert",
        boundary=boundary,
        working_directory=boundary.classify(work),
        record_identity=record,
    )
    frames: list[dict] = []

    async def on_progress(frame: dict) -> None:
        frames.append(frame)

    outcome = await run_internal_operation(
        launcher,
        operation=RunnerOperation.NOOP,
        manifest={
            "params": {
                "stages": 2,
                "echo": "transport",
                "produce_file": {"name": "evidence.bin", "content": "certified"},
            }
        },
        on_progress=on_progress,
        resolve_output=lambda key: work / key,
    )
    assert outcome.succeeded and outcome.exit_code == 0
    assert outcome.summary == {"echo": "transport"}
    assert [frame["type"] for frame in frames] == ["progress", "progress"]
    assert (work / "evidence.bin").read_bytes() == b"certified"
    assert pids and not process_group_exists(pids[0])


_POSTER_FRAMES = (
    {"v": 1, "type": "progress", "stage": "fetch", "state": "start", "total": 6},
    {"v": 1, "type": "progress", "stage": "ocr", "state": "progress", "done": 5, "total": 12},
    {"v": 1, "type": "progress", "stage": "ocr", "state": "end", "survivors": 9},
    {"v": 1, "type": "progress", "stage": "rank", "state": "start", "total": 9},
)


async def test_poster_pipeline_scenario_producer_to_consumer(
    db, data_dir, installed_pgqueuer, monkeypatch
) -> None:
    """poster_pipeline: route producer → GPU delivery → contained-runner boundary
    → typed durable progress → canonical PipelineRun → run/presentation consumers."""
    from PIL import Image

    movie = Movie(
        title="Blade Runner 2049",
        year=2017,
        folder_path=str(data_dir / "library"),
        movie_file_path=str(data_dir / "library" / "movie.mkv"),
        tmdb_id=335984,
    )
    db.add(movie)
    await db.commit()

    captured = _controlled_runner(
        monkeypatch,
        stage_files=lambda workspace: (
            (workspace / "run.json").write_text(
                json.dumps(
                    {
                        "run_id": "certrun000001",
                        "candidates": [
                            {
                                "orig_filename": "poster_a.jpg",
                                "image_path": str(workspace / "candidate-000.jpg"),
                                "rank": 1,
                                "final_score": 0.9,
                            }
                        ],
                    }
                )
            ),
            Image.new("RGB", (8, 12), color=(10, 20, 30)).save(
                workspace / "candidate-000.jpg", format="JPEG"
            ),
        ),
        frames=_POSTER_FRAMES,
        outcome_summary={
            "pipeline_status": "completed",
            "run_id": "certrun000001",
            "counts": {"posters_found": 6, "ranked": 2, "style_gated": 1},
            "recommendation": {"orig_filename": "poster_a.jpg", "rank": 1, "final_score": 0.9},
            "scorer_name": "weighted",
            "source_count": 6,
            "candidate_count": 3,
            "candidate_files": {"poster_a.jpg": "candidate-000.jpg"},
        },
        files=("run.json",),
    )
    _capture_workspace(monkeypatch, captured)

    async with _client() as client:
        submission = await client.post(f"/api/pipeline/movie/{movie.id}/run")
    assert submission.status_code == 202, submission.text
    job_id = submission.json()["job_id"]

    await _deliver(db, job_id, entrypoint="gpu")

    terminal = await _terminal_job(job_id)
    assert (terminal.phase, terminal.outcome) == ("terminal", "succeeded")
    stored = JobProgress.model_validate(terminal.progress)
    assert stored.metrics.items_survived == 9
    assert stored.freshness.value == "terminal"

    async with _get_session_factory()() as session:
        run = await session.scalar(select(PipelineRun).where(PipelineRun.job_id == job_id))
        assert run is not None and run.run_id == "certrun000001"
        assert run.selected_artifact_id is not None
        assert run.archive_artifact_id is not None

    async with _client() as client:
        run_view = await client.get("/api/pipeline/runs/certrun000001")
        presentation = await client.get(f"/api/jobs/{job_id}/presentation")
    assert run_view.status_code == 200, run_view.text
    run_payload = run_view.json()
    assert run_payload["run_id"] == "certrun000001"
    assert run_payload["ranked"][0]["orig_filename"] == "poster_a.jpg"
    progress = presentation.json()["progress"]
    assert progress["metrics"]["items_survived"] == 9
    assert progress["overall"]["percent"] is not None


async def _activate_profile_via_route(db, data_dir: Path, monkeypatch) -> None:
    """Producer→delivery for taste_rebuild used as a staged prerequisite."""
    await _seed_canonical_taste_evidence(db, data_dir)
    captured = _controlled_runner(
        monkeypatch,
        stage_files=lambda workspace: _write_valid_taste_profile(workspace / "profile.npz"),
        frames=(
            {"v": 1, "type": "progress", "stage": "clip", "state": "progress",
             "done": 2, "total": 2, "cursor": 1},
        ),
        outcome_summary={"family": "taste_profile", "library": "movies", "exemplars": 2},
        files=("profile.npz",),
    )
    _capture_workspace(monkeypatch, captured)
    async with _client() as client:
        submission = await client.post("/api/taste/retrain", json={"library": "movies"})
    assert submission.status_code == 202, submission.text
    await _deliver(db, submission.json()["job_id"], entrypoint="gpu")


async def test_taste_rebuild_scenario_producer_to_consumer(
    db, data_dir, installed_pgqueuer, monkeypatch
) -> None:
    """taste_rebuild: route producer → GPU delivery → native artifact → fenced
    activation → taste profile API consumer observes the active publication."""
    await _seed_canonical_taste_evidence(db, data_dir)
    captured = _controlled_runner(
        monkeypatch,
        stage_files=lambda workspace: _write_valid_taste_profile(workspace / "profile.npz"),
        frames=(
            {"v": 1, "type": "progress", "stage": "starting", "state": "start", "cursor": 1},
            {"v": 1, "type": "progress", "stage": "clip", "state": "progress",
             "done": 1, "total": 2, "cursor": 2},
            {"v": 1, "type": "progress", "stage": "calibration", "state": "progress",
             "done": 2, "total": 2, "cursor": 3},
        ),
        outcome_summary={"family": "taste_profile", "library": "movies", "exemplars": 2},
        files=("profile.npz",),
    )
    _capture_workspace(monkeypatch, captured)

    async with _client() as client:
        submission = await client.post("/api/taste/retrain", json={"library": "movies"})
    assert submission.status_code == 202, submission.text
    job_id = submission.json()["job_id"]
    await _deliver(db, job_id, entrypoint="gpu")

    terminal = await _terminal_job(job_id)
    assert (terminal.phase, terminal.outcome) == ("terminal", "succeeded")
    stored = JobProgress.model_validate(terminal.progress)
    assert stored.overall.percent is not None

    async with _get_session_factory()() as session:
        active = await session.get(MlActivePublication, "taste_profile:movies")
        assert active is not None and active.generation == 1

    async with _client() as client:
        listing = await client.get("/api/taste/profiles")
    assert listing.status_code == 200
    profiles = listing.json()["profiles"]
    assert len(profiles) == 1 and profiles[0]["status"] == "active"
    assert profiles[0]["summary"]["exemplars"] == 2


async def test_taste_map_scenario_producer_to_consumer(
    db, data_dir, installed_pgqueuer, monkeypatch
) -> None:
    """taste_map: consumes the active profile, publishes a native map, and the
    map API consumer loads the activated generation."""
    await _activate_profile_via_route(db, data_dir, monkeypatch)

    def stage_map(workspace: Path) -> None:
        assert (workspace / "profile.npz").is_file(), (
            "the coordinator must stage the exact active profile generation"
        )
        _write_valid_taste_map(workspace / "map.npz")

    captured = _controlled_runner(
        monkeypatch,
        stage_files=stage_map,
        frames=(
            {"v": 1, "type": "progress", "stage": "load", "state": "start", "cursor": 1},
            {"v": 1, "type": "progress", "stage": "project", "state": "start", "cursor": 2},
        ),
        outcome_summary={"family": "taste_map", "library": "movies", "exemplars": 2},
        files=("map.npz",),
    )
    _capture_workspace(monkeypatch, captured)

    async with _client() as client:
        submission = await client.post("/api/taste/map/rebuild")
    assert submission.status_code == 202, submission.text
    job_id = submission.json()["job_id"]
    await _deliver(db, job_id, entrypoint="cpu")

    terminal = await _terminal_job(job_id)
    assert (terminal.phase, terminal.outcome) == ("terminal", "succeeded")

    async with _get_session_factory()() as session:
        active = await session.get(MlActivePublication, "taste_map:movies")
        assert active is not None and active.generation == 1

    async with _client() as client:
        map_view = await client.get("/api/taste/map")
    assert map_view.status_code == 200, map_view.text
    assert len(map_view.json()["points"]) == 2


async def test_taste_enrich_scenario_producer_to_consumer(
    db, data_dir, installed_pgqueuer, monkeypatch
) -> None:
    """taste_enrich: consumes the active profile and advances the same
    taste_profile authority; the profiles API observes the successor."""
    await _activate_profile_via_route(db, data_dir, monkeypatch)

    def stage_enriched(workspace: Path) -> None:
        assert (workspace / "source-profile.npz").is_file()
        _write_valid_taste_profile(workspace / "profile.npz")
        with np.load(workspace / "profile.npz", allow_pickle=False) as data:
            payload = {key: data[key] for key in data.files}
        payload["genres_json"] = np.array(['["Science Fiction"]', '["Drama"]'])
        payload["years"] = np.array([2020, 2021], dtype=np.int64)
        payload["tmdb_ids"] = np.array([1, 2], dtype=np.int64)
        np.savez(workspace / "profile.npz", **payload)

    captured = _controlled_runner(
        monkeypatch,
        stage_files=stage_enriched,
        frames=(
            {"v": 1, "type": "progress", "stage": "enriching", "state": "start", "cursor": 1},
            {"v": 1, "type": "progress", "stage": "resolve", "state": "progress",
             "done": 2, "total": 2, "cursor": 2},
        ),
        outcome_summary={
            "family": "taste_profile",
            "operation": "enrichment",
            "library": "movies",
            "exemplars": 2,
            "resolved": 2,
        },
        files=("profile.npz",),
    )
    _capture_workspace(monkeypatch, captured)

    async with _client() as client:
        submission = await client.post("/api/taste/enrich")
    assert submission.status_code == 202, submission.text
    job_id = submission.json()["job_id"]
    await _deliver(db, job_id, entrypoint="cpu")

    terminal = await _terminal_job(job_id)
    assert (terminal.phase, terminal.outcome) == ("terminal", "succeeded")

    async with _get_session_factory()() as session:
        active = await session.get(MlActivePublication, "taste_profile:movies")
        assert active is not None and active.generation == 2, (
            "enrichment must advance the same taste_profile authority"
        )
    async with _client() as client:
        listing = await client.get("/api/taste/profiles")
    assert listing.status_code == 200
    statuses = {row["id"]: row["status"] for row in listing.json()["profiles"]}
    assert list(statuses.values()).count("active") == 1


# --------------------------------------------------------------------------- #
# Negative controls: false, stale, skipped, unexecuted, consumerless claims
# --------------------------------------------------------------------------- #


def test_negative_nonexistent_node_reference_fails() -> None:
    assert not resolve_node("tests/test_jmc6i_executable_certification.py::test_never_written")
    assert not resolve_node("tests/test_missing_module.py::test_anything")
    evidence = ExecutionEvidence()
    classification = {
        "in_scope": {
            "system_noop": {
                "scenarios": ["tests/test_missing_module.py::test_anything"],
            }
        },
        "deferred_jmc6j": [],
    }
    report = verify_claims(
        evidence,
        classification,
        manifest={
            "definitions": [
                {
                    "job_type": "system_noop",
                    "certification_test": "tests/test_missing_module.py::test_anything",
                }
            ]
        },
    )
    assert report.failed and "does not resolve" in report.failed[0]


def test_negative_unexecuted_node_fails_certification() -> None:
    """A resolvable, selected, but never-executed node is not evidence."""
    evidence = ExecutionEvidence()
    node = (
        "tests/test_jmc6i_executable_certification.py::"
        "test_noop_scenario_producer_to_consumer"
    )
    evidence.mark_selected(node)
    classification = {
        "in_scope": {"system_noop": {"scenarios": [node]}},
        "deferred_jmc6j": [],
    }
    report = verify_claims(evidence, classification)
    assert report.failed and "not executed" in report.failed[0]


def test_negative_skipped_or_failed_node_is_not_executed_evidence() -> None:
    evidence = ExecutionEvidence()
    node = (
        "tests/test_jmc6i_executable_certification.py::"
        "test_noop_scenario_producer_to_consumer"
    )
    evidence.mark_selected(node)
    evidence.record(node, "skipped")
    assert not evidence.node_executed(node)
    report = verify_claims(
        evidence,
        {"in_scope": {"system_noop": {"scenarios": [node]}}, "deferred_jmc6j": []},
    )
    assert report.failed and "skipped" in report.failed[0]
    evidence.record(node, "failed")
    assert evidence.node_outcome(node) == "failed"
    # A later pass record cannot resurrect failed evidence.
    evidence.record(node, "passed")
    assert evidence.node_outcome(node) == "failed"


def test_negative_stale_manifest_claim_fails() -> None:
    evidence = ExecutionEvidence()
    report = verify_claims(
        evidence,
        {
            "in_scope": {"retired_definition": {"scenarios": []}},
            "deferred_jmc6j": [],
        },
    )
    assert report.failed and "stale claim" in report.failed[0]


def test_negative_manifest_node_not_in_executable_matrix_fails() -> None:
    """A static manifest string cannot point outside the collected scenario matrix."""
    manifest = load_classification()  # retain the checked-in classification's shape in this fixture
    closure = json.loads(
        (Path(__file__).parent / "fixtures" / "jmc6h" / "enabled_definition_closure.json").read_text()
    )
    doctored = json.loads(json.dumps(closure))
    for entry in doctored["definitions"]:
        if entry["job_type"] == "poster_pipeline":
            entry["certification_test"] = "tests/test_jmc6h_product_effect.py::test_placeholder"
            break
    report = verify_claims(ExecutionEvidence(), manifest, manifest=doctored)
    assert any("manifest certification node" in finding for finding in report.failed)


async def test_negative_schema_valid_placeholder_fails_consumer_assertion(
    db, data_dir, installed_pgqueuer
) -> None:
    """A handler returning schema-valid output without the declared product
    effect cannot satisfy the consumer assertion."""
    job = await create_system_noop(
        db, payload={"echo": "placeholder"}, idempotency_key=f"system_noop:jmc6i-{uuid4().hex}"
    )
    job_id = job.id
    await _deliver(db, job_id, entrypoint="control")
    terminal = await _terminal_job(job_id)
    assert terminal.outcome == "succeeded"  # schema-valid success…
    async with _get_session_factory()() as session:
        run = await session.scalar(select(PipelineRun).where(PipelineRun.job_id == job_id))
    with pytest.raises(AssertionError):
        assert run is not None, "the declared consumer never observed a product effect"


# --------------------------------------------------------------------------- #
# The report (runs last; consumes the session's executed evidence)
# --------------------------------------------------------------------------- #


def _static_field_acceptance(entry: dict) -> bool:
    required = ("job_type", "handler", "certification_test", "consumer", "real_product_effect")
    return all(isinstance(entry.get(key), str) and entry[key] for key in required)


def test_certification_claims_require_executed_node_evidence(request) -> None:
    """§7 (frozen at I0): a statically valid entry naming an unexecuted node must
    be rejectable through session execution evidence."""
    from tests.support.jmc6i_certification import load_closure_manifest

    manifest = load_closure_manifest()
    entries = {entry["job_type"]: entry for entry in manifest["definitions"]}
    for job_type in IN_SCOPE_DEFINITIONS:
        assert job_type in entries, f"manifest lost in-scope definition {job_type}"

    doctored = dict(entries["poster_pipeline"])
    doctored["certification_test"] = (
        "tests/test_jmc6i_executable_certification.py::test_scenario_that_never_ran"
    )
    assert _static_field_acceptance(doctored), (
        "static field acceptance cannot distinguish an unexecuted claim — "
        "which is exactly why executed evidence is required"
    )

    evidence = getattr(request.config, "jmc6i_execution_evidence", None)
    assert evidence is not None, (
        "no executed-node evidence is collected in this session; behavioral closure "
        "currently rests on static manifest strings and named files (plan §2.6)"
    )
    assert not evidence.node_executed(doctored["certification_test"]), (
        "a never-executed node must not count as executed evidence"
    )
    assert not resolve_node(doctored["certification_test"])


def test_executable_certification_report_is_green(request, tmp_path: Path) -> None:
    """Evaluate every in-scope claim against this session's executed evidence and
    emit the honest executed/unavailable/deferred/failed report."""
    evidence = getattr(request.config, "jmc6i_execution_evidence", None)
    assert evidence is not None
    classification = load_classification()
    report = verify_claims(evidence, classification)

    report_path = tmp_path / "jmc6i-certification-report.json"
    report_path.write_text(report.to_json())

    assert report.failed == [], report.to_json()
    assert set(report.executed) >= IN_SCOPE_DEFINITIONS, report.to_json()
    assert report.deferred_jmc6j, "JMC6J-owned scope must be recorded, never claimed closed"
    # Live capability is either genuinely executed (MARQUEE_LIVE_SMOKE=1) or
    # truthfully reported unavailable — never silently counted as passed.
    live_nodes = {
        node
        for claims in classification["in_scope"].values()
        for node in claims.get("live_capability", ())
    }
    reported_live = {
        node for nodes in report.executed.values() for node in nodes if node in live_nodes
    } | {node for nodes in report.unavailable.values() for node in nodes}
    assert reported_live == live_nodes, report.to_json()
