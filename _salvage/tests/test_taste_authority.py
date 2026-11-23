"""Taste authority: preference events, exemplar retention, polarity, and readiness.

The taste profile is the ranking authority, so every exemplar must be earned. Covers
bounded idempotent preference events, the rule that a positive exemplar requires a
successful deployment, activation pinned to exact bytes, undo that revokes without
rewriting history, retry and crash recovery lineage, and frozen polarity-complete profiles
where a negative exemplar actually changes the similarity penalty."""

from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import numpy as np
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from marquee.config import settings
from marquee.core.jobs.internal_runner import _run_taste_profile
from marquee.core.pipeline_config import pipeline_settings
from marquee.core.taste_preferences import (
    TastePreferenceError,
    activate_exemplar,
    append_preference_event,
    create_pending_exemplar,
    derive_readiness,
    evidence_revision,
    pin_candidate_artifact,
    profile_exemplar_manifest,
    profile_input_ids,
    profile_rebuild_due,
    reconcile_pending_onboarding_deployments,
    record_deployment_effect,
    record_deployment_retry_successor,
    record_onboarding_analysis_retry_successor,
    record_onboarding_analysis_submission,
    revoke_exemplar,
)
from marquee.main import app
from marquee.ml.artifact_codec import save_npz_atomic, unicode_array, unicode_scalar
from marquee.ml.taste_store import NumpyTasteStore
from marquee.models import (
    Job,
    JobArtifact,
    JobAttempt,
    OnboardingAnalysisSuccessor,
    TasteDeploymentSuccessor,
    TasteExemplar,
)


@pytest.fixture
async def onboarding_client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as value:
        yield value


def test_profile_inputs_overlay_global_with_only_the_requested_namespace():
    rows = [
        SimpleNamespace(id="global", namespace="global"),
        SimpleNamespace(id="movie", namespace="movies"),
        SimpleNamespace(id="tv", namespace="tv"),
    ]
    assert profile_input_ids(rows, "movies") == {"global", "movie"}
    assert profile_input_ids(rows, "tv") == {"global", "tv"}


def test_profile_rebuild_coalesces_additions_and_revocations():
    prior = {f"example-{index}" for index in range(20)}
    assert not profile_rebuild_due(prior | {"new"}, prior, threshold=2)
    assert profile_rebuild_due(prior | {"new-1", "new-2"}, prior, threshold=2)
    assert profile_rebuild_due(prior - {"example-0", "example-1"}, prior, threshold=2)
    assert profile_rebuild_due(prior, prior, threshold=10, force=True)


async def _candidate_artifact(db, *, name: str = "candidate.jpg"):
    job_id = uuid4().hex
    job = Job(
        id=job_id,
        type="poster_pipeline",
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
        subject_reference="movie-1",
        subject_snapshot={
            "version": 1,
            "kind": "movie",
            "display_id": "movie:1",
            "display_name": "Fixture",
            "snapshot_at": datetime.now(UTC).isoformat(),
            "movie_id": 1,
            "title": "Fixture",
            "year": 2026,
            "media_kind": "movie",
        },
    )
    db.add(job)
    await db.flush()
    attempt = JobAttempt(
        job_id=job_id,
        number=1,
        fence_token=1,
        phase="running",
        started_at=datetime.now(UTC),
    )
    db.add(attempt)
    await db.flush()
    job.current_attempt_id = attempt.id
    data = b"canonical-poster-bytes"
    key = f"test-artifacts/{job_id}/{name}"
    path = Path(settings.DATA_DIR) / key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    artifact = JobArtifact(
        job_id=job_id,
        attempt_id=attempt.id,
        kind="evidence_image",
        name=name,
        status="available",
        storage_key=key,
        content_type="image/jpeg",
        size_bytes=len(data),
        checksum=sha256(data).hexdigest(),
        artifact_metadata={"family": "poster_pipeline_candidate"},
    )
    db.add(artifact)
    await db.commit()
    return job, attempt, artifact


