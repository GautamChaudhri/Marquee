"""Intentional-red JMC6K production-contract regressions.

These tests freeze the independent audit findings before the corrective implementation.
They are expected to fail at the compact JMC6J product surface and become the K1--K5
producer-to-consumer regressions.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

import pytest
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError
from sqlalchemy import select

from marquee.config import settings
from marquee.core.jobs.documents import TasteRebuildRequestV1
from marquee.core.onboarding_review import (
    OnboardingReviewError,
    bind_onboarding_decision,
    load_onboarding_review,
)
from marquee.core.taste_preferences import derive_readiness
from marquee.main import app
from marquee.models import (
    Job,
    JobArtifact,
    JobAttempt,
    MlActivePublication,
    Movie,
    PipelineRun,
    PosterPreferenceEvent,
    TasteExemplar,
)


def _active_job() -> Job:
    return Job(
        id="jmc6k-active-onboarding-job",
        type="poster_pipeline",
        payload_version=1,
        request={},
        phase="running",
        desired_state="run",
        fence_token=1,
        root_id="jmc6k-active-onboarding-job",
        trigger_kind="manual",
        feature_area="posters",
        presentation_family="posters",
        subject_kind="movie",
        subject_reference="1",
        subject_snapshot={"version": 1, "kind": "movie", "title": "JMC6K Fixture"},
    )


async def _canonical_review_run(db) -> PipelineRun:
    movie = Movie(
        title="JMC6K Fixture",
        year=2026,
        folder_path="/jmc6k/fixture",
        movie_file_path="fixture.mkv",
        tmdb_id=6001,
    )
    db.add(movie)
    await db.flush()
    job = _active_job()
    job.id = "a" * 32
    job.root_id = job.id
    db.add(job)
    await db.flush()
    attempt = JobAttempt(
        job_id=job.id,
        number=1,
        fence_token=1,
        phase="finished",
        outcome="succeeded",
        started_at=datetime.now(UTC),
        finished_at=datetime.now(UTC),
    )
    db.add(attempt)
    await db.flush()
    job.current_attempt_id = attempt.id

    candidate_bytes = b"jmc6k-review-candidate"
    candidate_key = f"test-artifacts/{job.id}/candidate.jpg"
    candidate_path = Path(settings.DATA_DIR) / candidate_key
    candidate_path.parent.mkdir(parents=True, exist_ok=True)
    candidate_path.write_bytes(candidate_bytes)
    candidate = JobArtifact(
        job_id=job.id,
        attempt_id=attempt.id,
        kind="evidence_image",
        name="candidate.jpg",
        status="available",
        storage_key=candidate_key,
        content_type="image/jpeg",
        size_bytes=len(candidate_bytes),
        checksum=sha256(candidate_bytes).hexdigest(),
        artifact_metadata={
            "family": "poster_pipeline",
            "role": "review_candidate",
            "candidate_reference": "candidate.jpg",
        },
    )
    db.add(candidate)
    await db.flush()

    survivor = {
        "candidate_id": sha256(
            b"jmc7b-review-v1\x00jmc6k-review-run\x00candidate.jpg"
        ).hexdigest(),
        "reference": "candidate.jpg",
        "position": 0,
        "objective_eligible": True,
        "artifact_id": candidate.id,
        "artifact_storage_key": candidate.storage_key,
        "artifact_checksum": candidate.checksum,
    }
    review = {
        "version": 1,
        "order_algorithm": "source_family_round_robin_sha256_v1",
        "survivors": [survivor],
        "eligible_count": 1,
        "archived_count": 1,
        "truncated_count": 0,
    }
    review["checksum"] = sha256(
        json.dumps(
            {
                "version": review["version"],
                "order_algorithm": review["order_algorithm"],
                "survivors": review["survivors"],
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
    ).hexdigest()
    archive = {
        "diagnostic_ledger": {
            "version": 1,
            "candidates": [
                {
                    "orig_filename": "candidate.jpg",
                    "gate_decision": "passed",
                    "rejection_reason": None,
                    "raw_features": {"knn_sim": 0.5},
                    "width": 1000,
                    "height": 1500,
                    "language": "en",
                }
            ],
        },
        "review": review,
    }
    archive_bytes = json.dumps(archive, separators=(",", ":"), sort_keys=True).encode()
    archive_key = f"test-artifacts/{job.id}/run.json"
    archive_path = Path(settings.DATA_DIR) / archive_key
    archive_path.write_bytes(archive_bytes)
    archive_artifact = JobArtifact(
        job_id=job.id,
        attempt_id=attempt.id,
        kind="command_report",
        name="run.json",
        status="available",
        storage_key=archive_key,
        content_type="application/json",
        size_bytes=len(archive_bytes),
        checksum=sha256(archive_bytes).hexdigest(),
        artifact_metadata={"family": "poster_pipeline"},
    )
    db.add(archive_artifact)
    await db.flush()
    run = PipelineRun(
        run_id="jmc6k-review-run",
        movie_id=movie.id,
        media_type="movie",
        subject_snapshot={"kind": "movie", "title": movie.title, "year": movie.year},
        status="completed",
        job_id=job.id,
        attempt_id=attempt.id,
        fence_token=1,
        archive_artifact_id=archive_artifact.id,
    )
    db.add(run)
    await db.commit()
    return run


@pytest.mark.asyncio
async def test_choose_accepts_intent_only_not_client_exposure() -> None:
    """The public command must reject only canonical evidence, never a missing client order."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/onboarding/choose",
            json={
                "run_id": "missing-run",
                "candidate_id": "a" * 32,
                "review_revision": "a" * 64,
                "idempotency_key": "jmc6k-intent-only",
            },
        )
    assert response.status_code in {400, 404, 409}


