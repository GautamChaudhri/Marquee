"""Database-backed JMC6K taste-profile coordination regressions."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

import numpy as np
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from marquee.config import settings
from marquee.core.jobs.ml_publication import (
    acknowledge_consumption,
    resolve_loaded_taste_profile,
)
from marquee.core.taste_preferences import (
    derive_readiness,
    record_profile_build_terminal,
    schedule_initial_profile_build,
    schedule_profile_builds,
)
from marquee.main import app
from marquee.models import (
    Job,
    JobArtifact,
    JobAttempt,
    MlActivePublication,
    MlConsumerAcknowledgement,
    PosterPreferenceEvent,
    RuntimeInstance,
    TasteExemplar,
    TasteProfileBuild,
    TasteProfileCoordinator,
    TasteProfileRevision,
)


def _job(*, job_type: str = "poster_deploy") -> Job:
    job_id = uuid4().hex
    return Job(
        id=job_id,
        type=job_type,
        payload_version=1,
        request={},
        phase="running",
        desired_state="run",
        fence_token=1,
        root_id=job_id,
        trigger_kind="manual",
        feature_area="posters" if job_type == "poster_deploy" else "ml_taste",
        presentation_family="posters" if job_type == "poster_deploy" else "ml",
        subject_kind="movie",
        subject_reference="jmc6k",
        subject_snapshot={"version": 1, "kind": "movie", "title": "JMC6K fixture"},
    )


async def _seed_required_positive_subjects(db, *, count: int = 50) -> None:
    job = _job()
    db.add(job)
    await db.flush()
    artifacts = []
    for index in range(count):
        digest = sha256(f"jmc6k-profile-{index}".encode()).hexdigest()
        artifact = JobArtifact(
            job_id=job.id,
            kind="taste_exemplar",
            name=f"example-{index}.jpg",
            status="available",
            storage_key=f"jmc6k/profile/{index}.jpg",
            content_type="image/jpeg",
            size_bytes=1,
            checksum=digest,
            retention_class="pinned",
            artifact_metadata={"fixture": "jmc6k"},
        )
        db.add(artifact)
        artifacts.append((index, digest, artifact))
    await db.flush()
    for index, digest, artifact in artifacts:
        event = PosterPreferenceEvent(
            id=uuid4().hex,
            version=1,
            idempotency_key=f"jmc6k:positive:{index}",
            namespace="global",
            subject_kind="movie",
            subject_reference=f"movie-{index}",
            subject_snapshot={"id": index, "title": f"Movie {index}"},
            action="selection",
            exposed_candidates=[{"candidate_id": f"candidate-{index}"}],
            presentation_order=[f"candidate-{index}"],
            training_context={"fixture": "jmc6k"},
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
                deployment_job_id=job.id,
                initiator={"kind": "test"},
                asset_key=artifact.storage_key,
                checksum=digest,
                content_type="image/jpeg",
                status="active",
                activated_at=datetime.now(UTC),
            )
        )
    await db.commit()


async def _append_positive_subject(db, *, index: int) -> None:
    """Add one later global positive so the coordinator has a newer desired revision."""
    job = _job()
    db.add(job)
    await db.flush()
    digest = sha256(f"jmc6k-profile-{index}".encode()).hexdigest()
    artifact = JobArtifact(
        job_id=job.id,
        kind="taste_exemplar",
        name=f"example-{index}.jpg",
        status="available",
        storage_key=f"jmc6k/profile/{index}.jpg",
        content_type="image/jpeg",
        size_bytes=1,
        checksum=digest,
        retention_class="pinned",
        artifact_metadata={"fixture": "jmc6k"},
    )
    event = PosterPreferenceEvent(
        id=uuid4().hex,
        version=1,
        idempotency_key=f"jmc6k:positive:{index}",
        namespace="global",
        subject_kind="movie",
        subject_reference=f"movie-{index}",
        subject_snapshot={"id": index, "title": f"Movie {index}"},
        action="selection",
        exposed_candidates=[{"candidate_id": f"candidate-{index}"}],
        presentation_order=[f"candidate-{index}"],
        training_context={"fixture": "jmc6k"},
        confidence="explicit",
        initiator={"kind": "test"},
    )
    db.add_all([artifact, event])
    await db.flush()
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
            deployment_job_id=job.id,
            initiator={"kind": "test"},
            asset_key=artifact.storage_key,
            checksum=digest,
            content_type="image/jpeg",
            status="active",
            activated_at=datetime.now(UTC),
        )
    )
    await db.commit()


async def _activate_build(db, build: TasteProfileBuild, *, generation: int) -> None:
    """Install a loader-confirmed publication for one coordinator fixture build."""
    job = await db.get(Job, build.job_id)
    assert job is not None
    now = datetime.now(UTC)
    runtime = RuntimeInstance(
        id=str(uuid4()),
        role="worker",
        node_label="jmc6k-fixture",
        build="test",
        host_boot_id=str(uuid4()),
        process_id=1000 + generation,
        process_start_ticks=1,
        process_group_id=1000 + generation,
        advertised_entrypoints=["cpu"],
        capabilities={"entrypoints": ["cpu"]},
        readiness="ready",
        started_at=now,
        last_heartbeat_at=now,
        heartbeat_expires_at=now + timedelta(minutes=1),
    )
    db.add(runtime)
    await db.flush()
    attempt = JobAttempt(
        job_id=job.id,
        number=1,
        fence_token=1,
        runtime_instance_id=runtime.id,
        phase="running",
        started_at=now,
    )
    db.add(attempt)
    await db.flush()
    job.current_attempt_id = attempt.id
    storage_key = f"jmc6k/profiles/{job.id}-{build.library}-{generation}.npz"
    profile_path = Path(settings.DATA_DIR) / storage_key
    profile_path.parent.mkdir(parents=True, exist_ok=True)
    embeddings = np.zeros((1, 512), dtype=np.float32)
    embeddings[0, 0] = 1.0
    np.savez(
        profile_path,
        model_name=np.asarray("clip-vit-b-32"),
        embeddings=embeddings,
        centroid_emb=embeddings[0],
        poster_names=np.asarray(["fixture.jpg"]),
        asset_kinds=np.asarray(["movie"]),
    )
    payload = profile_path.read_bytes()
    checksum = sha256(payload).hexdigest()
    artifact = JobArtifact(
        job_id=job.id,
        attempt_id=attempt.id,
        kind="taste_profile",
        name=f"{build.library}-profile.npz",
        status="available",
        storage_key=storage_key,
        size_bytes=len(payload),
        checksum=checksum,
        artifact_metadata={
            "family": "taste_profile",
            "library": build.library,
            "revision": build.revision_digest,
        },
    )
    db.add(artifact)
    await db.flush()
    db.add(
        MlActivePublication(
            family=f"taste_profile:{build.library}",
            generation=generation,
            artifact_id=artifact.id,
            version=f"fixture-{generation}",
            checksum=checksum,
            job_id=job.id,
            attempt_id=attempt.id,
            fence_token=1,
        )
    )
    db.add(
        MlConsumerAcknowledgement(
            family=f"taste_profile:{build.library}",
            consumer_role="poster_pipeline",
            instance_id=runtime.id,
            generation=generation,
            checksum=checksum,
            artifact_id=artifact.id,
            load_result={"loader": "NumpyTasteStore", "exemplars": 1},
        )
    )
    build.state = "succeeded"
    build.result_generation = generation
    build.result_checksum = checksum
    build.consumer_reload_checksum = None
    build.completed_at = datetime.now(UTC)
    coordinator = await db.get(TasteProfileCoordinator, build.library)
    assert coordinator is not None
    coordinator.inflight_build_id = None
    coordinator.desired_generation = generation
    await db.commit()


@pytest.mark.asyncio
async def test_initial_profile_builds_are_one_per_library_with_explicit_generation(
    db, installed_pgqueuer
) -> None:
    await _seed_required_positive_subjects(db)

    submitted = await schedule_initial_profile_build(db, initiator_identifier="jmc6k-test")
    await db.commit()

    assert len(submitted) == 2
    builds = list(await db.scalars(select(TasteProfileBuild).order_by(TasteProfileBuild.library)))
    assert [build.library for build in builds] == ["movies", "tv"]
    assert {build.expected_generation for build in builds} == {0}
    assert {build.state for build in builds} == {"queued"}
    for build in builds:
        job = await db.get(Job, build.job_id)
        assert job is not None
        assert job.request["profile_build_id"] == build.id
        assert job.request["expected_generation"] == build.expected_generation
        assert job.request["revision"] == build.revision_digest

    assert await schedule_initial_profile_build(db, initiator_identifier="jmc6k-test") == ()
    readiness = (await derive_readiness(db)).to_dict()
    assert readiness["state"] == "building"
    assert readiness["libraries"]["movies"]["build"]["state"] == "queued"
    assert readiness["libraries"]["tv"]["build"]["state"] == "queued"


@pytest.mark.asyncio
async def test_failed_profile_build_stays_terminal_until_canonical_retry(db, installed_pgqueuer) -> None:
    await _seed_required_positive_subjects(db)
    submitted = await schedule_initial_profile_build(db, initiator_identifier="jmc6k-test")
    await db.commit()
    build = await db.scalar(
        select(TasteProfileBuild).where(TasteProfileBuild.job_id == submitted[0].job_id)
    )
    assert build is not None

    successor = await record_profile_build_terminal(
        db,
        build_id=build.id,
        job_id=build.job_id,
        state="failed",
        failure={"reason": "fixture_failure"},
    )
    await db.commit()

    assert successor is None
    failed = await db.get(TasteProfileBuild, build.id)
    assert failed is not None
    assert failed.state == "failed"
    failed_id = failed.id
    readiness = (await derive_readiness(db)).to_dict()
    assert readiness["libraries"][failed.library]["build"]["state"] == "failed"

    job = await db.get(Job, failed.job_id)
    assert job is not None
    job.phase = "terminal"
    job.outcome = "failed"
    job.terminal_at = datetime.now(UTC)
    expected_fence_token = job.fence_token
    await db.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            f"/api/jobs/{job.id}/retry",
            json={"expected_fence_token": expected_fence_token},
        )
    assert response.status_code == 200, response.text
    replacement_job_id = response.json()["replacement_job_id"]
    assert replacement_job_id
    await db.rollback()
    db.expire_all()
    successor = await db.scalar(
        select(TasteProfileBuild).where(TasteProfileBuild.job_id == replacement_job_id)
    )
    assert successor is not None
    assert successor.supersedes_build_id == failed_id
    assert successor.state == "queued"
    replacement = await db.get(Job, successor.job_id)
    assert replacement is not None
    assert replacement.request["profile_build_id"] == successor.id


@pytest.mark.asyncio
async def test_newer_evidence_coalesces_then_reconciles_one_successor(db, installed_pgqueuer) -> None:
    await _seed_required_positive_subjects(db)
    await schedule_initial_profile_build(db, initiator_identifier="jmc6k-test")
    await db.commit()
    first = await db.scalar(
        select(TasteProfileBuild).where(TasteProfileBuild.library == "movies")
    )
    assert first is not None

    await _append_positive_subject(db, index=50)
    assert (
        await schedule_profile_builds(
            db,
            namespaces=("movies",),
            initiator_identifier="jmc6k-test",
            force=True,
        )
        == ()
    )
    await db.commit()

    before = list(
        await db.scalars(
            select(TasteProfileBuild).where(TasteProfileBuild.library == "movies")
        )
    )
    assert len(before) == 1

    successor = await record_profile_build_terminal(
        db,
        build_id=first.id,
        job_id=first.job_id,
        state="succeeded",
        result_generation=1,
        result_checksum="a" * 64,
        consumer_reload_checksum="a" * 64,
    )
    await db.commit()

    assert successor is not None
    movies = list(
        await db.scalars(
            select(TasteProfileBuild)
            .where(TasteProfileBuild.library == "movies")
            .order_by(TasteProfileBuild.created_at, TasteProfileBuild.id)
        )
    )
    assert len(movies) == 2
    assert movies[0].revision_digest != movies[1].revision_digest
    assert movies[1].state == "queued"
    assert movies[1].expected_generation == 0
    assert (
        await schedule_profile_builds(
            db,
            namespaces=("movies",),
            initiator_identifier="jmc6k-test",
            force=True,
        )
        == ()
    )


@pytest.mark.asyncio
async def test_active_profiles_stay_personalized_when_a_later_update_fails(
    db, installed_pgqueuer
) -> None:
    await _seed_required_positive_subjects(db)
    await schedule_initial_profile_build(db, initiator_identifier="jmc6k-test")
    await db.commit()
    initial = list(await db.scalars(select(TasteProfileBuild)))
    assert {build.library for build in initial} == {"movies", "tv"}
    for build in initial:
        await _activate_build(db, build, generation=1)

    await _append_positive_subject(db, index=50)
    submissions = await schedule_profile_builds(
        db,
        namespaces=("movies",),
        initiator_identifier="jmc6k-test",
        force=True,
    )
    await db.commit()
    assert len(submissions) == 1
    update = await db.scalar(
        select(TasteProfileBuild).where(TasteProfileBuild.job_id == submissions[0].job_id)
    )
    assert update is not None
    assert update.expected_generation == 1

    await record_profile_build_terminal(
        db,
        build_id=update.id,
        job_id=update.job_id,
        state="failed",
        failure={"reason": "fixture_update_failure"},
    )
    await db.commit()

    readiness = (await derive_readiness(db)).to_dict()
    assert readiness["state"] == "personalized"
    assert readiness["personalized_scoring_available"] is True
    assert readiness["libraries"]["movies"]["active"]["generation"] == 1
    assert readiness["libraries"]["movies"]["build"]["state"] == "failed"
    assert readiness["libraries"]["movies"]["update_attention"] is True


@pytest.mark.asyncio
async def test_readiness_requires_a_live_current_consumer_acknowledgement(
    db, installed_pgqueuer
) -> None:
    await _seed_required_positive_subjects(db)
    await schedule_initial_profile_build(db, initiator_identifier="jmc6k-test")
    await db.commit()
    for build in list(await db.scalars(select(TasteProfileBuild))):
        await _activate_build(db, build, generation=1)

    assert (await derive_readiness(db)).state == "personalized"
    acknowledgement = await db.scalar(
        select(MlConsumerAcknowledgement).where(
            MlConsumerAcknowledgement.family == "taste_profile:movies"
        )
    )
    assert acknowledgement is not None
    previous_runtime = await db.get(RuntimeInstance, acknowledgement.instance_id)
    assert previous_runtime is not None
    previous_runtime.readiness = "stopped"
    previous_runtime.stopped_at = datetime.now(UTC)
    await db.commit()

    restarted = await derive_readiness(db)
    assert restarted.state != "personalized"
    assert restarted.libraries["movies"]["reload_state"]["ready"] is False

    now = datetime.now(UTC)
    replacement = RuntimeInstance(
        id=str(uuid4()),
        role="worker",
        node_label="jmc6k-replacement",
        build="test",
        host_boot_id=str(uuid4()),
        process_id=2001,
        process_start_ticks=1,
        process_group_id=2001,
        advertised_entrypoints=["cpu"],
        capabilities={"entrypoints": ["cpu"]},
        readiness="ready",
        started_at=now,
        last_heartbeat_at=now,
        heartbeat_expires_at=now + timedelta(minutes=1),
    )
    db.add(replacement)
    await db.flush()
    publication, load_result = await resolve_loaded_taste_profile(db, library="movies")
    await acknowledge_consumption(
        db,
        publication=publication,
        consumer_role="poster_pipeline",
        instance_id=replacement.id,
        load_result={**load_result, "supplied_to": "contained_poster_runner"},
    )
    await db.commit()

    assert (await derive_readiness(db)).state == "personalized"
    publication.path.write_bytes(b"corrupt-profile-bytes")
    corrupt = await derive_readiness(db)
    assert corrupt.state != "personalized"
    assert "bytes are invalid" in corrupt.libraries["movies"]["reload_state"]["reason"]


@pytest.mark.asyncio
async def test_database_partial_unique_index_rejects_two_inflight_library_builds(db) -> None:
    revision = TasteProfileRevision(
        digest="a" * 64,
        exemplar_ids=[],
        exemplar_checksums=[],
        positive_subjects=50,
        state="eligible",
        consumer_reload={},
    )
    first_job = _job(job_type="taste_rebuild")
    second_job = _job(job_type="taste_rebuild")
    db.add_all([revision, first_job, second_job])
    await db.flush()
    db.add(
        TasteProfileBuild(
            id=uuid4().hex,
            revision_digest=revision.digest,
            library="movies",
            expected_generation=0,
            job_id=first_job.id,
            state="queued",
        )
    )
    await db.flush()
    db.add(
        TasteProfileBuild(
            id=uuid4().hex,
            revision_digest=revision.digest,
            library="movies",
            expected_generation=1,
            job_id=second_job.id,
            state="running",
        )
    )
    with pytest.raises(IntegrityError):
        await db.commit()
