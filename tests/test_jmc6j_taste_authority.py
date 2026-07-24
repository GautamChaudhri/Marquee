"""JMC6J canonical preference, exemplar retention, and readiness contracts."""

from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from marquee.config import settings
from marquee.core.taste_preferences import (
    TastePreferenceError,
    activate_exemplar,
    append_preference_event,
    create_pending_exemplar,
    derive_readiness,
    pin_candidate_artifact,
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
    data = b"canonical-jmc6j-poster-bytes"
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