async def _deployment_job(db, pipeline_job: Job):
    job_id = uuid4().hex
    job = Job(
        id=job_id,
        type="poster_deploy",
        payload_version=1,
        request={
            "target_kind": "movie",
            "target_id": 1,
            "candidate": {
                "source": "pipeline_run",
                "storage_key": f"test-artifacts/{pipeline_job.id}/candidate.jpg",
                "run_id": "fixture-run",
                "candidate_reference": "candidate.jpg",
                "expected_checksum": "a" * 64,
            },
            "ai_selected": True,
            "user_approved": True,
        },
        phase="running",
        desired_state="run",
        fence_token=1,
        root_id=job_id,
        trigger_kind="manual",
        feature_area="posters",
        presentation_family="posters",
        subject_kind=pipeline_job.subject_kind,
        subject_reference=pipeline_job.subject_reference,
        subject_snapshot=pipeline_job.subject_snapshot,
    )
    db.add(job)
    await db.flush()
    attempt = JobAttempt(
        job_id=job_id,
        number=1,
        fence_token=1,
        phase="running",
        started_at=datetime.now(UTC),
    )
    db.add(attempt)
    await db.flush()
    job.current_attempt_id = attempt.id
    await db.commit()
    return job, attempt


async def _selection_event(db, artifact, *, key: str = "select:movie-1"):
    return await append_preference_event(
        db,
        idempotency_key=key,
        namespace="global",
        subject_kind="movie",
        subject_reference="movie-1",
        subject_snapshot={"title": "Fixture", "year": 2026},
        action="selection",
        exposed_candidates=[{"candidate_id": "candidate-1", "artifact_id": artifact.id}],
        presentation_order=["candidate-1"],
        training_context={"personalization_mode": "collecting"},
        confidence="explicit",
        initiator={"kind": "user", "identifier": "test"},
        candidate_artifact_id=artifact.id,
    )


@pytest.mark.asyncio
async def test_fresh_readiness_is_derived_collecting(db):
    status = await derive_readiness(db)
    assert status.state == "collecting"
    assert status.active_positive_subjects == 0
    assert status.revision == sha256(b"[]").hexdigest()
    assert status.to_dict()["thresholds"] == {
        "required": 50,
        "encouraged": 75,
        "strong_target": 100,
    }


@pytest.mark.asyncio
async def test_preference_event_is_bounded_and_idempotent(db):
    _job, _attempt, artifact = await _candidate_artifact(db)
    first = await _selection_event(db, artifact)
    second = await _selection_event(db, artifact)
    assert first.id == second.id
    with pytest.raises(TastePreferenceError, match="unexposed"):
        await append_preference_event(
            db,
            idempotency_key="bad-order",
            namespace="global",
            subject_kind="movie",
            subject_reference="movie-1",
            subject_snapshot={},
            action="selection",
            exposed_candidates=[{"candidate_id": "shown"}],
            presentation_order=["unseen"],
            training_context={},
            confidence="explicit",
            initiator={"kind": "user"},
        )


@pytest.mark.asyncio
async def test_positive_exemplar_requires_deployment(db):
    _job, _attempt, artifact = await _candidate_artifact(db)
    event = await _selection_event(db, artifact)
    with pytest.raises(TastePreferenceError, match="deployment"):
        await create_pending_exemplar(
            db,
            event=event,
            polarity="positive",
            evidence_source="explicit_selection",
            evidence_weight=1.0,
            deployment_job_id=None,
        )


@pytest.mark.asyncio
async def test_successful_deploy_pins_and_activates_exact_bytes(db):
    job, attempt, artifact = await _candidate_artifact(db)
    deployment, deployment_attempt = await _deployment_job(db, job)
    event = await _selection_event(db, artifact)
    exemplar = await create_pending_exemplar(
        db,
        event=event,
        polarity="positive",
        evidence_source="explicit_selection",
        evidence_weight=1.0,
        deployment_job_id=deployment.id,
    )
    await db.commit()
    pinned = await pin_candidate_artifact(
        artifact,
        deployment_job_id=deployment.id,
        deployment_attempt_id=deployment_attempt.id,
        deployment_fence_token=1,
    )
    assert pinned.checksum == artifact.checksum
    assert pinned.retention_class == "pinned"
    assert pinned.expires_at is None

    active = await activate_exemplar(
        db,
        exemplar_id=exemplar.id,
        retained_artifact_id=pinned.id,
        deployment_result={"outcome": "succeeded", "validated": True},
    )
    await db.commit()
    assert active.status == "active"
    status = await derive_readiness(db)
    assert status.active_positive_subjects == 1
    assert status.state == "collecting"


