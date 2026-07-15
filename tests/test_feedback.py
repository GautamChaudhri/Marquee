"""Feedback-core tests that don't require ML extras.

The profile-add and head-retrain steps are monkeypatched (they're the only
ML-touching parts); the label-writing, scenario mapping, dedup remap, gate
snapshot, and undo round-trip are exercised directly.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from marquee.api.routes import feedback as feedback_route
from marquee.config import settings
from marquee.core.pipeline_config import pipeline_settings
from marquee.main import app
from marquee.ml import feedback_store
from marquee.ml.namespaces import get_namespace
from marquee.models import Job, Movie, PipelineRun, Season, Series


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture(autouse=True)
def labels_to_tmp(tmp_path, monkeypatch):
    """Redirect the labels file to a temp path for every test."""
    monkeypatch.setattr(pipeline_settings, "FEEDBACK_LABELS_PATH", tmp_path / "labels.jsonl")
    monkeypatch.setattr(pipeline_settings, "FEEDBACK_DEPLOY_DEFAULT", False)
    # Avoid all ML: stub the profile-add and head-retrain hooks.
    monkeypatch.setattr(feedback_route, "_add_to_profile", lambda *a, **k: "Die Hard (1988).jpg")
    monkeypatch.setattr(
        feedback_route, "_maybe_retrain_head", lambda *a, **k: {"retrained": False, "reason": "stub"}
    )
    monkeypatch.setattr(feedback_route.run_manager, "reset_extractor", lambda: None)
    yield


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


async def _seed(db, tmp_path, run_id="r1") -> Movie:
    movie = Movie(title="Die Hard", year=1988, folder_path="/m/Die Hard", tmdb_id=562)
    db.add(movie)
    await db.commit()
    await db.refresh(movie)

    archive_file = tmp_path / f"{run_id}.json"
    archive = _archive(movie.id)
    archive_file.write_text(json.dumps(archive))

    db.add(
        PipelineRun(
            run_id=run_id,
            movie_id=movie.id,
            status="completed",
            scorer_name="weighted",
            archive_path=str(archive_file),
            output_dir=str(tmp_path),
        )
    )
    await db.commit()
    return movie


def _tv_archive(series_id: int, season_id: int | None = None, season_number: int | None = None) -> dict:
    subject = {"series_id": series_id, "title": "Breaking Bad"}
    if season_id is not None:
        subject["season_id"] = season_id
    if season_number is not None:
        subject["season_number"] = season_number
    return {
        "media_type": "season" if season_id is not None else "series",
        "subject": subject,
        "title": "Breaking Bad" if season_id is None else f"Breaking Bad - Season {season_number:02d}",
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

    archive_file = tmp_path / f"{run_id}.json"
    archive_file.write_text(
        json.dumps(
            _tv_archive(
                series.id,
                season.id if season is not None else None,
                season.season_number if season is not None else None,
            )
        )
    )

    db.add(
        PipelineRun(
            run_id=run_id,
            media_type=media_type,
            series_id=series.id,
            season_id=season.id if season is not None else None,
            status="completed",
            scorer_name="weighted",
            archive_path=str(archive_file),
            output_dir=str(tmp_path),
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

    rows = feedback_store.read_all()
    assert len(rows) == 1
    assert rows[0]["label"] == 1
    assert rows[0]["orig_filename"] == "auto.jpg"
    assert rows[0]["role"] == "user_pick"
    assert rows[0]["v"] == 2


@pytest.mark.asyncio
async def test_scenario_b_override_ranked(client, db, tmp_path):
    await _seed(db, tmp_path)
    resp = await client.post(
        "/api/feedback",
        json={"run_id": "r1", "action": "override", "selected_filename": "alt.jpg"},
    )
    assert resp.status_code == 200
    assert resp.json()["labels_written"] == 2

    rows = {r["orig_filename"]: r for r in feedback_store.read_all()}
    assert rows["auto.jpg"]["label"] == 0  # negative for the passed-over pick
    assert rows["auto.jpg"]["role"] == "auto_pick"
    assert rows["alt.jpg"]["label"] == 1  # positive for the chosen pick
    assert rows["alt.jpg"]["role"] == "user_pick"


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

    rows = {r["orig_filename"]: r for r in feedback_store.read_all()}
    pick = rows["ocrreject.jpg"]
    assert pick["label"] == 1
    assert pick["rejection_reason"] == "ocr_text_heavy"
    # Gate snapshot records the knob value at feedback time.
    assert (
        pick["gate_snapshot"]["OCR_MAX_RESIDUAL_BOXES"] == pipeline_settings.OCR_MAX_RESIDUAL_BOXES
    )


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
    rows = {r["orig_filename"]: r for r in feedback_store.read_all()}
    assert rows["auto.jpg"]["label"] == 1


@pytest.mark.asyncio
async def test_scenario_d_reject_all(client, db, tmp_path):
    await _seed(db, tmp_path)
    resp = await client.post("/api/feedback", json={"run_id": "r1", "action": "reject_all"})
    assert resp.status_code == 200
    rows = feedback_store.read_all()
    assert len(rows) == 1
    assert rows[0]["label"] == 0
    assert rows[0]["role"] == "explicit_reject"


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

        archive_file = tmp_path / f"bulk-{i}.json"
        archive = _archive(movie.id)
        archive_file.write_text(json.dumps(archive))
        db.add(
            PipelineRun(
                run_id=f"bulk-{i}",
                movie_id=movie.id,
                status="completed",
                scorer_name="weighted",
                archive_path=str(archive_file),
                output_dir=str(tmp_path),
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
    manual_archive = tmp_path / "manual.json"
    manual_archive.write_text(
        json.dumps({"movie_id": manual_movie.id, "title": "Manual", "candidates": []})
    )
    db.add(
        PipelineRun(
            run_id="manual-run",
            movie_id=manual_movie.id,
            status="flagged_manual",
            scorer_name=None,
            archive_path=str(manual_archive),
            output_dir=str(tmp_path),
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
async def test_undo_round_trip(client, db, tmp_path, monkeypatch):
    from sqlalchemy import select

    await _seed(db, tmp_path)
    removed_calls = []
    monkeypatch.setattr(
        feedback_route.profile_updater,
        "remove_exemplar",
        lambda name, namespace=None: removed_calls.append(name) or True,
    )

    resp = await client.post("/api/feedback", json={"run_id": "r1", "action": "approve"})
    event_id = resp.json()["event_id"]
    assert len(feedback_store.read_all()) == 1

    undo = await client.post("/api/feedback/undo", json={"event_id": event_id})
    assert undo.status_code == 200
    assert undo.json()["removed_labels"] == 1
    assert feedback_store.read_all() == []
    assert removed_calls == ["Die Hard (1988).jpg"]

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
    # positive_exemplar_count(2) == 1 -> only the top of the order is staged.
    assert data["favorites_exemplars"] == ["Die Hard (1988).jpg"]

    rows = feedback_store.read_all()
    assert len(rows) == 1
    row = rows[0]
    assert row["v"] == 4 and row["type"] == "ranking"
    assert [c["orig_filename"] for c in row["order"]] == ["auto.jpg", "alt.jpg"]
    assert row["order"][0]["pipeline_rank"] == 1
    assert row["order"][0]["baseline_rank"] == 1
    assert row["order"][1]["pipeline_rank"] == 4


@pytest.mark.asyncio
async def test_rank_mines_hard_negatives_by_rank(client, db, tmp_path, monkeypatch):
    await _seed(db, tmp_path)
    copied = []
    monkeypatch.setattr(
        feedback_route,
        "_copy_negative",
        lambda c, namespace: copied.append(c["orig_filename"]) or c["orig_filename"],
    )
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
    # alt.jpg ranked #4 (≤10) is a hard negative; ocrreject was never ranked.
    assert copied == ["alt.jpg"]
    assert resp.json()["negatives_added"] == ["alt.jpg"]


@pytest.mark.asyncio
async def test_rank_undo_removes_exemplars_and_negatives(client, db, tmp_path, monkeypatch):
    await _seed(db, tmp_path)
    removed_ex: list[str] = []
    removed_neg: list[str] = []
    monkeypatch.setattr(
        feedback_route, "_copy_negative", lambda c, namespace: c["orig_filename"]
    )
    monkeypatch.setattr(
        feedback_route.profile_updater,
        "remove_exemplar",
        lambda name, namespace=None: removed_ex.append(name) or True,
    )
    monkeypatch.setattr(
        feedback_route,
        "_remove_negative",
        lambda name, namespace=None: removed_neg.append(name) or True,
    )
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
    assert undo.json()["removed_labels"] == 1
    assert removed_ex == ["Die Hard (1988).jpg"]
    assert removed_neg == ["alt.jpg"]
    assert feedback_store.read_all() == []


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


# ---------------------------------------------------------------------------
# feedback_store unit tests
# ---------------------------------------------------------------------------


def test_store_append_read_remove(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline_settings, "FEEDBACK_LABELS_PATH", tmp_path / "l.jsonl")
    feedback_store.append_labels([{"event_id": "e1", "label": 1}, {"event_id": "e1", "label": 0}])
    feedback_store.append_labels([{"event_id": "e2", "label": 1}])
    assert len(feedback_store.read_all()) == 3

    removed = feedback_store.remove_event("e1")
    assert len(removed) == 2
    remaining = feedback_store.read_all()
    assert len(remaining) == 1
    assert remaining[0]["event_id"] == "e2"


def test_summary_counts_ranking_events(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline_settings, "FEEDBACK_LABELS_PATH", tmp_path / "l.jsonl")
    feedback_store.append_labels(
        [
            {
                "event_id": "e1",
                "type": "ranking",
                "v": 4,
                "movie_id": 1,
                "order": [
                    {"orig_filename": "a.jpg"},
                    {"orig_filename": "b.jpg"},
                    {"orig_filename": "c.jpg"},
                ],
                "hated": [{"orig_filename": "d.jpg"}],
            }
        ]
    )
    summary = feedback_store.summary()
    # positive_exemplar_count(3) == max(1, min(3, ceil(0.6))) == 1.
    assert summary["positives"] == 1
    assert summary["negatives"] == 1  # one hated poster
    assert summary["total"] == 2
    assert summary["movies"] == 1


@pytest.mark.asyncio
async def test_taste_status_shape(client, db):
    resp = await client.get("/api/taste/status")
    assert resp.status_code == 200
    data = resp.json()
    assert "labels" in data
    assert "exemplars" in data
    assert "learned_head" in data
    assert "gate_alerts" in data
    assert "activation" in data["learned_head"]


def test_gate_override_alert_respects_snapshot(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline_settings, "FEEDBACK_LABELS_PATH", tmp_path / "l.jsonl")
    monkeypatch.setattr(pipeline_settings, "OCR_MAX_RESIDUAL_BOXES", 0)
    # Five overrides recorded at the current threshold (0) → should count.
    current = [
        {
            "event_id": f"e{i}",
            "role": "user_pick",
            "action": "override",
            "rejection_reason": "ocr_text_heavy",
            "gate_snapshot": {"OCR_MAX_RESIDUAL_BOXES": 0},
        }
        for i in range(5)
    ]
    # One override recorded at a *different* threshold (2) → must NOT count.
    stale = [
        {
            "event_id": "old",
            "role": "user_pick",
            "action": "override",
            "rejection_reason": "ocr_text_heavy",
            "gate_snapshot": {"OCR_MAX_RESIDUAL_BOXES": 2},
        }
    ]
    feedback_store.append_labels(current + stale)

    alerts = {a["gate"]: a for a in feedback_store.gate_override_alerts()}
    assert alerts["ocr_text_heavy"]["overrides"] == 5
    assert alerts["ocr_text_heavy"]["active"] is True


@pytest.mark.asyncio
async def test_tv_series_feedback_writes_to_tv_namespace(client, db, tmp_path):
    await _seed_tv_run(db, tmp_path, run_id="tv-series", media_type="series")

    resp = await client.post("/api/feedback", json={"run_id": "tv-series", "action": "approve"})
    assert resp.status_code == 200

    movie_rows = feedback_store.read_all()
    tv_rows = feedback_store.read_all(get_namespace("tv"))
    assert movie_rows == []
    assert len(tv_rows) == 1
    assert tv_rows[0]["library"] == "tv"
    assert tv_rows[0]["media_type"] == "series"
    assert tv_rows[0]["series_id"] is not None
    assert tv_rows[0]["season_id"] is None
    assert tv_rows[0]["title"] == "Breaking Bad"


@pytest.mark.asyncio
async def test_tv_season_feedback_uses_series_root_and_season_filename(
    client, db, tmp_path, monkeypatch, installed_pgqueuer
):
    series, season = await _seed_tv_run(db, tmp_path, run_id="tv-season", media_type="season")
    originals = tmp_path / "0-originals"
    originals.mkdir()
    (originals / "auto.jpg").write_bytes(b"server-owned-candidate")
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path)

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

    tv_rows = feedback_store.read_all(get_namespace("tv"))
    assert len(tv_rows) == 1
    assert tv_rows[0]["media_type"] == "season"
    assert tv_rows[0]["series_id"] == series.id
    assert tv_rows[0]["season_id"] == season.id
    assert tv_rows[0]["title"] == "Breaking Bad - Season 01"


@pytest.mark.asyncio
async def test_tv_feedback_undo_uses_tv_namespace(client, db, tmp_path, monkeypatch):
    await _seed_tv_run(db, tmp_path, run_id="tv-undo", media_type="season")
    removed_calls = []
    monkeypatch.setattr(
        feedback_route.profile_updater,
        "remove_exemplar",
        lambda name, namespace=None: removed_calls.append((name, namespace.library)) or True,
    )

    resp = await client.post("/api/feedback", json={"run_id": "tv-undo", "action": "approve"})
    event_id = resp.json()["event_id"]
    assert len(feedback_store.read_all(get_namespace("tv"))) == 1

    undo = await client.post("/api/feedback/undo", json={"event_id": event_id})
    assert undo.status_code == 200
    assert feedback_store.read_all(get_namespace("tv")) == []
    assert removed_calls == [("Die Hard (1988).jpg", "tv")]
