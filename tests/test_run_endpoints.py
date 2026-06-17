"""Tests for the run machinery that don't require ML extras:

- ``RunState`` event buffering / replay / finish
- ``build_results_payload`` shaping from a fabricated archive
- ``explanations`` helpers
- the run GET endpoints (results, posters, history) against a seeded DB +
  an archive file, plus the 404 / 409 control paths
"""

from __future__ import annotations

import json

import pytest
from httpx import ASGITransport, AsyncClient

from marquee.api.explanations import (
    explain_rejection,
    explain_top_contributions,
    suggest_for_summary,
)
from marquee.api.results import build_results_payload, categorize_rejection
from marquee.main import app
from marquee.models import Movie, PipelineRun
from marquee.pipeline.run_manager import _SENTINEL, RunState, run_manager


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


# ---------------------------------------------------------------------------
# RunState
# ---------------------------------------------------------------------------


def test_runstate_replays_history_for_late_subscriber():
    state = RunState(run_id="r1")
    state.publish({"stage": "fetch", "state": "start"})
    state.publish({"stage": "fetch", "state": "end"})

    queue = state.subscribe()  # joins late — should still get both events
    assert queue.get_nowait()["stage"] == "fetch"
    assert queue.get_nowait()["state"] == "end"


def test_runstate_finish_sends_sentinel_to_all():
    state = RunState(run_id="r2")
    q1 = state.subscribe()
    state.publish({"stage": "rank", "state": "end"})
    q2 = state.subscribe()  # late joiner gets the buffered event...
    state.finish()
    # q1 sees the live event then sentinel
    assert q1.get_nowait()["stage"] == "rank"
    assert q1.get_nowait() is _SENTINEL
    # q2 sees the replayed event then sentinel
    assert q2.get_nowait()["stage"] == "rank"
    assert q2.get_nowait() is _SENTINEL


def test_runstate_unsubscribe_stops_delivery():
    state = RunState(run_id="r3")
    queue = state.subscribe()
    state.unsubscribe(queue)
    state.publish({"stage": "ocr", "state": "start"})
    assert queue.empty()


# ---------------------------------------------------------------------------
# Explanations
# ---------------------------------------------------------------------------


def test_explain_rejection_known_and_unknown():
    assert "non-title text" in explain_rejection("ocr_text_heavy")
    assert explain_rejection("feature_error: boom").startswith("Could not be processed")
    assert explain_rejection(None) == "Rejected."
    # Unknown code degrades gracefully to a humanized form.
    assert explain_rejection("some_new_reason") == "Some new reason."


def test_explain_top_contributions_orders_and_labels():
    contributions = {"knn_sim": 0.4, "face_area": 0.1, "official_family": 0.3, "aesthetic": 0.0}
    labels = explain_top_contributions(contributions, top_n=2)
    assert labels[0] == "Style match to your taste profile"
    assert labels[1] == "Official key-art family"
    assert len(labels) == 2  # aesthetic (0.0) excluded


def test_suggest_for_summary_picks_dominant():
    suggestion = suggest_for_summary({"ocr_text_heavy": 15, "style_aesthetic_floor": 4})
    assert "OCR_MAX_RESIDUAL_BOXES" in suggestion
    assert suggest_for_summary({}) is None


# ---------------------------------------------------------------------------
# Results shaping
# ---------------------------------------------------------------------------


def _fake_archive() -> dict:
    return {
        "movie_id": 1,
        "title": "Die Hard",
        "tmdb_id": 562,
        "stage_timings_seconds": {"fetch": 0.5},
        "config": {"ai_model": "clip-vit-b-32"},
        "candidates": [
            {
                "orig_filename": "a.jpg",
                "image_path": "/x/ranked/1__0.9__a.jpg",
                "rank": 1,
                "final_score": 0.9,
                "contributions": {"knn_sim": 0.5, "official_family": 0.3},
                "raw_features": {"knn_sim": 0.8},
                "normalized_features": {"knn_sim": 0.9},
                "stage_reached": "ranked",
                "rejection_reason": None,
            },
            {
                "orig_filename": "b.jpg",
                "image_path": "/x/ranked/2__0.7__b.jpg",
                "rank": 2,
                "final_score": 0.7,
                "contributions": {"knn_sim": 0.4},
                "stage_reached": "ranked",
                "rejection_reason": None,
            },
            {
                "orig_filename": "c.jpg",
                "image_path": "/x/2-ocr-rejected/ocr_text_heavy__c.jpg",
                "rank": None,
                "stage_reached": "ocr",
                "rejection_reason": "ocr_text_heavy",
            },
            {
                "orig_filename": "d.jpg",
                "image_path": "/x/3-phash-rejected/phash__d.jpg",
                "rank": None,
                "stage_reached": "phash",
                "rejection_reason": "dedup_phash",
                "dedup_kept": "a.jpg",
            },
        ],
    }