@pytest.mark.asyncio
async def test_failed_deployment_cannot_activate(db):
    job, _attempt, artifact = await _candidate_artifact(db)
    deployment, _deployment_attempt = await _deployment_job(db, job)
    event = await _selection_event(db, artifact)
    exemplar = await create_pending_exemplar(
        db,
        event=event,
        polarity="positive",
        evidence_source="explicit_selection",
        evidence_weight=1.0,
        deployment_job_id=deployment.id,
    )
    with pytest.raises(TastePreferenceError, match="validation"):
        await activate_exemplar(
            db,
            exemplar_id=exemplar.id,
            retained_artifact_id=artifact.id,
            deployment_result={"outcome": "failed", "validated": False},
        )


@pytest.mark.asyncio
async def test_active_positive_subject_and_content_are_unique(db):
    job, attempt, artifact = await _candidate_artifact(db)
    deployment, deployment_attempt = await _deployment_job(db, job)
    event = await _selection_event(db, artifact)
    exemplar = await create_pending_exemplar(
        db,
        event=event,
        polarity="positive",
        evidence_source="explicit_selection",
        evidence_weight=1.0,
        deployment_job_id=deployment.id,
    )
    await db.commit()
    pinned = await pin_candidate_artifact(
        artifact,
        deployment_job_id=deployment.id,
        deployment_attempt_id=deployment_attempt.id,
        deployment_fence_token=1,
    )
    await activate_exemplar(
        db,
        exemplar_id=exemplar.id,
        retained_artifact_id=pinned.id,
        deployment_result={"outcome": "succeeded", "validated": True},
    )
    duplicate_event = await append_preference_event(
        db,
        idempotency_key="duplicate-subject",
        namespace="global",
        subject_kind="movie",
        subject_reference="movie-1",
        subject_snapshot={"title": "Fixture"},
        action="selection",
        exposed_candidates=[{"candidate_id": "candidate-1"}],
        presentation_order=["candidate-1"],
        training_context={},
        confidence="explicit",
        initiator={"kind": "user"},
    )
    db.add(
        TasteExemplar(
            id=uuid4().hex,
            version=1,
            namespace="global",
            polarity="positive",
            evidence_weight=1.0,
            evidence_source="explicit_selection",
            subject_kind="movie",
            subject_reference="movie-1",
            subject_snapshot={"title": "Fixture"},
            retained_artifact_id=pinned.id,
            preference_event_id=duplicate_event.id,
            deployment_job_id=job.id,
            initiator={"kind": "user"},
            asset_key=pinned.storage_key,
            checksum=pinned.checksum,
            content_type="image/jpeg",
            status="active",
        )
    )
    with pytest.raises(IntegrityError):
        await db.commit()


@pytest.mark.asyncio
async def test_undo_revokes_without_rewriting_history(db):
    job, attempt, artifact = await _candidate_artifact(db)
    deployment, deployment_attempt = await _deployment_job(db, job)
    source = await _selection_event(db, artifact)
    exemplar = await create_pending_exemplar(
        db,
        event=source,
        polarity="positive",
        evidence_source="explicit_selection",
        evidence_weight=1.0,
        deployment_job_id=deployment.id,
    )
    await db.commit()
    pinned = await pin_candidate_artifact(
        artifact,
        deployment_job_id=deployment.id,
        deployment_attempt_id=deployment_attempt.id,
        deployment_fence_token=1,
    )
    await activate_exemplar(
        db,
        exemplar_id=exemplar.id,
        retained_artifact_id=pinned.id,
        deployment_result={"outcome": "succeeded", "validated": True},
    )
    undo = await append_preference_event(
        db,
        idempotency_key="undo:movie-1",
        namespace="global",
        subject_kind="movie",
        subject_reference="movie-1",
        subject_snapshot={"title": "Fixture"},
        action="undo",
        exposed_candidates=[],
        presentation_order=[],
        training_context={},
        confidence="explicit",
        initiator={"kind": "user"},
        supersedes_event_id=source.id,
    )
    await revoke_exemplar(db, exemplar_id=exemplar.id, event=undo, reason="user undo")
    await db.commit()
    assert source.revoked_event_id == undo.id
    assert (await derive_readiness(db)).active_positive_subjects == 0