@pytest.mark.asyncio
async def test_review_is_neutral_and_hate_uses_only_canonical_archive_evidence(db) -> None:
    run = await _canonical_review_run(db)
    review = await load_onboarding_review(db, run.run_id)

    assert review["review_revision"]
    assert len(review["candidates"]) == 1
    assert "rank" not in review["candidates"][0]
    assert "score" not in review["candidates"][0]

    result = await bind_onboarding_decision(
        db,
        run_id=run.run_id,
        candidate_id=review["candidates"][0]["candidate_id"],
        review_revision=review["review_revision"],
        idempotency_key="jmc6k-hate",
        decision="hate",
    )
    await db.commit()

    exemplar = await db.get(TasteExemplar, result["exemplar_id"])
    assert exemplar is not None
    assert exemplar.status == "active"
    assert exemplar.polarity == "negative"
    assert exemplar.deployment_job_id is None
    assert exemplar.deployment_result is None
    assert result["deployment_job_id"] is None

    replay = await bind_onboarding_decision(
        db,
        run_id=run.run_id,
        candidate_id=review["candidates"][0]["candidate_id"],
        review_revision=review["review_revision"],
        idempotency_key="jmc6k-hate",
        decision="hate",
    )
    assert replay["event_id"] == result["event_id"]
    assert replay["disposition"] == "reused"


@pytest.mark.asyncio
async def test_review_rejects_a_survivor_that_fails_an_objective_gate(db) -> None:
    run = await _canonical_review_run(db)
    archive_artifact = await db.get(JobArtifact, run.archive_artifact_id)
    assert archive_artifact is not None
    archive_path = Path(settings.DATA_DIR) / str(archive_artifact.storage_key)
    archive = json.loads(archive_path.read_text())
    diagnostic = archive["diagnostic_ledger"]["candidates"][0]
    diagnostic["gate_decision"] = "gated"
    diagnostic["rejection_reason"] = "ocr_residual_text"
    archive_bytes = json.dumps(archive, separators=(",", ":"), sort_keys=True).encode()
    archive_path.write_bytes(archive_bytes)
    archive_artifact.size_bytes = len(archive_bytes)
    archive_artifact.checksum = sha256(archive_bytes).hexdigest()
    await db.commit()

    with pytest.raises(OnboardingReviewError, match="survivor membership"):
        await load_onboarding_review(db, run.run_id)


@pytest.mark.asyncio
async def test_decision_rejects_a_survivor_artifact_from_the_wrong_attempt(db) -> None:
    run = await _canonical_review_run(db)
    review = await load_onboarding_review(db, run.run_id)
    archive_artifact = await db.get(JobArtifact, run.archive_artifact_id)
    assert archive_artifact is not None
    archive = json.loads((Path(settings.DATA_DIR) / str(archive_artifact.storage_key)).read_text())
    artifact_id = archive["review"]["survivors"][0]["artifact_id"]
    candidate_artifact = await db.get(JobArtifact, artifact_id)
    assert candidate_artifact is not None
    candidate_artifact.attempt_id = None
    await db.commit()

    with pytest.raises(OnboardingReviewError, match="artifact is invalid"):
        await bind_onboarding_decision(
            db,
            run_id=run.run_id,
            candidate_id=review["candidates"][0]["candidate_id"],
            review_revision=review["review_revision"],
            idempotency_key="jmc7b-wrong-attempt",
            decision="hate",
        )
    assert run.feedback_event_id is None
    assert await db.scalar(select(PosterPreferenceEvent)) is None