def test_categorize_rejection_buckets():
    assert categorize_rejection({"rejection_reason": "dedup_phash"}) == "dedup"
    assert categorize_rejection({"rejection_reason": "ocr_text_heavy"}) == "ocr"
    assert categorize_rejection({"rejection_reason": "feature_error: x"}) == "errored"
    assert categorize_rejection({"rejection_reason": "style_aesthetic_floor"}) == "gate"


def test_build_results_payload_shape():
    payload = build_results_payload(
        _fake_archive(), run_id="run1", status="completed", reviewed=False, scorer="weighted"
    )
    assert payload["auto_pick"]["orig_filename"] == "a.jpg"
    assert payload["auto_pick"]["explanations"][0] == "Style match to your taste profile"
    assert payload["auto_pick"]["poster_url"] == "/api/pipeline/runs/run1/posters/a.jpg"
    assert len(payload["ranked"]) == 2
    assert len(payload["rejected"]["ocr"]) == 1
    assert len(payload["rejected"]["dedup"]) == 1
    assert payload["rejected"]["dedup"][0]["dedup_kept"] == "a.jpg"
    assert payload["rejection_summary"] == {"ocr_text_heavy": 1, "dedup_phash": 1}
    assert "OCR_MAX_RESIDUAL_BOXES" in payload["suggestion"]


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_run_results_404(client, db):
    resp = await client.get("/api/pipeline/runs/does-not-exist")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_run_results_from_archive(client, db, tmp_path):
    movie = Movie(title="Die Hard", year=1988, folder_path="/m/Die Hard", tmdb_id=562)
    db.add(movie)
    await db.commit()
    await db.refresh(movie)

    archive_file = tmp_path / "run-xyz.json"
    archive = _fake_archive()
    archive["movie_id"] = movie.id
    archive_file.write_text(json.dumps(archive))

    db.add(
        PipelineRun(
            run_id="run-xyz",
            movie_id=movie.id,
            status="completed",
            scorer_name="weighted",
            archive_path=str(archive_file),
        )
    )
    await db.commit()

    resp = await client.get("/api/pipeline/runs/run-xyz")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "completed"
    assert data["scorer"] == "weighted"
    assert data["auto_pick"]["orig_filename"] == "a.jpg"
    assert data["reviewed"] is False


@pytest.mark.asyncio
async def test_list_movie_runs(client, db):
    movie = Movie(title="Heat", year=1995, folder_path="/m/Heat", tmdb_id=949)
    db.add(movie)
    await db.commit()
    await db.refresh(movie)
    db.add(PipelineRun(run_id="h1", movie_id=movie.id, status="completed"))
    await db.commit()

    resp = await client.get(f"/api/movies/{movie.id}/runs")
    assert resp.status_code == 200
    data = resp.json()
    assert data["movie_id"] == movie.id
    assert len(data["runs"]) == 1
    assert data["runs"][0]["run_id"] == "h1"


@pytest.mark.asyncio
async def test_run_conflict_returns_409(client, db, monkeypatch):
    movie = Movie(title="Alien", year=1979, folder_path="/m/Alien", tmdb_id=348)
    db.add(movie)
    await db.commit()
    await db.refresh(movie)

    # get_tmdb resolves before the handler — give it a dummy client so the
    # request reaches the busy-check rather than 503-ing on missing config.
    app.state.tmdb_client = object()
    # Simulate a busy manager without triggering a real run.
    monkeypatch.setattr(run_manager, "_active_run_id", "busy-run")
    try:
        resp = await client.post(f"/api/pipeline/movie/{movie.id}/run")
        assert resp.status_code == 409
        assert resp.json()["detail"]["active_run_id"] == "busy-run"
    finally:
        run_manager._active_run_id = None
        app.state.tmdb_client = None