@pytest.mark.asyncio
async def test_onboarding_analysis_retry_appends_immutable_successor_lineage(db):
    original, _attempt, _artifact = await _candidate_artifact(db)
    original.phase = "terminal"
    original.outcome = "cancelled"
    original.terminal_at = datetime.now(UTC)
    await record_onboarding_analysis_submission(
        db,
        job_id=original.id,
        subject_kind="movie",
        subject_reference="movie-1",
    )
    successor, _successor_attempt, _successor_artifact = await _candidate_artifact(
        db, name="retry-candidate.jpg"
    )
    successor.retry_of_job_id = original.id
    row = await record_onboarding_analysis_retry_successor(
        db,
        original_job_id=original.id,
        successor_job_id=successor.id,
    )
    await db.commit()
    assert row is not None
    assert row.job_id == successor.id
    assert row.predecessor_job_id == original.id
    assert await db.get(OnboardingAnalysisSuccessor, original.id) is not None


@pytest.mark.asyncio
async def test_deployment_retry_keeps_pending_exemplar_and_appends_successor(db):
    pipeline, _pipeline_attempt, artifact = await _candidate_artifact(db)
    deployment, _deployment_attempt = await _deployment_job(db, pipeline)
    event = await _selection_event(db, artifact)
    exemplar = await create_pending_exemplar(
        db,
        event=event,
        polarity="positive",
        evidence_source="explicit_selection",
        evidence_weight=1.0,
        deployment_job_id=deployment.id,
    )
    deployment.phase = "terminal"
    deployment.outcome = "failed"
    deployment.terminal_at = datetime.now(UTC)
    successor_job, _successor_attempt = await _deployment_job(db, pipeline)
    successor_job.retry_of_job_id = deployment.id
    successor = await record_deployment_retry_successor(
        db,
        original_job_id=deployment.id,
        successor_job_id=successor_job.id,
    )
    await db.commit()
    assert successor is not None
    assert successor.predecessor_job_id == deployment.id
    assert successor.ordinal == 1
    assert exemplar.status == "pending_deploy"
    assert exemplar.deployment_job_id == successor_job.id
    assert await db.get(TasteDeploymentSuccessor, deployment.id) is None


@pytest.mark.asyncio
async def test_public_retry_appends_deployment_successor_without_reopening_pending_exemplar(
    db, onboarding_client, monkeypatch
):
    pipeline, _pipeline_attempt, artifact = await _candidate_artifact(db)
    deployment, _deployment_attempt = await _deployment_job(db, pipeline)
    event = await _selection_event(db, artifact)
    exemplar = await create_pending_exemplar(
        db,
        event=event,
        polarity="positive",
        evidence_source="explicit_selection",
        evidence_weight=1.0,
        deployment_job_id=deployment.id,
    )
    deployment.phase = "terminal"
    deployment.outcome = "cancelled"
    deployment.terminal_at = datetime.now(UTC)
    await db.commit()

    monkeypatch.setattr(
        "marquee.core.jobs.control.configuration_provider.snapshot_for",
        lambda _keys: SimpleNamespace(version=1, values={}),
    )

    async def fake_enqueue(_session, **_kwargs):
        return 607001

    monkeypatch.setattr("marquee.core.jobs.control.pgqueuer_gateway.enqueue", fake_enqueue)

    response = await onboarding_client.post(
        f"/api/jobs/{deployment.id}/retry",
        json={"expected_fence_token": deployment.fence_token},
    )
    assert response.status_code == 200, response.text
    successor_id = response.json()["replacement_job_id"]

    await db.rollback()
    await db.refresh(deployment)
    await db.refresh(exemplar)
    assert deployment.phase == "terminal"
    assert deployment.outcome == "cancelled"
    assert exemplar.status == "pending_deploy"
    assert exemplar.deployment_job_id == successor_id
    successor = await db.scalar(
        select(TasteDeploymentSuccessor).where(TasteDeploymentSuccessor.job_id == successor_id)
    )
    assert successor is not None
    assert successor.predecessor_job_id == deployment.id
    assert successor.exemplar_id == exemplar.id


