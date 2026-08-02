"""Feedback: approve, override, reject, undo, and review-queue disposition.

Every decision is an immutable event that both submits the poster mutation and becomes
ranking evidence. A review-queue reset changes disposition only — it must never null the
database poster state while the deployed file stays on disk."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from marquee.api.routes import feedback as feedback_route
from marquee.api.routes.pipeline import reset_review_queue
from marquee.config import settings
from marquee.core.pipeline_config import pipeline_settings
from marquee.main import app
from marquee.ml.namespaces import get_namespace
from marquee.models import (
    Job,
    JobArtifact,
    Movie,
    PipelineRun,
    PosterPreferenceEvent,
    Season,
    Series,
    TasteExemplar,
)
from marquee.models.job import JobAttempt
from tests.support.canonical_poster import seed_canonical_pipeline_run

_REAL_SCHEDULE_RESIDUAL_SUCCESSOR = feedback_route._schedule_residual_successor


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture(autouse=True)
def labels_to_tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline_settings, "FEEDBACK_DEPLOY_DEFAULT", False)

    # Avoid scheduling ML work in endpoint behavior tests.
    async def no_residual_successor(*_args, **_kwargs):
        return {"scheduled": False, "reason": "stub", "job": None}

    monkeypatch.setattr(feedback_route, "_schedule_residual_successor", no_residual_successor)
    yield


@pytest.mark.asyncio
async def test_feedback_successor_preserves_exact_revision_lineage(
    db, installed_pgqueuer, monkeypatch
):
    monkeypatch.setattr(
        feedback_route,
        "build_residual_pairs",
        lambda _events: [SimpleNamespace(subject=f"movie:{index // 8}") for index in range(200)],
    )
    result = await _REAL_SCHEDULE_RESIDUAL_SUCCESSOR(
        db,
        get_namespace("movies"),
    )

    assert result["scheduled"] is True
    job = await db.get(Job, result["job"]["job_id"])
    assert job is not None
    assert job.type == "ranking_residual_train"
    assert job.subject_reference == "ranking_residual:movies"
    assert job.request["evidence_revision"] == sha256(b"[]").hexdigest()
    assert job.request["mutation"] == "manual"


def _archive(movie_id: int) -> dict:
    return {
        "movie_id": movie_id,
        "title": "Die Hard",
        "tmdb_id": 562,
        "candidates": [
            {
                "orig_filename": "auto.jpg",
                "image_path": "/x/ranked/1__0.9__auto.jpg",
                "rank": 1,
                "final_score": 0.9,
                "normalized_features": {"knn_sim": 0.9},
                "raw_features": {"knn_sim": 0.8},
                "extended_features": {},
                "stage_reached": "ranked",
                "rejection_reason": None,
            },
            {
                "orig_filename": "alt.jpg",
                "image_path": "/x/ranked/4__0.6__alt.jpg",
                "rank": 4,
                "final_score": 0.6,
                "normalized_features": {"knn_sim": 0.6},
                "raw_features": {"knn_sim": 0.5},
                "extended_features": {},
                "stage_reached": "ranked",
                "rejection_reason": None,
            },
            {
                "orig_filename": "ocrreject.jpg",
                "image_path": "/x/2-ocr-rejected/ocr_text_heavy__ocrreject.jpg",
                "rank": None,
                "normalized_features": {"knn_sim": 0.7},
                "raw_features": {"knn_sim": 0.6},
                "extended_features": {},
                "stage_reached": "ocr",
                "rejection_reason": "ocr_text_heavy",
            },
            {
                "orig_filename": "twin.jpg",
                "image_path": "/x/3-phash-rejected/phash__twin.jpg",
                "rank": None,
                "stage_reached": "phash",
                "rejection_reason": "dedup_phash",
                "dedup_kept": "auto.jpg",
            },
        ],
    }


async def _register_archive(db, document: dict, *, run_id: str, subject_kind: str) -> tuple:
    job_id = uuid4().hex
    fence_token = 1
    job = Job(
        id=job_id,
        type="poster_pipeline",
        payload_version=1,
        request={},
        phase="terminal",
        outcome="succeeded",
        desired_state="run",
        fence_token=fence_token,
        root_id=job_id,
        trigger_kind="manual",
        feature_area="posters",
        presentation_family="posters",
        subject_kind=subject_kind,
        subject_reference=run_id,
        subject_snapshot={"version": 1, "kind": subject_kind},
        terminal_at=datetime.now(UTC),
    )
    db.add(job)
    await db.flush()
    attempt = JobAttempt(
        job_id=job_id,
        number=1,
        fence_token=fence_token,
        phase="finished",
        outcome="succeeded",
        started_at=datetime.now(UTC),
        finished_at=datetime.now(UTC),
    )
    db.add(attempt)
    await db.flush()
    job.current_attempt_id = attempt.id

    selected_artifact = None
    for index, candidate in enumerate(document.get("candidates", [])):
        filename = str(candidate.get("orig_filename") or f"candidate-{index}.jpg")
        candidate_bytes = f"candidate:{run_id}:{filename}".encode()
        candidate_key = f"test-artifacts/{job_id}/candidate-{index}.jpg"
        candidate_file = Path(settings.DATA_DIR) / candidate_key
        candidate_file.parent.mkdir(parents=True, exist_ok=True)
        candidate_file.write_bytes(candidate_bytes)
        candidate_artifact = JobArtifact(
            job_id=job_id,
            attempt_id=attempt.id,
            kind="evidence_image",
            name=filename,
            status="available",
            storage_key=candidate_key,
            content_type="image/jpeg",
            size_bytes=len(candidate_bytes),
            checksum=sha256(candidate_bytes).hexdigest(),
            artifact_metadata={
                "family": "poster_pipeline_candidate",
                "run_id": run_id,
                "orig_filename": filename,
            },
        )
        db.add(candidate_artifact)
        await db.flush()
        candidate["artifact_id"] = candidate_artifact.id
        candidate["artifact_storage_key"] = candidate_key
        candidate["artifact_checksum"] = candidate_artifact.checksum
        if candidate.get("rank") == 1:
            selected_artifact = candidate_artifact

    payload = json.dumps(document, allow_nan=False).encode()
    storage_key = f"test-artifacts/{job_id}/pipeline-run.json"
    archive_file = Path(settings.DATA_DIR) / storage_key
    archive_file.parent.mkdir(parents=True, exist_ok=True)
    archive_file.write_bytes(payload)
    artifact = JobArtifact(
        job_id=job_id,
        attempt_id=attempt.id,
        kind="command_report",
        name="pipeline-run.json",
        status="available",
        storage_key=storage_key,
        content_type="application/json",
        size_bytes=len(payload),
        checksum=sha256(payload).hexdigest(),
        artifact_metadata={"family": "poster_pipeline", "run_id": run_id},
    )
    db.add(artifact)
    await db.flush()
    return job, attempt, artifact, selected_artifact


async def _seed(
    db,
    tmp_path,
    run_id="r1",
    *,
    auto_pick_filename: str | None = "auto.jpg",
    collecting: bool = False,
) -> Movie:
    movie = Movie(title="Die Hard", year=1988, folder_path="/m/Die Hard", tmdb_id=562)
    db.add(movie)
    await db.commit()
    await db.refresh(movie)

    archive = _archive(movie.id)
    if collecting:
        archive["personalization_mode"] = "collecting"
    job, attempt, artifact, selected = await _register_archive(
        db, archive, run_id=run_id, subject_kind="movie"
    )

    db.add(
        PipelineRun(
            run_id=run_id,
            movie_id=movie.id,
            status="completed",
            scorer_name=None if collecting else "weighted",
            job_id=job.id,
            attempt_id=attempt.id,
            fence_token=attempt.fence_token,
            archive_artifact_id=artifact.id,
            selected_artifact_id=(
                selected.id if selected is not None and auto_pick_filename is not None else None
            ),
            auto_pick_filename=auto_pick_filename,
        )
    )
    await db.commit()
    return movie


def _tv_archive(
    series_id: int, season_id: int | None = None, season_number: int | None = None
) -> dict:
    subject = {"series_id": series_id, "title": "Breaking Bad"}
    if season_id is not None:
        subject["season_id"] = season_id
    if season_number is not None:
        subject["season_number"] = season_number
    return {
        "media_type": "season" if season_id is not None else "series",
        "subject": subject,
        "title": "Breaking Bad"
        if season_id is None
        else f"Breaking Bad - Season {season_number:02d}",
        "tmdb_id": 1396,
        "candidates": [
            {
                "orig_filename": "auto.jpg",
                "image_path": "/x/ranked/1__0.9__auto.jpg",
                "rank": 1,
                "final_score": 0.9,
                "normalized_features": {"knn_sim": 0.9},
                "raw_features": {"knn_sim": 0.8},
                "extended_features": {},
                "stage_reached": "ranked",
                "rejection_reason": None,
            },
            {
                "orig_filename": "alt.jpg",
                "image_path": "/x/ranked/2__0.8__alt.jpg",
                "rank": 2,
                "final_score": 0.8,
                "normalized_features": {"knn_sim": 0.8},
                "raw_features": {"knn_sim": 0.7},
                "extended_features": {},
                "stage_reached": "ranked",
                "rejection_reason": None,
            },
        ],
    }


async def _seed_tv_run(db, tmp_path, *, run_id="tv-r1", media_type="series"):
    series = Series(
        title="Breaking Bad",
        year=2008,
        series_path=str(tmp_path / "Breaking Bad"),
        sonarr_id=101,
        tvdb_id=81189,
        tmdb_id=1396,
    )
    db.add(series)
    await db.flush()

    season = None
    if media_type == "season":
        season = Season(
            series_id=series.id,
            season_number=1,
            episode_count=7,
            episode_file_count=7,
        )
        db.add(season)
        await db.flush()

    archive = _tv_archive(
        series.id,
        season.id if season is not None else None,
        season.season_number if season is not None else None,
    )
    job, attempt, artifact, selected = await _register_archive(
        db, archive, run_id=run_id, subject_kind=media_type
    )

    db.add(
        PipelineRun(
            run_id=run_id,
            media_type=media_type,
            series_id=series.id,
            season_id=season.id if season is not None else None,
            status="completed",
            scorer_name="weighted",
            job_id=job.id,
            attempt_id=attempt.id,
            fence_token=attempt.fence_token,
            archive_artifact_id=artifact.id,
            selected_artifact_id=selected.id if selected is not None else None,
            auto_pick_filename="auto.jpg",
        )
    )
    await db.commit()
    await db.refresh(series)
    if season is not None:
        await db.refresh(season)
    return series, season


@pytest.mark.asyncio
async def test_scenario_a_approve(client, db, tmp_path):
    await _seed(db, tmp_path)
    resp = await client.post("/api/feedback", json={"run_id": "r1", "action": "approve"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["labels_written"] == 1

    event = await db.get(PosterPreferenceEvent, data["event_id"])
    assert event is not None
    assert event.action == "approval"
    assert event.training_context["selected_candidate"] == "auto.jpg"


@pytest.mark.asyncio
async def test_collecting_run_requires_an_explicit_choice_without_a_false_auto_pick(
    client, db, tmp_path
):
    await _seed(db, tmp_path, auto_pick_filename=None, collecting=True)

    approve = await client.post("/api/feedback", json={"run_id": "r1", "action": "approve"})
    reject = await client.post("/api/feedback", json={"run_id": "r1", "action": "reject_all"})

    assert approve.status_code == 400
    assert approve.json()["detail"] == "No auto-pick available to approve"
    assert reject.status_code == 400
    assert reject.json()["detail"] == "No auto-pick to reject"

    choose = await client.post(
        "/api/feedback",
        json={
            "run_id": "r1",
            "action": "override",
            "selected_filename": "alt.jpg",
            "deploy": False,
        },
    )

    assert choose.status_code == 200
    assert choose.json()["labels_written"] == 1
    event = await db.get(PosterPreferenceEvent, choose.json()["event_id"])
    assert event is not None
    assert event.training_context["selected_candidate"] == "alt.jpg"


@pytest.mark.asyncio
async def test_explicit_feedback_is_canonical_but_automatic_pick_alone_is_not(client, db, tmp_path):
    await _seed(db, tmp_path)
    assert await db.scalar(select(PosterPreferenceEvent)) is None
    assert await db.scalar(select(TasteExemplar)) is None

    response = await client.post(
        "/api/feedback",
        json={"run_id": "r1", "action": "approve", "deploy": False},
    )
    assert response.status_code == 200
    event = await db.get(PosterPreferenceEvent, response.json()["event_id"])
    assert event is not None
    assert event.action == "approval"
    assert event.namespace == "movies"
    assert event.training_context["selected_candidate"] == "auto.jpg"
    assert await db.scalar(select(TasteExemplar)) is None


@pytest.mark.asyncio
async def test_override_records_comparison_without_unrequested_negative(client, db, tmp_path):
    await _seed(db, tmp_path)
    response = await client.post(
        "/api/feedback",
        json={
            "run_id": "r1",
            "action": "override",
            "selected_filename": "alt.jpg",
            "deploy": False,
        },
    )
    assert response.status_code == 200
    event = await db.get(PosterPreferenceEvent, response.json()["event_id"])
    assert event is not None
    assert event.action == "override"
    assert event.training_context["selected_candidate"] == "alt.jpg"
    assert {row["candidate_id"] for row in event.exposed_candidates} >= {"auto.jpg", "alt.jpg"}
    assert list((await db.scalars(select(TasteExemplar))).all()) == []


@pytest.mark.asyncio
async def test_explicit_hate_pins_negative_and_undo_revokes_history(client, db, tmp_path):
    await _seed(db, tmp_path)
    response = await client.post(
        "/api/feedback",
        json={
            "run_id": "r1",
            "action": "rank",
            "order": ["auto.jpg"],
            "hated": ["alt.jpg"],
            "deploy": False,
        },
    )
    assert response.status_code == 200
    event_id = response.json()["event_id"]
    negative = await db.scalar(select(TasteExemplar).where(TasteExemplar.polarity == "negative"))
    assert negative is not None
    assert negative.status == "active"
    retained = await db.get(JobArtifact, negative.retained_artifact_id)
    assert retained is not None
    assert retained.retention_class == "pinned"

    undone = await client.post("/api/feedback/undo", json={"event_id": event_id})
    assert undone.status_code == 200
    await db.refresh(negative)
    assert negative.status == "revoked"
    undo_event = await db.scalar(
        select(PosterPreferenceEvent).where(
            PosterPreferenceEvent.action == "undo",
            PosterPreferenceEvent.supersedes_event_id == negative.preference_event_id,
        )
    )
    assert undo_event is not None


@pytest.mark.asyncio
async def test_scenario_b_override_ranked(client, db, tmp_path):
    await _seed(db, tmp_path)
    resp = await client.post(
        "/api/feedback",
        json={"run_id": "r1", "action": "override", "selected_filename": "alt.jpg"},
    )
    assert resp.status_code == 200
    assert resp.json()["labels_written"] == 2

    event = await db.get(PosterPreferenceEvent, resp.json()["event_id"])
    assert event is not None and event.action == "override"
    assert event.training_context["selected_candidate"] == "alt.jpg"


@pytest.mark.asyncio
async def test_scenario_c_override_reject_tracks_gate(client, db, tmp_path):
    await _seed(db, tmp_path)
    resp = await client.post(
        "/api/feedback",
        json={"run_id": "r1", "action": "override", "selected_filename": "ocrreject.jpg"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["gate_override"]["reason"] == "ocr_text_heavy"

    event = await db.get(PosterPreferenceEvent, data["event_id"])
    assert event is not None
    assert event.training_context["selected_candidate"] == "ocrreject.jpg"


@pytest.mark.asyncio
async def test_dedup_twin_override_remaps_to_survivor(client, db, tmp_path):
    await _seed(db, tmp_path)
    resp = await client.post(
        "/api/feedback",
        json={"run_id": "r1", "action": "override", "selected_filename": "twin.jpg"},
    )
    assert resp.status_code == 200
    assert resp.json()["remapped_to"] == "auto.jpg"
    # Picking the twin == approving the survivor: no self-override negative.
    event = await db.get(PosterPreferenceEvent, resp.json()["event_id"])
    assert event is not None
    assert event.training_context["selected_candidate"] == "auto.jpg"


@pytest.mark.asyncio
async def test_scenario_d_reject_all(client, db, tmp_path):
    await _seed(db, tmp_path)
    resp = await client.post("/api/feedback", json={"run_id": "r1", "action": "reject_all"})
    assert resp.status_code == 200
    event = await db.get(PosterPreferenceEvent, resp.json()["event_id"])
    assert event is not None and event.action in {"reject", "reject_all", "hate"}


@pytest.mark.asyncio
async def test_run_marked_reviewed(client, db, tmp_path):
    from sqlalchemy import select

    await _seed(db, tmp_path)
    resp = await client.post("/api/feedback", json={"run_id": "r1", "action": "approve"})
    event_id = resp.json()["event_id"]

    run = (await db.execute(select(PipelineRun).where(PipelineRun.run_id == "r1"))).scalar_one()
    await db.refresh(run)
    assert run.feedback_event_id == event_id


@pytest.mark.asyncio
async def test_bulk_auto_approve_reviews_entire_queue(client, db, tmp_path, monkeypatch):
    from sqlalchemy import func, select

    completed_ids: list[int] = []
    for i in range(61):
        movie = Movie(
            title=f"Bulk Movie {i}",
            year=2000 + i,
            folder_path=str(tmp_path / f"movie-{i}"),
            movie_file_path=f"bulk-{i}.mkv",
            tmdb_id=1000 + i,
        )
        db.add(movie)
        await db.flush()

        archive = _archive(movie.id)
        job, attempt, artifact, selected = await _register_archive(
            db, archive, run_id=f"bulk-{i}", subject_kind="movie"
        )
        db.add(
            PipelineRun(
                run_id=f"bulk-{i}",
                movie_id=movie.id,
                status="completed",
                scorer_name="weighted",
                job_id=job.id,
                attempt_id=attempt.id,
                fence_token=attempt.fence_token,
                archive_artifact_id=artifact.id,
                selected_artifact_id=selected.id if selected is not None else None,
                auto_pick_filename="auto.jpg",
            )
        )
        completed_ids.append(movie.id)

    manual_movie = Movie(
        title="Manual Review",
        year=1999,
        folder_path=str(tmp_path / "manual"),
        movie_file_path="manual.mkv",
        tmdb_id=4242,
    )
    db.add(manual_movie)
    await db.flush()
    manual_document = {"movie_id": manual_movie.id, "title": "Manual", "candidates": []}
    job, attempt, artifact, selected = await _register_archive(
        db, manual_document, run_id="manual-run", subject_kind="movie"
    )
    db.add(
        PipelineRun(
            run_id="manual-run",
            movie_id=manual_movie.id,
            status="flagged_manual",
            scorer_name=None,
            job_id=job.id,
            attempt_id=attempt.id,
            fence_token=attempt.fence_token,
            archive_artifact_id=artifact.id,
            selected_artifact_id=selected.id if selected is not None else None,
            auto_pick_filename=None,
        )
    )
    await db.commit()

    resp = await client.post("/api/pipeline/review-queue/approve-auto", json={"deploy": False})

    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 62
    assert data["approved"] == 61
    assert data["skipped_no_auto"] == 1
    assert data["failed"] == 0

    reviewed = await db.scalar(
        select(func.count())
        .select_from(PipelineRun)
        .where(PipelineRun.feedback_event_id.is_not(None))
    )
    assert reviewed == 61

    posters = await db.scalar(
        select(func.count()).select_from(Movie).where(Movie.poster_path.is_not(None))
    )
    assert posters == 0

    manual = (
        await db.execute(select(PipelineRun).where(PipelineRun.run_id == "manual-run"))
    ).scalar_one()
    await db.refresh(manual)
    assert manual.feedback_event_id is None


@pytest.mark.asyncio
async def test_undo_round_trip(client, db, tmp_path):
    from sqlalchemy import select

    await _seed(db, tmp_path)
    resp = await client.post("/api/feedback", json={"run_id": "r1", "action": "approve"})
    event_id = resp.json()["event_id"]
    undo = await client.post("/api/feedback/undo", json={"event_id": event_id})
    assert undo.status_code == 200
    assert undo.json()["removed_labels"] == 0
    assert undo.json()["exemplars_removed"] == []

    run = (await db.execute(select(PipelineRun).where(PipelineRun.run_id == "r1"))).scalar_one()
    await db.refresh(run)
    assert run.feedback_event_id is None


@pytest.mark.asyncio
async def test_undo_unknown_event_404(client, db):
    resp = await client.post("/api/feedback/undo", json={"event_id": "nope"})
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# rank action (v4 sortable-list ranking events — design 30)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rank_writes_v4_ranking_event(client, db, tmp_path):
    await _seed(db, tmp_path)
    resp = await client.post(
        "/api/feedback",
        json={
            "run_id": "r1",
            "action": "rank",
            "order": ["auto.jpg", "alt.jpg"],
            "hated": [],
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["labels_written"] == 1
    assert data["favorites_exemplars"] == []

    event = await db.get(PosterPreferenceEvent, data["event_id"])
    assert event is not None and event.action == "rank"
    assert event.training_context["order"] == ["auto.jpg", "alt.jpg"]
    exposed = {row["candidate_id"]: row for row in event.exposed_candidates}
    assert exposed["auto.jpg"]["baseline_rank"] == 1
    assert exposed["alt.jpg"]["baseline_rank"] == 4


@pytest.mark.asyncio
async def test_rank_records_hated_candidates_without_mutating_profile_folders(
    client, db, tmp_path, monkeypatch
):
    await _seed(db, tmp_path)
    monkeypatch.setattr(pipeline_settings, "FEEDBACK_HARD_NEGATIVE_RANK_MAX", 10)
    resp = await client.post(
        "/api/feedback",
        json={
            "run_id": "r1",
            "action": "rank",
            "order": ["auto.jpg"],
            "hated": ["alt.jpg", "ocrreject.jpg"],
        },
    )
    assert resp.status_code == 200
    assert resp.json()["negatives_added"] == []
    event = await db.get(PosterPreferenceEvent, resp.json()["event_id"])
    assert event is not None
    assert event.training_context["hated"] == ["alt.jpg", "ocrreject.jpg"]


@pytest.mark.asyncio
async def test_rank_undo_revokes_canonical_negative_without_erasing_history(client, db, tmp_path):
    await _seed(db, tmp_path)
    resp = await client.post(
        "/api/feedback",
        json={
            "run_id": "r1",
            "action": "rank",
            "order": ["auto.jpg"],
            "hated": ["alt.jpg"],
        },
    )
    event_id = resp.json()["event_id"]

    undo = await client.post("/api/feedback/undo", json={"event_id": event_id})
    assert undo.status_code == 200
    assert undo.json()["removed_labels"] == 0
    assert len(undo.json()["exemplars_removed"]) == 1
    assert undo.json()["negatives_removed"] == []
    exemplar = await db.get(TasteExemplar, undo.json()["exemplars_removed"][0])
    assert exemplar is not None and exemplar.status == "revoked"


@pytest.mark.asyncio
async def test_rank_requires_order_or_hated(client, db, tmp_path):
    await _seed(db, tmp_path)
    resp = await client.post(
        "/api/feedback",
        json={"run_id": "r1", "action": "rank", "order": [], "hated": []},
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_rank_unknown_filename_404(client, db, tmp_path):
    await _seed(db, tmp_path)
    resp = await client.post(
        "/api/feedback",
        json={"run_id": "r1", "action": "rank", "order": ["nope.jpg"]},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_rank_rejects_incomplete_coverage(client, db, tmp_path):
    await _seed(db, tmp_path)
    resp = await client.post(
        "/api/feedback",
        # alt.jpg is also ranked but missing from both order and hated.
        json={"run_id": "r1", "action": "rank", "order": ["auto.jpg"], "hated": []},
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_taste_status_shape(client, db):
    resp = await client.get("/api/taste/status")
    assert resp.status_code == 200
    data = resp.json()
    assert "labels" in data
    assert "exemplars" in data
    assert "ranking_residual" in data
    assert "gate_alerts" in data
    assert "activation" in data["ranking_residual"]


@pytest.mark.asyncio
async def test_tv_series_feedback_writes_to_tv_namespace(client, db, tmp_path):
    await _seed_tv_run(db, tmp_path, run_id="tv-series", media_type="series")

    resp = await client.post("/api/feedback", json={"run_id": "tv-series", "action": "approve"})
    assert resp.status_code == 200

    event = await db.get(PosterPreferenceEvent, resp.json()["event_id"])
    assert event is not None
    assert event.namespace == "tv"
    assert event.subject_kind == "series"


@pytest.mark.asyncio
async def test_tv_season_feedback_uses_series_root_and_season_filename(
    client, db, tmp_path, monkeypatch, installed_pgqueuer
):
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path)
    series, season = await _seed_tv_run(db, tmp_path, run_id="tv-season", media_type="season")

    resp = await client.post(
        "/api/feedback",
        json={
            "run_id": "tv-season",
            "action": "approve",
            "deploy": True,
            "idempotency_key": "poster_deploy:feedback-tv-season-1",
        },
    )
    assert resp.status_code == 202
    job_id = resp.json()["deployment_job"]["job_id"]
    job = await db.get(Job, job_id)
    assert job.type == "poster_deploy"
    assert job.subject_kind == "season"
    assert job.request["target_id"] == season.id
    assert not (Path(series.series_path) / f"season{season.season_number:02d}.jpg").exists()

    event = await db.get(PosterPreferenceEvent, resp.json()["event_id"])
    assert event is not None
    assert event.namespace == "tv"
    assert event.subject_kind == "season"
    assert event.subject_reference == str(season.id)


@pytest.mark.asyncio
async def test_tv_feedback_undo_uses_tv_namespace(client, db, tmp_path):
    await _seed_tv_run(db, tmp_path, run_id="tv-undo", media_type="season")

    resp = await client.post("/api/feedback", json={"run_id": "tv-undo", "action": "approve"})
    event_id = resp.json()["event_id"]
    undo = await client.post("/api/feedback/undo", json={"event_id": event_id})
    assert undo.status_code == 200
    assert undo.json()["exemplars_removed"] == []
    undo_event = await db.scalar(
        select(PosterPreferenceEvent).where(
            PosterPreferenceEvent.action == "undo",
            PosterPreferenceEvent.supersedes_event_id == event_id,
        )
    )
    assert undo_event is not None
    source_event = await db.get(PosterPreferenceEvent, event_id)
    assert source_event is not None
    assert source_event.revoked_event_id == undo_event.id


@pytest.mark.asyncio
async def test_review_reset_changes_disposition_only_not_poster_state(db):
    movie = Movie(
        title="Deployed Movie",
        year=2020,
        folder_path="/library/Deployed",
        tmdb_id=5551,
        poster_path="/library/Deployed/poster.jpg",
        poster_source="tmdb",
        poster_ai_selected=True,
        poster_deployed_filename="poster.jpg",
    )
    db.add(movie)
    await db.flush()
    run = await seed_canonical_pipeline_run(
        db,
        run_id="review-reset-run",
        movie_id=movie.id,
        archive={"run_id": "review-reset-run", "movie_id": movie.id, "candidates": []},
    )
    await db.commit()

    result = await reset_review_queue(db)

    assert result["reset"] == 1
    assert result["runs_cleared"] == 1
    # H19: the reset must not clear any poster state.
    assert result["posters_reset"] == 0

    await db.refresh(run)
    await db.refresh(movie)
    # Review disposition changed — the run leaves the Review tab.
    assert run.feedback_event_id is not None
    # Deployed poster DB state is completely untouched (no DB/filesystem disagreement).
    assert movie.poster_path == "/library/Deployed/poster.jpg"
    assert movie.poster_deployed_filename == "poster.jpg"
    assert movie.poster_source == "tmdb"
    assert movie.poster_ai_selected is True
