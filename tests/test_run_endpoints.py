"""Tests for the run machinery that don't require ML extras:

- ``build_results_payload`` shaping from a fabricated archive
- ``explanations`` helpers
- the run GET endpoints (results, posters, history) against a seeded DB +
  an archive file, plus the 404 / 409 control paths
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

import numpy as np
import pytest
from httpx import ASGITransport, AsyncClient
from PIL import Image

from marquee.api.explanations import (
    explain_rejection,
    explain_top_contributions,
    rejection_label,
    suggest_for_summary,
)
from marquee.api.results import build_results_payload, categorize_rejection
from marquee.config import settings
from marquee.main import app
from marquee.models import Job, JobArtifact, Movie, PipelineRun
from marquee.models.job import JobAttempt


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


async def _canonical_run(db, *, run_id: str, movie_id: int, archive: dict) -> PipelineRun:
    job_id = uuid4().hex
    now = datetime.now(UTC)
    job = Job(
        id=job_id,
        type="poster_pipeline",
        payload_version=1,
        request={},
        phase="terminal",
        outcome="succeeded",
        desired_state="run",
        fence_token=1,
        root_id=job_id,
        trigger_kind="manual",
        feature_area="posters",
        presentation_family="posters",
        subject_kind="movie",
        subject_reference=str(movie_id),
        subject_snapshot={"version": 1, "kind": "movie", "id": movie_id},
        terminal_at=now,
    )
    db.add(job)
    await db.flush()
    attempt = JobAttempt(
        job_id=job_id,
        number=1,
        fence_token=1,
        phase="finished",
        outcome="succeeded",
        started_at=now,
        finished_at=now,
    )
    db.add(attempt)
    await db.flush()
    job.current_attempt_id = attempt.id
    payload = json.dumps(archive, allow_nan=False).encode()
    storage_key = f"test-artifacts/{job_id}/pipeline-run.json"
    path = Path(settings.DATA_DIR) / storage_key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
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
    run = PipelineRun(
        run_id=run_id,
        movie_id=movie_id,
        status="completed",
        scorer_name="weighted",
        job_id=job_id,
        attempt_id=attempt.id,
        fence_token=attempt.fence_token,
        archive_artifact_id=artifact.id,
    )
    db.add(run)
    return run


# ---------------------------------------------------------------------------
# Explanations
# ---------------------------------------------------------------------------


def test_explain_rejection_known_and_unknown():
    assert "non-title text" in explain_rejection("ocr_text_heavy")
    assert explain_rejection("feature_error: boom").startswith("Could not be processed")
    assert explain_rejection(None) == "Rejected."
    # Unknown code degrades gracefully to a humanized form.
    assert explain_rejection("some_new_reason") == "Some new reason."


def test_ocr_rejection_labels_are_short_and_media_neutral():
    assert rejection_label("text_heavy") == "Text heavy"
    assert rejection_label("ocr_no_text") == "No text found"
    assert rejection_label("no_title") == "Title not matched"
    assert rejection_label("format_blocklist") == "Format badge"
    assert rejection_label("ocr_error: worker unavailable") == "OCR error"
    assert "movie" not in explain_rejection("no_title").lower()


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
    # No stack metadata in the fixture → flat fallback (no stacks, auto = rank 1).
    assert payload["stacks"] == []


def test_build_results_payload_exposes_compact_ocr_evidence_and_error_details():
    archive = _fake_archive()
    by_name = {candidate["orig_filename"]: candidate for candidate in archive["candidates"]}
    by_name["c.jpg"].update(
        rejection_reason="text_heavy",
        ocr_detected_text="DIE HARD ONLY IN THEATERS",
        ocr_title_bbox=[[10, 20], [110, 20], [110, 60], [10, 60]],
        ocr_display_regions=[
            {
                "text": "DIE HARD",
                "confidence": 0.98,
                "category": "title",
                "is_title": True,
                "is_title_fragment": False,
                "is_significant": False,
            },
            {
                "text": "ONLY IN THEATERS",
                "confidence": 0.84,
                "category": "tagline",
                "is_title": False,
                "is_title_fragment": False,
                "is_significant": True,
            },
        ],
    )
    archive["candidates"].append(
        {
            "orig_filename": "ocr-error.jpg",
            "image_path": "/x/errored/ocr_error__ocr-error.jpg",
            "rank": None,
            "stage_reached": "ocr",
            "rejection_reason": "ocr_error: PaddleOCR could not initialize",
            "ocr_detected_text": "",
            "ocr_residual_boxes": [],
        }
    )
    archive["candidates"].append(
        {
            "orig_filename": "ocr-worker.jpg",
            "image_path": "/x/errored/ocr_error__ocr-worker.jpg",
            "rank": None,
            "stage_reached": "ocr",
            "rejection_reason": "ocr_error",
            "ocr_detected_text": "",
            "ocr_residual_boxes": [],
        }
    )

    payload = build_results_payload(
        archive, run_id="run1", status="completed", reviewed=False, scorer="weighted"
    )
    text_heavy = next(
        candidate
        for candidate in payload["rejected"]["ocr"]
        if candidate["orig_filename"] == "c.jpg"
    )
    assert text_heavy["rejection_label"] == "Text heavy"
    assert text_heavy["ocr_evidence"] == {
        "available": True,
        "has_text": True,
        "detected_text": "DIE HARD ONLY IN THEATERS",
        "title_matched": True,
        "regions": by_name["c.jpg"]["ocr_display_regions"],
        "error": None,
    }

    ocr_error = next(
        candidate
        for candidate in payload["rejected"]["ocr"]
        if candidate["orig_filename"] == "ocr-error.jpg"
    )
    assert ocr_error["rejection_label"] == "OCR error"
    assert ocr_error["ocr_evidence"]["has_text"] is False
    assert ocr_error["ocr_evidence"]["error"] == "PaddleOCR could not initialize"

    ocr_worker = next(
        candidate
        for candidate in payload["rejected"]["ocr"]
        if candidate["orig_filename"] == "ocr-worker.jpg"
    )
    assert ocr_worker["ocr_evidence"]["error"] == "OCR worker was unavailable."


def test_build_results_payload_uses_legacy_ocr_residuals_when_compact_regions_are_missing():
    archive = _fake_archive()
    legacy = archive["candidates"][2]
    legacy.update(
        rejection_reason="no_title",
        ocr_detected_text="TAGLINE ONLY",
        ocr_title_bbox=None,
        ocr_residual_boxes=[{"text": "TAGLINE ONLY", "confidence": 0.74}],
    )

    payload = build_results_payload(
        archive, run_id="run1", status="completed", reviewed=False, scorer="weighted"
    )
    evidence = payload["rejected"]["ocr"][0]["ocr_evidence"]
    assert evidence["has_text"] is True
    assert evidence["title_matched"] is False
    assert evidence["regions"] == [
        {
            "text": "TAGLINE ONLY",
            "confidence": 0.74,
            "category": None,
            "is_title": False,
            "is_title_fragment": False,
            "is_significant": False,
        }
    ]


def test_rejected_ocr_candidates_archive_compact_display_regions():
    from marquee.pipeline.runner import _attach_ocr_diagnostics
    from marquee.pipeline.types import CandidateScore, OCRCandidateResult

    record = CandidateScore(image_path=Path("candidate.jpg"), orig_filename="candidate.jpg")
    result = OCRCandidateResult(
        image_path=Path("candidate.jpg"),
        accepted=False,
        detected_text="EXAMPLE TITLE ONLY IN THEATERS",
        reason="text_heavy",
        title_bbox=None,
        diagnostics={
            "detected_boxes": [
                {
                    "text": "EXAMPLE TITLE",
                    "confidence": 0.97,
                    "category": "title",
                    "is_title": True,
                    "is_title_fragment": False,
                    "is_significant": False,
                    "bbox": [[0, 0]],
                },
                {
                    "text": "ONLY IN THEATERS",
                    "confidence": 0.81,
                    "category": "tagline",
                    "is_title": False,
                    "is_title_fragment": False,
                    "is_significant": True,
                    "bbox": [[0, 0]],
                },
            ]
        },
    )

    _attach_ocr_diagnostics(record, result)

    assert record.ocr_display_regions == [
        {
            "text": "EXAMPLE TITLE",
            "confidence": 0.97,
            "category": "title",
            "is_title": True,
            "is_title_fragment": False,
            "is_significant": False,
        },
        {
            "text": "ONLY IN THEATERS",
            "confidence": 0.81,
            "category": "tagline",
            "is_title": False,
            "is_title_fragment": False,
            "is_significant": True,
        },
    ]


def test_build_results_payload_with_stacks():
    archive = _fake_archive()
    by_name = {c["orig_filename"]: c for c in archive["candidates"]}
    # b.jpg (global rank 2) is the top *design*; a.jpg (global rank 1) is design 2.
    by_name["b.jpg"].update(
        stack_id=0, stack_rank=1, stack_pos=1, stack_label="A", stack_size=1, stack_score=0.72
    )
    by_name["a.jpg"].update(
        stack_id=1, stack_rank=2, stack_pos=1, stack_label="A", stack_size=1, stack_score=0.90
    )
    payload = build_results_payload(
        archive, run_id="r", status="completed", reviewed=False, scorer="weighted"
    )
    assert [s["stack_rank"] for s in payload["stacks"]] == [1, 2]
    assert payload["stacks"][0]["representative"]["orig_filename"] == "b.jpg"
    # Auto-pick follows the stack (1A = b.jpg), not global rank 1 (a.jpg).
    assert payload["auto_pick"]["orig_filename"] == "b.jpg"
    # The flat ranked list is still global-rank ordered.
    assert payload["ranked"][0]["orig_filename"] == "a.jpg"


def test_write_run_json_coerces_numpy(tmp_path):
    """Regression: feature extras (calibration typicality, zero-shot axes) emit
    numpy scalars/arrays; the archive write must coerce them, not raise."""
    import numpy as np

    from marquee.pipeline.runner import write_run_json

    path = tmp_path / "run.json"
    write_run_json(
        path,
        {
            "score": np.float32(0.5),
            "vec": np.array([1.0, 2.0], dtype=np.float32),
            "count": np.int64(3),
            "flag": np.bool_(True),
            "nested": {"x": np.float32(1.25)},
        },
    )
    data = json.loads(path.read_text())
    assert data == {
        "score": 0.5,
        "vec": [1.0, 2.0],
        "count": 3,
        "flag": True,
        "nested": {"x": 1.25},
    }


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

    archive = _fake_archive()
    archive["movie_id"] = movie.id
    await _canonical_run(db, run_id="run-xyz", movie_id=movie.id, archive=archive)
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
    archive = _fake_archive()
    archive["movie_id"] = movie.id
    await _canonical_run(db, run_id="h1", movie_id=movie.id, archive=archive)
    await db.commit()

    resp = await client.get(f"/api/movies/{movie.id}/runs")
    assert resp.status_code == 200
    data = resp.json()
    assert data["movie_id"] == movie.id
    assert len(data["runs"]) == 1
    assert data["runs"][0]["run_id"] == "h1"


@pytest.mark.asyncio
async def test_events_404_for_unknown_run(client):
    resp = await client.get("/api/pipeline/runs/nope/events")
    assert resp.status_code == 404


def test_taste_rebuild_has_no_process_local_execution_state():
    from marquee.api.routes import taste as taste_route

    for removed in (
        "_rebuild_state",
        "_rebuild_process",
        "_rebuild_queue",
        "_rebuild_cancel_requested",
        "_mark_rebuild_started",
        "_apply_rebuild_progress",
        "_monitor_rebuild_process",
    ):
        assert not hasattr(taste_route, removed)


def test_measure_exemplar_features_reports_substage_progress(tmp_path, monkeypatch):
    from marquee.ml import taste_trainer
    from marquee.pipeline.types import OCRCandidateResult

    image_path = tmp_path / "Example Movie (2024).jpg"
    Image.new("RGB", (32, 48), color="navy").save(image_path)
    events: list[dict] = []

    class FakeAesthetic:
        def score_batch(self, embeddings):
            return np.asarray([1.5], dtype=np.float32)

    class FakeAxes:
        def scores(self, embedding):
            return {"axis_fake": 0.25}

    class FakeFaceDetector:
        def detect(self, image):
            return []

    class FakePersonDetector:
        def person_features(self, image):
            return {"person_count": 0.0, "person_area_frac": 0.0}

    class FakePosterTextFilter:
        def __init__(self, title):
            self.title = title

        def is_acceptable(self, path: Path):
            return OCRCandidateResult(path, True, "example movie", None, None)

    from marquee.pipeline import ocr_filter

    monkeypatch.setattr(ocr_filter, "PosterTextFilter", FakePosterTextFilter)
    names, matrix = taste_trainer.measure_exemplar_features(
        [image_path],
        np.zeros((1, 512), dtype=np.float32),
        aesthetic=FakeAesthetic(),
        axes=FakeAxes(),
        face_detector=FakeFaceDetector(),
        person_detector=FakePersonDetector(),
        run_ocr=True,
        progress_callback=events.append,
    )

    substages = [event.get("substage") for event in events]
    assert "cv" in substages
    assert "face" in substages
    assert "person" in substages
    assert "ocr" in substages
    assert events[-1]["processed"] == 1
    assert events[-1]["current_item"] == image_path.name
    assert "aesthetic" in names
    assert matrix.shape[1] == 1