@pytest.mark.asyncio
async def test_validated_terminal_effect_recovers_without_repeating_deployment(db):
    pipeline, _pipeline_attempt, artifact = await _candidate_artifact(db)
    deployment, _deployment_attempt = await _deployment_job(db, pipeline)
    event = await _selection_event(db, artifact)
    exemplar = await create_pending_exemplar(
        db,
        event=event,
        polarity="positive",
        evidence_source="explicit_selection",
        evidence_weight=1.0,
        deployment_job_id=deployment.id,
    )
    result = {
        "outcome": "succeeded",
        "validation": {"verdict": "passed"},
        "target_outcomes": [
            {
                "expected": {"checksum": artifact.checksum},
                "actual": {"checksum": artifact.checksum},
            }
        ],
    }
    _recorded, validation = await record_deployment_effect(
        db, deployment_job_id=deployment.id, result=result
    )
    assert validation is not None
    deployment.phase = "terminal"
    deployment.outcome = "succeeded"
    deployment.terminal_at = datetime.now(UTC)
    deployment.result = result
    await db.commit()

    await reconcile_pending_onboarding_deployments(db)
    await db.commit()

    recovered = await db.get(TasteExemplar, exemplar.id)
    assert recovered is not None
    assert recovered.status == "active"
    assert recovered.retained_artifact_id is not None


@pytest.mark.asyncio
async def test_crash_after_validated_effect_recovers_from_failed_terminal_delivery(
    db, onboarding_client
):
    pipeline, _pipeline_attempt, artifact = await _candidate_artifact(db)
    deployment, _deployment_attempt = await _deployment_job(db, pipeline)
    event = await _selection_event(db, artifact)
    exemplar = await create_pending_exemplar(
        db,
        event=event,
        polarity="positive",
        evidence_source="explicit_selection",
        evidence_weight=1.0,
        deployment_job_id=deployment.id,
    )
    result = {
        "outcome": "succeeded",
        "reason_code": "copied",
        "validation": {"verdict": "passed"},
        "target_outcomes": [
            {
                "expected": {"checksum": artifact.checksum},
                "actual": {"checksum": artifact.checksum},
            }
        ],
    }
    _recorded, validation = await record_deployment_effect(
        db, deployment_job_id=deployment.id, result=result
    )
    assert validation is not None
    deployment.phase = "terminal"
    deployment.outcome = "failed"
    deployment.terminal_at = datetime.now(UTC)
    await db.commit()

    response = await onboarding_client.get("/api/onboarding/status")
    assert response.status_code == 200, response.text
    deployment_status = response.json()["lineage"]["deployment"]
    assert deployment_status[0]["job_id"] == deployment.id
    assert deployment_status[0]["state"] == "failed"
    assert deployment_status[0]["post_effect_validation"]["validated"] is True

    await db.rollback()
    await db.refresh(exemplar)
    assert exemplar.status == "active"
    assert exemplar.deployment_result is not None
    assert exemplar.deployment_result["outcome"] == "succeeded"
    assert exemplar.deployment_result["validated"] is True


