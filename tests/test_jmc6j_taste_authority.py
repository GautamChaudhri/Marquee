"""JMC6J canonical preference, exemplar retention, and readiness contracts."""

from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
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
    revoke_exemplar,
)
from marquee.models import Job, JobArtifact, JobAttempt, TasteExemplar


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
        subject_snapshot={"version": 1, "kind": "movie", "title": "Fixture"},
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
    event = await _selection_event(db, artifact)
    exemplar = await create_pending_exemplar(
        db,
        event=event,
        polarity="positive",
        evidence_source="explicit_selection",
        evidence_weight=1.0,
        deployment_job_id=job.id,
    )
    await db.commit()
    pinned = await pin_candidate_artifact(
        artifact,
        deployment_job_id=job.id,
        deployment_attempt_id=attempt.id,
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
    event = await _selection_event(db, artifact)
    exemplar = await create_pending_exemplar(
        db,
        event=event,
        polarity="positive",
        evidence_source="explicit_selection",
        evidence_weight=1.0,
        deployment_job_id=job.id,
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
    event = await _selection_event(db, artifact)
    exemplar = await create_pending_exemplar(
        db,
        event=event,
        polarity="positive",
        evidence_source="explicit_selection",
        evidence_weight=1.0,
        deployment_job_id=job.id,
    )
    await db.commit()
    pinned = await pin_candidate_artifact(
        artifact,
        deployment_job_id=job.id,
        deployment_attempt_id=attempt.id,
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
    source = await _selection_event(db, artifact)
    exemplar = await create_pending_exemplar(
        db,
        event=source,
        polarity="positive",
        evidence_source="explicit_selection",
        evidence_weight=1.0,
        deployment_job_id=job.id,
    )
    await db.commit()
    pinned = await pin_candidate_artifact(
        artifact,
        deployment_job_id=job.id,
        deployment_attempt_id=attempt.id,
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
