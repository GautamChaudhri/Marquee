"""Tests for the runtime knobs API and the rescore endpoint."""

from __future__ import annotations

import json

import pytest
from httpx import ASGITransport, AsyncClient

from marquee.core.pipeline_config import pipeline_settings
from marquee.main import app
from marquee.models import Movie, PipelineRun


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture(autouse=True)
def overrides_to_tmp(tmp_path, monkeypatch):
    # Redirect the overrides file and silence the extractor reset (no ML).
    import marquee.core.pipeline_config as cfg

    monkeypatch.setattr(cfg, "_overrides_path", lambda: tmp_path / "pipeline_overrides.json")
    from marquee.pipeline.run_manager import run_manager

    monkeypatch.setattr(run_manager, "reset_extractor", lambda: None)
    yield


# ---------------------------------------------------------------------------
# Config knobs
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_pipeline_config(client):
    resp = await client.get("/api/config/pipeline")
    assert resp.status_code == 200
    data = resp.json()
    assert "values" in data and "defaults" in data and "restart_required" in data
    assert "GATE_MIN_AESTHETIC" in data["values"]
    assert "AI_MODEL" in data["restart_required"]


def test_pipeline_snapshot_includes_full_ocr_context():
    snapshot = pipeline_settings.snapshot()
    assert "ocr" in snapshot
    assert {
        "device",
        "workers",
        "detail_passes",
        "max_residual_boxes",
        "max_residual_area_fraction",
        "mode",
        "require_title",
        "accept_no_text_fallback",
        "allow_title",
        "allow_director",
        "allow_studio",
        "allow_rating",
        "allow_tagline",
        "allow_billing",
        "confidence_threshold",
        "strip_confidence_threshold",
        "bottom_confidence_threshold",
        "fuzzy_cutoff",
        "title_proximity_pixels",
        "residual_significant_area_fraction",
        "residual_significant_width_fraction",
        "enhance_retry",
        "title_recovery_enabled",
        "title_recovery_confidence_threshold",
    }.issubset(snapshot["ocr"])


@pytest.mark.asyncio
async def test_put_valid_knob_applies_and_persists(client, tmp_path):
    original = pipeline_settings.GATE_MIN_AESTHETIC
    try:
        resp = await client.put(
            "/api/config/pipeline", json={"values": {"GATE_MIN_AESTHETIC": 3.0}}
        )
        assert resp.status_code == 200
        assert pipeline_settings.GATE_MIN_AESTHETIC == 3.0
        # Persisted to the overrides file.
        overrides = json.loads((tmp_path / "pipeline_overrides.json").read_text())
        assert overrides["GATE_MIN_AESTHETIC"] == 3.0
    finally:
        pipeline_settings.GATE_MIN_AESTHETIC = original


@pytest.mark.asyncio
async def test_put_invalid_value_rejected(client):
    # NORM_KNN_MAX must be > NORM_KNN_MIN — this violates the validator.
    resp = await client.put(
        "/api/config/pipeline",
        json={"values": {"NORM_KNN_MAX": 0.1, "NORM_KNN_MIN": 0.4}},
    )
    assert resp.status_code == 400
    assert "Invalid configuration" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_put_restart_required_rejected(client):
    resp = await client.put("/api/config/pipeline", json={"values": {"AI_MODEL": "something-else"}})
    assert resp.status_code == 400
    assert "restart" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_put_unknown_key_rejected(client):
    resp = await client.put("/api/config/pipeline", json={"values": {"NOT_A_KNOB": 1}})
    assert resp.status_code == 400
    assert "Unknown" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# Rescore
# ---------------------------------------------------------------------------


def _rescore_archive(movie_id: int) -> dict:
    # Two ranked candidates with opposing feature strengths.
    return {
        "movie_id": movie_id,
        "title": "Heat",
        "candidates": [
            {
                "orig_filename": "high_knn.jpg",
                "image_path": "/x/ranked/1.jpg",
                "rank": 1,
                "raw_features": {
                    "knn_sim": 0.9,
                    "aesthetic": 6.0,
                    "title_colorfulness": 10.0,
                    "text_residual": 0.0,
                    "resolution": 2.0,
                    "sharpness": 500.0,
                    "face_area": 0.5,
                    "provenance": 0.6,
                    "lang_match": 1.0,
                },
                "normalized_features": {"knn_sim": 0.95, "face_area": 0.5},
            },
            {
                "orig_filename": "low_face.jpg",
                "image_path": "/x/ranked/2.jpg",
                "rank": 2,
                "raw_features": {
                    "knn_sim": 0.6,
                    "aesthetic": 6.0,
                    "title_colorfulness": 10.0,
                    "text_residual": 0.0,
                    "resolution": 2.0,
                    "sharpness": 500.0,
                    "face_area": 0.0,
                    "provenance": 0.6,
                    "lang_match": 1.0,
                },
                "normalized_features": {"knn_sim": 0.6, "face_area": 1.0},
            },
        ],
    }


async def _seed_rescore(db, tmp_path) -> str:
    movie = Movie(title="Heat", year=1995, folder_path="/m/Heat", tmdb_id=949)
    db.add(movie)
    await db.commit()
    await db.refresh(movie)
    archive_file = tmp_path / "rs.json"
    archive = _rescore_archive(movie.id)
    archive_file.write_text(json.dumps(archive))
    db.add(
        PipelineRun(
            run_id="rs1",
            movie_id=movie.id,
            status="completed",
            archive_path=str(archive_file),
        )
    )
    await db.commit()
    return "rs1"


@pytest.mark.asyncio
async def test_rescore_reorders_when_face_weight_dominates(client, db, tmp_path):
    await _seed_rescore(db, tmp_path)
    # Weight only face_area: low_face.jpg (normalized face_area=1.0) should win.
    resp = await client.post(
        "/api/pipeline/runs/rs1/rescore",
        json={"weights": {"knn_sim": 0.0, "face_area": 1.0}},
    )
    assert resp.status_code == 200
    ranked = resp.json()["ranked"]
    assert ranked[0]["orig_filename"] == "low_face.jpg"
    assert ranked[0]["rank"] == 1


@pytest.mark.asyncio
async def test_rescore_knn_weight_keeps_high_knn_first(client, db, tmp_path):
    await _seed_rescore(db, tmp_path)
    resp = await client.post(
        "/api/pipeline/runs/rs1/rescore",
        json={"weights": {"knn_sim": 1.0, "face_area": 0.0}},
    )
    assert resp.status_code == 200
    ranked = resp.json()["ranked"]
    assert ranked[0]["orig_filename"] == "high_knn.jpg"