@pytest.mark.asyncio
async def test_status_repair_refuses_stale_fence_after_validated_effect(db, onboarding_client):
    pipeline, _pipeline_attempt, artifact = await _candidate_artifact(db)
    deployment, _deployment_attempt = await _deployment_job(db, pipeline)
    event = await _selection_event(db, artifact)
    exemplar = await create_pending_exemplar(
        db,
        event=event,
        polarity="positive",
        evidence_source="explicit_selection",
        evidence_weight=1.0,
        deployment_job_id=deployment.id,
    )
    result = {
        "outcome": "succeeded",
        "reason_code": "copied",
        "validation": {"verdict": "passed"},
        "target_outcomes": [
            {
                "expected": {"checksum": artifact.checksum},
                "actual": {"checksum": artifact.checksum},
            }
        ],
    }
    _recorded, validation = await record_deployment_effect(
        db, deployment_job_id=deployment.id, result=result
    )
    assert validation is not None
    deployment.fence_token = 2
    deployment.phase = "terminal"
    deployment.outcome = "failed"
    deployment.terminal_at = datetime.now(UTC)
    await db.commit()

    response = await onboarding_client.get("/api/onboarding/status")
    assert response.status_code == 200, response.text

    await db.rollback()
    await db.refresh(exemplar)
    assert exemplar.status == "pending_deploy"
    assert exemplar.retained_artifact_id is None


@pytest.mark.asyncio
async def test_unvalidated_no_change_remains_pending_after_reconciliation(db):
    pipeline, _pipeline_attempt, artifact = await _candidate_artifact(db)
    deployment, _deployment_attempt = await _deployment_job(db, pipeline)
    event = await _selection_event(db, artifact)
    exemplar = await create_pending_exemplar(
        db,
        event=event,
        polarity="positive",
        evidence_source="explicit_selection",
        evidence_weight=1.0,
        deployment_job_id=deployment.id,
    )
    result = {
        "outcome": "no_change",
        "reason_code": "target_missing",
        "validation": {"verdict": "passed"},
        "target_outcomes": [
            {
                "expected": {"checksum": artifact.checksum},
                "actual": {"checksum": artifact.checksum},
            }
        ],
    }
    _recorded, validation = await record_deployment_effect(
        db, deployment_job_id=deployment.id, result=result
    )
    assert validation is None
    deployment.phase = "terminal"
    deployment.outcome = "no_change"
    deployment.terminal_at = datetime.now(UTC)
    deployment.result = result
    await db.commit()

    await reconcile_pending_onboarding_deployments(db)
    await db.commit()

    recovered = await db.get(TasteExemplar, exemplar.id)
    assert recovered is not None
    assert recovered.status == "pending_deploy"
    assert recovered.retained_artifact_id is None


@pytest.mark.asyncio
async def test_validated_no_change_activates_only_for_identical_selected_bytes(db):
    pipeline, _pipeline_attempt, artifact = await _candidate_artifact(db)
    deployment, _deployment_attempt = await _deployment_job(db, pipeline)
    event = await _selection_event(db, artifact)
    exemplar = await create_pending_exemplar(
        db,
        event=event,
        polarity="positive",
        evidence_source="explicit_selection",
        evidence_weight=1.0,
        deployment_job_id=deployment.id,
    )
    result = {
        "outcome": "no_change",
        "reason_code": "already_identical",
        "validation": {"verdict": "passed"},
        "target_outcomes": [
            {
                "expected": {"checksum": artifact.checksum},
                "actual": {"checksum": artifact.checksum},
            }
        ],
    }
    _recorded, validation = await record_deployment_effect(
        db, deployment_job_id=deployment.id, result=result
    )
    assert validation is not None
    deployment.phase = "terminal"
    deployment.outcome = "no_change"
    deployment.terminal_at = datetime.now(UTC)
    deployment.result = result
    await db.commit()

    await reconcile_pending_onboarding_deployments(db)
    await db.commit()

    recovered = await db.get(TasteExemplar, exemplar.id)
    assert recovered is not None
    assert recovered.status == "active"


def _basis(index: int) -> np.ndarray:
    vector = np.zeros(512, dtype=np.float32)
    vector[index] = 1.0
    return vector