@pytest.mark.asyncio
async def test_stale_review_cannot_create_a_partial_decision(db) -> None:
    run = await _canonical_review_run(db)
    review = await load_onboarding_review(db, run.run_id)

    with pytest.raises(OnboardingReviewError, match="review has changed"):
        await bind_onboarding_decision(
            db,
            run_id=run.run_id,
            candidate_id=review["candidates"][0]["candidate_id"],
            review_revision="0" * 64,
            idempotency_key="jmc7b-stale-review",
            decision="hate",
        )
    assert run.feedback_event_id is None
    assert await db.scalar(select(PosterPreferenceEvent)) is None


@pytest.mark.asyncio
async def test_review_fails_closed_when_survivor_bytes_are_corrupt(db) -> None:
    run = await _canonical_review_run(db)
    archive_artifact = await db.get(JobArtifact, run.archive_artifact_id)
    assert archive_artifact is not None
    archive = json.loads((Path(settings.DATA_DIR) / str(archive_artifact.storage_key)).read_text())
    artifact_id = archive["review"]["survivors"][0]["artifact_id"]
    candidate_artifact = await db.get(JobArtifact, artifact_id)
    assert candidate_artifact is not None
    (Path(settings.DATA_DIR) / str(candidate_artifact.storage_key)).write_bytes(b"corrupt")

    with pytest.raises(OnboardingReviewError, match="bytes are unavailable"):
        await load_onboarding_review(db, run.run_id)


@pytest.mark.asyncio
async def test_legacy_candidate_list_is_not_inferred_as_an_onboarding_review(db) -> None:
    run = await _canonical_review_run(db)
    archive_artifact = await db.get(JobArtifact, run.archive_artifact_id)
    assert archive_artifact is not None
    archive_path = Path(settings.DATA_DIR) / str(archive_artifact.storage_key)
    legacy = {"candidates": [{"orig_filename": "candidate.jpg"}]}
    archive_bytes = json.dumps(legacy, separators=(",", ":"), sort_keys=True).encode()
    archive_path.write_bytes(archive_bytes)
    archive_artifact.size_bytes = len(archive_bytes)
    archive_artifact.checksum = sha256(archive_bytes).hexdigest()
    await db.commit()

    with pytest.raises(OnboardingReviewError, match="explicit review survivors"):
        await load_onboarding_review(db, run.run_id)


@pytest.mark.asyncio
async def test_diagnostic_rejection_can_never_be_served_as_a_review_candidate(db) -> None:
    run = await _canonical_review_run(db)
    archive_artifact = await db.get(JobArtifact, run.archive_artifact_id)
    assert archive_artifact is not None
    archive_path = Path(settings.DATA_DIR) / str(archive_artifact.storage_key)
    archive = json.loads(archive_path.read_text())
    archive["diagnostic_ledger"]["candidates"].append(
        {
            "orig_filename": "rejected.jpg",
            "gate_decision": "gated",
            "rejection_reason": "ocr_residual_text",
            "raw_features": {"knn_sim": 0.2},
        }
    )
    archive_bytes = json.dumps(archive, separators=(",", ":"), sort_keys=True).encode()
    archive_path.write_bytes(archive_bytes)
    archive_artifact.size_bytes = len(archive_bytes)
    archive_artifact.checksum = sha256(archive_bytes).hexdigest()
    await db.commit()

    from marquee.api.routes.pipeline import get_run_poster

    with pytest.raises(HTTPException, match="No reviewable candidate") as exc_info:
        await get_run_poster(run.run_id, "rejected.jpg", db)
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_onboarding_status_links_to_projection_room(db) -> None:
    """A recoverable onboarding snapshot must link to the canonical Activity destination."""
    db.add(_active_job())
    await db.commit()

    from marquee.api.routes.onboarding import onboarding_status

    result = await onboarding_status(db)
    assert result["active_jobs"]
    assert result["active_jobs"][0]["activity_link"].startswith("/projection-room")


@pytest.mark.asyncio
async def test_onboarding_status_recovers_the_latest_actionable_review(db) -> None:
    """A completed onboarding analysis returns to candidate review after refresh/navigation."""
    run = await _canonical_review_run(db)
    job = await db.get(Job, run.job_id)
    assert job is not None
    job.initiator = {"kind": "user", "identifier": "onboarding-api"}
    await db.commit()

    from marquee.api.routes.onboarding import onboarding_status

    status = await onboarding_status(db)

    assert status["review"] == {
        "run_id": run.run_id,
        "analysis_job_id": run.job_id,
        "url": f"/onboarding?review={run.run_id}",
    }


