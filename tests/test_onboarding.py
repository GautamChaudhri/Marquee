"""Cold-start onboarding: neutral collection, recovery, and the public API contract.

Before any taste evidence exists the pipeline must collect without a taste authority — no
taste floor, no rescue, and an order that is permutation-invariant and blind to features and
prior scores. Covers that collecting mode, analysis restart and successor lineage, and the
named OpenAPI schemas the browser client is generated from."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from marquee.core.pipeline_config import PipelineSettings
from marquee.core.poster_sources.tmdb import PosterCandidate
from marquee.core.taste_preferences import record_onboarding_analysis_submission
from marquee.main import app
from marquee.models import Job, Movie, OnboardingAnalysisSuccessor
from marquee.pipeline.features import FeatureExtractor
from marquee.pipeline.gate import PosterGate
from marquee.pipeline.runner import _neutral_candidate_order
from marquee.pipeline.types import CandidateScore, FeatureVector


def _candidate(name: str, language: str | None) -> PosterCandidate:
    return PosterCandidate(
        file_path=f"/{name}",
        width=1000,
        height=1500,
        aspect_ratio=2 / 3,
        language=language,
        vote_average=5.0,
        vote_count=1,
    )


def _record(name: str) -> CandidateScore:
    return CandidateScore(image_path=Path(name), orig_filename=name)


def _features(*, knn_sim: float, aesthetic: float) -> FeatureVector:
    return FeatureVector(
        knn_sim=knn_sim,
        aesthetic=aesthetic,
        title_colorfulness=0.0,
        text_residual=0.0,
        resolution=1.5,
        sharpness=1.0,
        face_area=0.0,
        provenance=0.5,
        lang_match=1.0,
    )


def test_collecting_extractor_has_no_taste_authority() -> None:
    extractor = FeatureExtractor(personalization_mode="collecting")

    assert extractor.taste_store is None
    assert extractor.personalization_mode == "collecting"


def test_collecting_style_gate_omits_taste_floor_and_rescue() -> None:
    config = PipelineSettings(
        GATE_MIN_AESTHETIC=0.4,
        GATE_MIN_AESTHETIC_RESCUED=0.2,
        GATE_AESTHETIC_RESCUE_KNN=0.9,
        GATE_MIN_KNN_SIM=0.8,
    )
    gate = PosterGate(config)
    off_style = _features(knn_sim=0.0, aesthetic=0.6)
    taste_rescued = _features(knn_sim=1.0, aesthetic=0.3)

    assert gate.evaluate_style(off_style, personalization_mode="collecting").passed
    decision = gate.evaluate_style(taste_rescued, personalization_mode="collecting")
    assert not decision.passed
    assert decision.reason == "aesthetic_floor"


def test_neutral_order_is_permutation_invariant_and_source_interleaved() -> None:
    candidates = {
        "a.jpg": _candidate("a.jpg", "en"),
        "b.jpg": _candidate("b.jpg", "en"),
        "c.jpg": _candidate("c.jpg", "fr"),
        "d.jpg": _candidate("d.jpg", "fr"),
    }

    forward = _neutral_candidate_order(
        [_record(name) for name in candidates],
        movie_title="Arrival",
        candidate_map=candidates,
    )
    reverse = _neutral_candidate_order(
        [_record(name) for name in reversed(candidates)],
        movie_title="Arrival",
        candidate_map=candidates,
    )

    assert [item.orig_filename for item in forward] == [item.orig_filename for item in reverse]
    assert [candidates[item.orig_filename].language for item in forward] == ["en", "fr", "en", "fr"]
    assert all(item.final_score is None and not item.contributions for item in forward)


def test_neutral_order_ignores_features_and_prior_scores() -> None:
    candidates = {
        "one.jpg": _candidate("one.jpg", None),
        "two.jpg": _candidate("two.jpg", None),
    }
    first = [_record("one.jpg"), _record("two.jpg")]
    second = [_record("one.jpg"), _record("two.jpg")]
    first[0].features = _features(knn_sim=0.0, aesthetic=0.0)
    first[1].features = _features(knn_sim=1.0, aesthetic=1.0)
    second[0].features = _features(knn_sim=1.0, aesthetic=1.0)
    second[1].features = _features(knn_sim=0.0, aesthetic=0.0)
    second[0].final_score = 1.0

    order_a = _neutral_candidate_order(first, movie_title="Heat", candidate_map=candidates)
    order_b = _neutral_candidate_order(second, movie_title="Heat", candidate_map=candidates)

    assert [item.orig_filename for item in order_a] == [item.orig_filename for item in order_b]


@pytest_asyncio.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as value:
        yield value


def _analysis_job(movie: Movie, *, phase: str, outcome: str | None = None) -> Job:
    job_id = uuid4().hex
    return Job(
        id=job_id,
        root_id=job_id,
        type="poster_pipeline",
        payload_version=1,
        request={
            "movie_id": movie.id,
            "tmdb_id": movie.tmdb_id,
            "title": movie.title,
            "source_descriptors": [{"provider": "tmdb", "reference": f"movie:{movie.tmdb_id}"}],
        },
        phase=phase,
        outcome=outcome,
        desired_state="run",
        fence_token=0,
        priority=80,
        trigger_kind="manual",
        initiator={"kind": "user", "identifier": "onboarding-api"},
        feature_area="posters",
        presentation_family="posters",
        subject_kind="movie",
        subject_reference=str(movie.id),
        subject_snapshot={
            "version": 1,
            "kind": "movie",
            "display_id": f"movie:{movie.id}",
            "display_name": movie.title,
            "snapshot_at": datetime.now(UTC).isoformat(),
            "movie_id": movie.id,
            "title": movie.title,
            "year": movie.year,
            "tmdb_id": movie.tmdb_id,
            "media_kind": "movie",
        },
        terminal_at=datetime.now(UTC) if phase == "terminal" else None,
    )


async def _seed_terminal_analysis(db: AsyncSession, *, outcome: str) -> tuple[Movie, Job]:
    movie = Movie(
        title="Lineage Fixture",
        year=2026,
        folder_path="/library/lineage-fixture",
        movie_file_path="lineage-fixture.mkv",
        tmdb_id=907001,
    )
    db.add(movie)
    await db.flush()
    job = _analysis_job(movie, phase="terminal", outcome=outcome)
    db.add(job)
    await db.flush()
    await record_onboarding_analysis_submission(
        db,
        job_id=job.id,
        subject_kind="movie",
        subject_reference=str(movie.id),
    )
    await db.commit()
    return movie, job


@pytest.mark.asyncio
async def test_onboarding_start_reuses_compatible_nonterminal_analysis(
    db: AsyncSession,
    client: AsyncClient,
):
    movie = Movie(
        title="Reusable Analysis Fixture",
        year=2026,
        folder_path="/library/reusable-analysis-fixture",
        movie_file_path="reusable-analysis-fixture.mkv",
        tmdb_id=907000,
    )
    db.add(movie)
    await db.flush()
    original = _analysis_job(movie, phase="queued")
    db.add(original)
    await db.flush()
    await record_onboarding_analysis_submission(
        db,
        job_id=original.id,
        subject_kind="movie",
        subject_reference=str(movie.id),
    )
    await db.commit()

    response = await client.post("/api/onboarding/start", json={})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["analysis_job"]["job_id"] == original.id
    assert body["analysis_job"]["disposition"] == "reused"
    assert body["analysis_job"]["idempotent"] is True
    assert [item["job_id"] for item in body["status"]["lineage"]["analysis"]] == [original.id]


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["failed", "cancelled"])
async def test_onboarding_start_replaces_terminal_analysis_and_status_survives_fresh_session(
    db: AsyncSession,
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    outcome: str,
):
    """Start enters shared retry and status remains reconstructible without browser state."""
    movie, original = await _seed_terminal_analysis(db, outcome=outcome)

    monkeypatch.setattr(
        "marquee.core.jobs.control.configuration_provider.snapshot_for",
        lambda _keys: SimpleNamespace(version=1, values={}),
    )

    async def fake_enqueue(_session, **_kwargs):
        return 907001

    monkeypatch.setattr("marquee.core.jobs.control.pgqueuer_gateway.enqueue", fake_enqueue)

    response = await client.post("/api/onboarding/start", json={})
    assert response.status_code == 200, response.text
    body = response.json()
    successor_id = body["analysis_job"]["job_id"]
    assert body["analysis_job"]["disposition"] == "created"
    assert successor_id != original.id

    first_lineage = body["status"]["lineage"]["analysis"]
    assert [(item["job_id"], item["predecessor_job_id"]) for item in first_lineage] == [
        (original.id, None),
        (successor_id, original.id),
    ]
    assert first_lineage[0]["phase"] == "terminal"
    assert first_lineage[0]["outcome"] == outcome
    assert first_lineage[1]["phase"] == "queued"

    await db.rollback()
    persisted = list(
        await db.scalars(
            select(OnboardingAnalysisSuccessor)
            .where(OnboardingAnalysisSuccessor.subject_reference == str(movie.id))
            .order_by(OnboardingAnalysisSuccessor.created_at, OnboardingAnalysisSuccessor.job_id)
        )
    )
    assert [(row.job_id, row.predecessor_job_id) for row in persisted] == [
        (original.id, None),
        (successor_id, original.id),
    ]

    # A new request session is the API/worker restart boundary: the database is the only source.
    restarted = await client.get("/api/onboarding/status")
    assert restarted.status_code == 200, restarted.text
    assert restarted.json()["lineage"]["analysis"] == first_lineage


@pytest.mark.asyncio
async def test_public_retry_appends_onboarding_successor_once_and_never_reopens_terminal_job(
    db: AsyncSession,
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
):
    _movie, original = await _seed_terminal_analysis(db, outcome="cancelled")

    monkeypatch.setattr(
        "marquee.core.jobs.control.configuration_provider.snapshot_for",
        lambda _keys: SimpleNamespace(version=1, values={}),
    )

    async def fake_enqueue(_session, **_kwargs):
        return 907002

    monkeypatch.setattr("marquee.core.jobs.control.pgqueuer_gateway.enqueue", fake_enqueue)

    response = await client.post(
        f"/api/jobs/{original.id}/retry",
        json={"expected_fence_token": original.fence_token},
    )
    assert response.status_code == 200, response.text
    successor_id = response.json()["replacement_job_id"]

    await db.rollback()
    await db.refresh(original)
    assert original.phase == "terminal"
    assert original.outcome == "cancelled"
    assert original.fence_token == 1
    lineage = list(
        await db.scalars(
            select(OnboardingAnalysisSuccessor)
            .where(OnboardingAnalysisSuccessor.subject_reference == original.subject_reference)
            .order_by(OnboardingAnalysisSuccessor.created_at, OnboardingAnalysisSuccessor.job_id)
        )
    )
    assert [(row.job_id, row.predecessor_job_id) for row in lineage] == [
        (original.id, None),
        (successor_id, original.id),
    ]


def _success_schema(path: str, method: str) -> dict[str, object]:
    document = app.openapi()
    return document["paths"][path][method]["responses"]["200"]["content"]["application/json"][
        "schema"
    ]


def test_every_public_onboarding_success_response_is_a_named_openapi_schema() -> None:
    expected = {
        ("/api/onboarding/status", "get"): "OnboardingStatusResponse",
        ("/api/onboarding/start", "post"): "OnboardingStartResponse",
        ("/api/onboarding/runs/{run_id}/review", "get"): "OnboardingReviewResponse",
        ("/api/onboarding/choose", "post"): "OnboardingDecisionResponse",
        ("/api/onboarding/hate", "post"): "OnboardingDecisionResponse",
        ("/api/onboarding/complete", "post"): "OnboardingCompletionResponse",
    }

    for (path, method), model_name in expected.items():
        schema = _success_schema(path, method)
        assert schema == {"$ref": f"#/components/schemas/{model_name}"}


def test_onboarding_browser_client_uses_generated_contract_types() -> None:
    root = Path(__file__).resolve().parents[1]
    client = (root / "frontend/src/lib/api/onboarding.ts").read_text()
    legacy_types = (root / "frontend/src/lib/api/types.ts").read_text()

    assert "./generated/openapi" in client
    assert "interface StartResult" not in client
    assert "OnboardingDecisionIntent = {" not in client
    assert "OnboardingStatus" not in legacy_types
    assert "OnboardingReview" not in legacy_types