def _save_profile(path: Path, *, negative: bool) -> None:
    positive = _basis(0).reshape(1, 512)
    payload: dict[str, np.ndarray] = {
        "embeddings": positive,
        "embedding_weights": np.asarray([0.4], dtype=np.float32),
        "poster_names": unicode_array(["liked.jpg"]),
        "asset_kinds": unicode_array(["movie"]),
        "centroid_emb": positive[0],
        "model_name": unicode_scalar(pipeline_settings.AI_MODEL),
    }
    if negative:
        payload.update(
            {
                "neg_embeddings": _basis(1).reshape(1, 512),
                "neg_embedding_weights": np.asarray([0.9], dtype=np.float32),
                "neg_poster_names": unicode_array(["hated.jpg"]),
            }
        )
    save_npz_atomic(path, payload)


def test_hate_changes_the_native_production_similarity_penalty(tmp_path: Path):
    without_hate = tmp_path / "without-hate.npz"
    with_hate = tmp_path / "with-hate.npz"
    _save_profile(without_hate, negative=False)
    _save_profile(with_hate, negative=True)

    baseline = NumpyTasteStore(without_hate)
    contrastive = NumpyTasteStore(with_hate)

    hated_candidate = _basis(1)
    unrelated_candidate = _basis(2)
    assert contrastive.negative_size == 1
    assert contrastive.style_score(hated_candidate) < baseline.style_score(hated_candidate)
    assert contrastive.style_score(unrelated_candidate) == baseline.style_score(unrelated_candidate)


def test_frozen_manifest_preserves_polarity_namespace_weight_and_lineage():
    exemplar = SimpleNamespace(
        id="negative-global",
        namespace="global",
        polarity="negative",
        evidence_weight=0.75,
        retained_artifact_id=42,
        checksum="a" * 64,
        embedding_identity={"model": "clip", "revision": "v1"},
        supersedes_exemplar_id="positive-old",
    )
    assert profile_exemplar_manifest(exemplar) == {
        "exemplar_id": "negative-global",
        "namespace": "global",
        "polarity": "negative",
        "weight": 0.75,
        "retained_artifact_id": 42,
        "checksum": "a" * 64,
        "embedding_identity": {"model": "clip", "revision": "v1"},
        "supersedes_exemplar_id": "positive-old",
    }
    rows = [
        SimpleNamespace(id="global-negative", namespace="global"),
        SimpleNamespace(id="movie-positive", namespace="movies"),
        SimpleNamespace(id="tv-negative", namespace="tv"),
    ]
    assert profile_input_ids(rows, "movies") == {"global-negative", "movie-positive"}

    original = SimpleNamespace(
        id="negative-global",
        namespace="global",
        polarity="negative",
        subject_kind="movie",
        subject_reference="movie-42",
        checksum="a" * 64,
        evidence_weight=0.75,
        retained_artifact_id=42,
        embedding_identity={"model": "clip", "revision": "v1"},
        supersedes_exemplar_id="positive-old",
        status="active",
    )
    replacement = SimpleNamespace(**{**original.__dict__, "retained_artifact_id": 43})
    assert evidence_revision([original]) != evidence_revision([replacement])


def test_runner_passes_negative_staging_and_frozen_weights_to_native_builder(
    monkeypatch, tmp_path: Path
):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "training").mkdir()
    (tmp_path / "negative").mkdir()
    captured: dict[str, object] = {}

    def fake_rebuild_profile(**kwargs):
        captured.update(kwargs)
        _save_profile(Path(kwargs["output"]), negative=True)
        return Path(kwargs["output"])

    monkeypatch.setattr("marquee.ml.taste_trainer.rebuild_profile", fake_rebuild_profile)
    result = _run_taste_profile(
        {
            "params": {
                "library": "movies",
                "source": {
                    "mode": "fixture",
                    "positive_weights": {"liked.jpg": 0.4},
                    "negative_weights": {"hated.jpg": 0.9},
                },
            }
        },
        SimpleNamespace(emit=lambda _frame: None),
    )

    assert captured["training_dir"] == Path("training")
    assert captured["negative_dir"] == Path("negative")
    assert captured["positive_weights"] == {"liked.jpg": 0.4}
    assert captured["negative_weights"] == {"hated.jpg": 0.9}
    assert result["summary"]["negatives"] == 1