@pytest.mark.asyncio
async def test_events_404_for_unknown_run(client):
    resp = await client.get("/api/pipeline/runs/nope/events")
    assert resp.status_code == 404


def test_gpu_busy_reports_run_and_rebuild():
    assert run_manager.gpu_busy() is None
    run_manager._active_run_id = "run-9"
    try:
        assert "run-9" in run_manager.gpu_busy()
    finally:
        run_manager._active_run_id = None
    run_manager.begin_rebuild()
    try:
        assert run_manager.gpu_busy() == "taste-profile rebuild"
    finally:
        run_manager.end_rebuild()
    assert run_manager.gpu_busy() is None


def test_release_gpu_resources_clears_cached_extractor():
    run_manager._extractor = object()
    result = run_manager.release_gpu_resources()
    assert run_manager._extractor is None
    assert result["extractor_cleared"] is True

    result = run_manager.release_gpu_resources()
    assert result["extractor_cleared"] is False


@pytest.mark.asyncio
async def test_release_gpu_endpoint_reports_busy(client):
    run_manager.begin_rebuild()
    try:
        resp = await client.post("/api/system/release-gpu")
    finally:
        run_manager.end_rebuild()

    assert resp.status_code == 200
    assert resp.json()["status"] == "busy"


@pytest.mark.asyncio
async def test_run_refused_during_rebuild(client, db):
    movie = Movie(title="Tron", year=1982, folder_path="/m/Tron", tmdb_id=97)
    db.add(movie)
    await db.commit()
    await db.refresh(movie)

    app.state.tmdb_client = object()
    run_manager.begin_rebuild()
    try:
        resp = await client.post(f"/api/pipeline/movie/{movie.id}/run")
        assert resp.status_code == 409
        assert resp.json()["detail"]["active_run_id"] == "taste-profile rebuild"
    finally:
        run_manager.end_rebuild()
        app.state.tmdb_client = None


@pytest.mark.asyncio
async def test_retrain_refused_during_run(client, db):
    run_manager._active_run_id = "busy"
    try:
        resp = await client.post("/api/taste/retrain")
        assert resp.status_code == 409
        assert "busy" in resp.json()["detail"]["active"]
    finally:
        run_manager._active_run_id = None


class _FakeQueue:
    def __init__(self, messages=None):
        self._messages = list(messages or [])
        self.closed = False

    def get_nowait(self):
        import queue

        if not self._messages:
            raise queue.Empty
        return self._messages.pop(0)

    def close(self):
        self.closed = True


class _FakeProcess:
    def __init__(self, exitcode=0):
        self.exitcode = exitcode

    def is_alive(self):
        return False

    def terminate(self):
        raise AssertionError("dead process should not be terminated")

    def kill(self):
        raise AssertionError("dead process should not be killed")

    def join(self, timeout=None):
        return None


@pytest.mark.asyncio
async def test_taste_rebuild_monitor_marks_completed(monkeypatch):
    from marquee.api.routes import taste as taste_route

    monkeypatch.setattr(run_manager, "release_gpu_resources", lambda: {})
    run_manager.begin_rebuild()
    started = taste_route._mark_rebuild_started()

    await taste_route._monitor_rebuild_process(_FakeProcess(0), _FakeQueue(), started)

    assert taste_route._rebuild_state["status"] == "completed"
    assert taste_route._rebuild_state["running"] is False
    assert taste_route._rebuild_state["finished_at"]
    assert run_manager.gpu_busy() is None


@pytest.mark.asyncio
async def test_taste_rebuild_monitor_records_failure(monkeypatch):
    from marquee.api.routes import taste as taste_route

    monkeypatch.setattr(run_manager, "release_gpu_resources", lambda: {})
    run_manager.begin_rebuild()
    started = taste_route._mark_rebuild_started()
    queue = _FakeQueue([{"type": "error", "error": "boom"}])

    await taste_route._monitor_rebuild_process(_FakeProcess(1), queue, started)

    assert taste_route._rebuild_state["status"] == "failed"
    assert taste_route._rebuild_state["running"] is False
    assert taste_route._rebuild_state["error"] == "boom"
    assert taste_route._rebuild_state["finished_at"]
    assert run_manager.gpu_busy() is None