def test_rebuild_request_requires_an_explicit_expected_generation() -> None:
    """A CAS request may use generation zero, but it cannot silently default to it."""
    with pytest.raises(ValidationError):
        TasteRebuildRequestV1(source="canonical_revision", revision="a" * 64)


def test_per_namespace_profile_build_lineage_is_a_public_model() -> None:
    """Movie and TV lineage must be independently representable in the durable model."""
    from marquee.models import TasteProfileBuild  # noqa: PLC0415

    assert TasteProfileBuild.__tablename__ == "taste_profile_builds"


@pytest.mark.asyncio
async def test_readiness_exposes_active_desired_and_build_state_per_library(db) -> None:
    """Readiness must not collapse evidence, publications, and coordinator state into one digest."""
    readiness = (await derive_readiness(db)).to_dict()
    assert set(readiness["libraries"]) == {"movies", "tv"}
    for library in readiness["libraries"].values():
        assert {"active", "desired_revision", "build", "reload_state"} <= set(library)


@pytest.mark.asyncio
async def test_readiness_marks_a_profile_mismatched_residual_dormant(db) -> None:
    """Changed profile identity retires use, not the immutable residual evidence."""
    job = _active_job()
    job.id = "d" * 32
    job.root_id = job.id
    db.add(job)
    await db.flush()
    attempt = JobAttempt(
        job_id=job.id,
        number=1,
        fence_token=1,
        phase="finished",
        outcome="succeeded",
        started_at=datetime.now(UTC),
        finished_at=datetime.now(UTC),
    )
    db.add(attempt)
    await db.flush()
    job.current_attempt_id = attempt.id
    profile = JobArtifact(
        job_id=job.id,
        attempt_id=attempt.id,
        kind="taste_profile",
        name="movies-profile.npz",
        status="available",
        virtual_source={"fixture": "jmc6k"},
        checksum="a" * 64,
        artifact_metadata={"revision": "b" * 64},
    )
    residual = JobArtifact(
        job_id=job.id,
        attempt_id=attempt.id,
        kind="ranking_residual",
        name="movies-residual.npz",
        status="available",
        virtual_source={"fixture": "jmc6k"},
        checksum="c" * 64,
        artifact_metadata={"profile_checksum": "d" * 64, "profile_generation": 1},
    )
    db.add_all((profile, residual))
    await db.flush()
    db.add_all(
        (
            MlActivePublication(
                family="taste_profile:movies",
                generation=2,
                artifact_id=profile.id,
                version="fixture-profile-2",
                checksum=profile.checksum,
                job_id=job.id,
                attempt_id=attempt.id,
                fence_token=1,
            ),
            MlActivePublication(
                family="ranking_residual:movies",
                generation=1,
                artifact_id=residual.id,
                version="fixture-residual-1",
                checksum=residual.checksum,
                job_id=job.id,
                attempt_id=attempt.id,
                fence_token=1,
            ),
        )
    )
    await db.commit()

    readiness = (await derive_readiness(db)).to_dict()

    assert readiness["residual_dormant"] is True
    residual_state = readiness["libraries"]["movies"]["residual"]
    assert {"active": True, "compatible": False, "dormant": True}.items() <= residual_state.items()
    assert residual_state["reason"] == "active ML publication evidence is invalid: ranking_residual:movies"


def test_residual_runtime_context_requires_external_profile_identity() -> None:
    """Residual compatibility must receive runtime profile identity instead of self-validating."""
    from marquee.pipeline.scorer import ResidualRuntimeContext  # noqa: PLC0415

    context = ResidualRuntimeContext(
        library="movies",
        baseline_signature="baseline",
        profile_checksum="a" * 64,
        profile_generation=1,
    )
    assert context.profile_checksum == "a" * 64


