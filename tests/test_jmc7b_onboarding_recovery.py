"""JMC7B public onboarding successor and restart reconstruction contracts."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from marquee.core.taste_preferences import record_onboarding_analysis_submission
from marquee.main import app
from marquee.models import Job, Movie, OnboardingAnalysisSuccessor


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
            "source_descriptors": [
                {"provider": "tmdb", "reference": f"movie:{movie.tmdb_id}"}
            ],
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
