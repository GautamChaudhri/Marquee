"""Feedback-core tests that don't require ML extras.

The profile-add and head-retrain steps are monkeypatched (they're the only
ML-touching parts); the label-writing, scenario mapping, dedup remap, gate
snapshot, and undo round-trip are exercised directly.
"""

from __future__ import annotations

import json

import pytest
from httpx import ASGITransport, AsyncClient

from marquee.api.routes import feedback as feedback_route
from marquee.core.pipeline_config import pipeline_settings
from marquee.main import app
from marquee.ml import feedback_store
from marquee.models import Movie, PipelineRun


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture(autouse=True)
def labels_to_tmp(tmp_path, monkeypatch):
    """Redirect the labels file to a temp path for every test."""
    monkeypatch.setattr(
        pipeline_settings, "FEEDBACK_LABELS_PATH", tmp_path / "labels.jsonl"
    )
    # Avoid all ML: stub the profile-add and head-retrain hooks.
    monkeypatch.setattr(
        feedback_route, "_add_to_profile", lambda *a, **k: "Die Hard (1988).jpg"
    )
    monkeypatch.setattr(
        feedback_route, "_maybe_retrain_head", lambda: {"retrained": False, "reason": "stub"}
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
    assert pick["gate_snapshot"]["OCR_MAX_RESIDUAL_BOXES"] == pipeline_settings.OCR_MAX_RESIDUAL_BOXES


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

    run = (
        await db.execute(select(PipelineRun).where(PipelineRun.run_id == "r1"))
    ).scalar_one()
    await db.refresh(run)
    assert run.feedback_event_id == event_id


@pytest.mark.asyncio
async def test_undo_round_trip(client, db, tmp_path, monkeypatch):
    from sqlalchemy import select

    await _seed(db, tmp_path)
    removed_calls = []
    monkeypatch.setattr(
        feedback_route.profile_updater,
        "remove_exemplar",
        lambda name: removed_calls.append(name) or True,
    )

    resp = await client.post("/api/feedback", json={"run_id": "r1", "action": "approve"})
    event_id = resp.json()["event_id"]
    assert len(feedback_store.read_all()) == 1

    undo = await client.post("/api/feedback/undo", json={"event_id": event_id})
    assert undo.status_code == 200
    assert undo.json()["removed_labels"] == 1
    assert feedback_store.read_all() == []
    assert removed_calls == ["Die Hard (1988).jpg"]

    run = (
        await db.execute(select(PipelineRun).where(PipelineRun.run_id == "r1"))
    ).scalar_one()
    await db.refresh(run)
    assert run.feedback_event_id is None


@pytest.mark.asyncio
async def test_undo_unknown_event_404(client, db):
    resp = await client.post("/api/feedback/undo", json={"event_id": "nope"})
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# rank action (v3 bucket-ranking events)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rank_writes_v3_ranking_event(client, db, tmp_path):
    await _seed(db, tmp_path)
    resp = await client.post(
        "/api/feedback",
        json={
            "run_id": "r1",
            "action": "rank",
            "favorites": [["auto.jpg"], ["alt.jpg"]],
            "hated": [],
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["labels_written"] == 1
    assert data["favorites_exemplars"] == ["Die Hard (1988).jpg"]

    rows = feedback_store.read_all()
    assert len(rows) == 1
    row = rows[0]
    assert row["v"] == 3 and row["type"] == "ranking"
    assert row["favorites"] == [["auto.jpg"], ["alt.jpg"]]
    buckets = {c["orig_filename"]: c for c in row["candidates"]}
    assert buckets["auto.jpg"]["bucket"] == "fav" and buckets["auto.jpg"]["tier"] == 1
    assert buckets["alt.jpg"]["bucket"] == "fav" and buckets["alt.jpg"]["tier"] == 2


@pytest.mark.asyncio
async def test_rank_mines_hard_negatives_by_rank(client, db, tmp_path, monkeypatch):
    await _seed(db, tmp_path)
    copied = []
    monkeypatch.setattr(
        feedback_route,
        "_copy_negative",
        lambda c: (copied.append(c["orig_filename"]) or c["orig_filename"]),
    )
    monkeypatch.setattr(pipeline_settings, "FEEDBACK_HARD_NEGATIVE_RANK_MAX", 10)
    resp = await client.post(
        "/api/feedback",
        json={
            "run_id": "r1",
            "action": "rank",
            "favorites": [["auto.jpg"]],
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
    monkeypatch.setattr(feedback_route, "_copy_negative", lambda c: c["orig_filename"])
    monkeypatch.setattr(
        feedback_route.profile_updater,
        "remove_exemplar",
        lambda name: removed_ex.append(name) or True,
    )
    monkeypatch.setattr(
        feedback_route,
        "_remove_negative",
        lambda name: removed_neg.append(name) or True,
    )
    resp = await client.post(
        "/api/feedback",
        json={
            "run_id": "r1",
            "action": "rank",
            "favorites": [["auto.jpg"]],
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
async def test_rank_requires_buckets(client, db, tmp_path):
    await _seed(db, tmp_path)
    resp = await client.post(
        "/api/feedback",
        json={"run_id": "r1", "action": "rank", "favorites": [], "hated": []},
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_rank_unknown_filename_404(client, db, tmp_path):
    await _seed(db, tmp_path)
    resp = await client.post(
        "/api/feedback",
        json={"run_id": "r1", "action": "rank", "favorites": [["nope.jpg"]]},
    )
    assert resp.status_code == 404


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
                "movie_id": 1,
                "favorites": [["a.jpg"], ["b.jpg"]],
                "hated": ["c.jpg"],
            }
        ]
    )
    summary = feedback_store.summary()
    assert summary["positives"] == 2  # two favorited posters
    assert summary["negatives"] == 1  # one hated poster
    assert summary["total"] == 3
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
            "event_id": f"e{i}", "role": "user_pick", "action": "override",
            "rejection_reason": "ocr_text_heavy",
            "gate_snapshot": {"OCR_MAX_RESIDUAL_BOXES": 0},
        }
        for i in range(5)
    ]
    # One override recorded at a *different* threshold (2) → must NOT count.
    stale = [
        {
            "event_id": "old", "role": "user_pick", "action": "override",
            "rejection_reason": "ocr_text_heavy",
            "gate_snapshot": {"OCR_MAX_RESIDUAL_BOXES": 2},
        }
    ]
    feedback_store.append_labels(current + stale)

    alerts = {a["gate"]: a for a in feedback_store.gate_override_alerts()}
    assert alerts["ocr_text_heavy"]["overrides"] == 5
    assert alerts["ocr_text_heavy"]["active"] is True