def test_residual_runtime_rejects_a_profile_change_without_deleting_the_artifact() -> None:
    """A published residual becomes dormant when the active profile identity changes."""
    import numpy as np  # noqa: PLC0415

    from marquee.ml.residual import (  # noqa: PLC0415
        ResidualArtifact,
        ResidualEvaluation,
        baseline_signature,
    )
    from marquee.pipeline.scorer import (  # noqa: PLC0415
        ResidualCompatibilityError,
        ResidualRuntimeContext,
        ResidualScorer,
        WeightedScorer,
    )

    baseline = WeightedScorer()
    artifact = ResidualArtifact(
        namespace="movies",
        feature_names=["aesthetic"],
        weights=np.asarray([0.25]),
        bias=0.0,
        alpha=0.5,
        delta_max=0.75,
        baseline_signature=baseline_signature(baseline.config.scorer_weights),
        profile_checksum="a" * 64,
        evidence_revision="b" * 64,
        seed=7,
        evaluation=ResidualEvaluation(0.5, 0.6, 0.1, 5, 20, 0.05, 0.1),
        trained_at="2026-07-22T00:00:00+00:00",
        profile_generation=1,
    )
    with pytest.raises(ResidualCompatibilityError, match="taste profile mismatch"):
        ResidualScorer(
            baseline,
            artifact,
            ResidualRuntimeContext(
                library="movies",
                baseline_signature=artifact.baseline_signature,
                profile_checksum="c" * 64,
                profile_generation=2,
            ),
        )
    assert artifact.profile_checksum == "a" * 64


def test_residual_evaluation_uses_the_runtime_candidate_scoring_primitive() -> None:
    """Activation must call the same logit-space, per-candidate transform as runtime."""
    from marquee.ml.residual import score_residual_candidate  # noqa: PLC0415

    score = score_residual_candidate(
        baseline_probability=0.5,
        normalized_features={"aesthetic": 0.5},
        weights={"aesthetic": 0.0},
        bias=0.0,
        alpha=0.5,
        delta_max=0.75,
    )
    assert score.final_score == pytest.approx(0.5)


def test_residual_scoring_clamps_each_candidate_before_pair_comparison() -> None:
    """Pair evaluation must compare final logits, not clamp a feature difference."""
    from marquee.ml.residual import score_residual_candidate  # noqa: PLC0415

    winner = score_residual_candidate(
        baseline_probability=0.5,
        normalized_features={"aesthetic": 10.0},
        weights={"aesthetic": 1.0},
        bias=0.0,
        alpha=0.5,
        delta_max=0.75,
    )
    loser = score_residual_candidate(
        baseline_probability=0.5,
        normalized_features={"aesthetic": -10.0},
        weights={"aesthetic": 1.0},
        bias=0.0,
        alpha=0.5,
        delta_max=0.75,
    )
    assert (winner.delta, loser.delta) == pytest.approx((0.75, -0.75))
    assert winner.final_logit - loser.final_logit == pytest.approx(0.75)


def test_residual_training_report_separates_all_subject_partitions() -> None:
    """Validation and untouched test metrics must remain distinguishable from training."""
    from marquee.ml.residual import ResidualPair, train_residual

    pairs = [
        ResidualPair(
            subject=f"movie:{subject}",
            winner={"aesthetic": 1.0},
            loser={"aesthetic": 0.0},
            baseline_margin=-0.2,
            weight=1.0,
            confidence="strong",
        )
        for subject in range(30)
        for _ in range(10)
    ]
    _artifact, report = train_residual(
        pairs,
        namespace="movies",
        baseline="baseline-v1",
        profile_checksum="a" * 64,
        evidence_revision="b" * 64,
        seed=7,
    )
    assert set(report["partitions"]) == {"train", "validation", "test"}


def test_current_closure_names_residual_authority_not_retired_head_language() -> None:
    """Current product contracts cannot revive the retired learned-head authority by description."""
    root = Path(__file__).resolve().parents[1]
    manifest = json.loads(
        (root / "tests/fixtures/jmc6h/enabled_definition_closure.json").read_text(encoding="utf-8")
    )
    residual = next(
        item for item in manifest["definitions"] if item["job_type"] == "ranking_residual_train"
    )

    assert residual["artifacts"] == "native loadable residual.npz"
    assert residual["producer"] == "POST /api/taste/residual/retrain (routes.taste)"
    assert residual["certification_test"].endswith(
        "test_ranking_residual_publishes_native_loadable_artifact"
    )

    active_sources = (
        "marquee/onboarding/__init__.py",
        "marquee/config.py",
        "marquee/pipeline/retro_features.py",
        "marquee/api/routes/feedback.py",
        "marquee/core/jobs/handlers_ml.py",
        "frontend/src/lib/api/onboarding.ts",
        "frontend/src/lib/components/pipeline/RunResultsView.svelte",
    )
    current_text = "\n".join((root / source).read_text(encoding="utf-8") for source in active_sources)
    assert "learned head" not in current_text.lower()
    assert "learned-head" not in current_text.lower()
    assert "rank test" not in current_text.lower()


def test_browser_onboarding_lifecycle_is_covered() -> None:
    """The certified browser suite needs a real onboarding journey, not Activity-only smoke."""
    from pathlib import Path

    assert Path("frontend/e2e/onboarding.spec.ts").is_file()
