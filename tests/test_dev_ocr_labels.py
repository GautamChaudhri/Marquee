from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

import marquee.pipeline.ocr_label_capture as ocr_label_capture
from marquee.config import settings
from marquee.main import app
from marquee.models import Movie, PipelineRun


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture(autouse=True)
def isolated_capture_root(tmp_path, monkeypatch):
    root = tmp_path / "ocr-labels"

    def _capture_root() -> Path:
        root.mkdir(parents=True, exist_ok=True)
        return root

    monkeypatch.setattr(ocr_label_capture, "capture_root", _capture_root)
    yield


def _full_ocr_snapshot() -> dict[str, object]:
    return {
        "device": "cpu",
        "workers": 1,
        "detail_passes": 1,
        "max_residual_boxes": 0,
        "max_residual_area_fraction": 0.04,
        "mode": "title_only",
        "require_title": True,
        "accept_no_text_fallback": False,
        "allow_title": True,
        "allow_director": False,
        "allow_studio": False,
        "allow_rating": False,
        "allow_tagline": False,
        "confidence_threshold": 0.75,
        "strip_confidence_threshold": 0.65,
        "bottom_confidence_threshold": 0.5,
        "fuzzy_cutoff": 0.6,
        "title_proximity_pixels": 30.0,
        "residual_significant_area_fraction": 0.005,
        "residual_significant_width_fraction": 0.4,
        "enhance_retry": True,
        "title_recovery_enabled": True,
        "title_recovery_confidence_threshold": 0.5,
    }


def _sample_trace() -> dict[str, object]:
    """A full structured OCR trace, as ocr_filter._process_image emits it."""
    return {
        "image_size": {"width": 1000, "height": 1500},
        "passes_run": ["full", "top", "bottom"],
        "enhance_retry": {"triggered": False, "recovered_text": False},
        "title_recovery": {
            "enabled": True,
            "triggered": False,
            "recovered_title": False,
            "confidence_threshold": 0.5,
            "error": None,
        },
        "detected_boxes": [
            {
                "text": "ALIEN ROMULUS",
                "confidence": 0.98,
                "bbox": [[10.0, 20.0], [110.0, 20.0], [110.0, 60.0], [10.0, 60.0]],
                "area": 4000.0,
                "geometry_valid": True,
                "pass": "full",
                "category": "title",
                "is_title": True,
                "is_residual": False,
                "is_significant": False,
                "significant_reason": None,
                "proximity_discounted": False,
            },
            {
                "text": "ONLY IN THEATERS",
                "confidence": 0.91,
                "bbox": [[5.0, 5.0], [50.0, 5.0], [50.0, 15.0], [5.0, 15.0]],
                "area": 450.0,
                "geometry_valid": True,
                "pass": "bottom",
                "category": "tagline",
                "is_title": False,
                "is_residual": True,
                "is_significant": True,
                "significant_reason": "word_level",
                "proximity_discounted": False,
            },
        ],
        "title": {
            "text": "ALIEN ROMULUS",
            "bbox": [[10.0, 20.0], [110.0, 20.0], [110.0, 60.0], [10.0, 60.0]],
            "match_score": 1.0,
        },
        "decision": {
            "mode": "title_only",
            "accepted": False,
            "reason": "text_heavy",
            "has_title": True,
            "require_title": True,
            "significant_residual_count": 1,
            "max_residual_boxes": 0,
            "significant_area_fraction": 0.0003,
            "max_residual_area_fraction": 0.04,
        },
    }


def _archive_payload(
    *,
    image_path: Path,
    config_ocr: dict[str, object],
    with_ocr: bool = True,
    with_trace: bool = False,
    candidate_overrides: dict[str, object] | None = None,
) -> dict[str, object]:
    candidate: dict[str, object] = {
        "orig_filename": "clean.jpg",
        "image_path": str(image_path),
        "rank": None,
        "stage_reached": "ocr",
        "rejection_reason": "ocr_text_heavy",
    }
    if with_ocr:
        # Durable OCR read, as written by CandidateScore.to_dict() post-fix.
        candidate["ocr_detected_text"] = "ALIEN ROMULUS"
        candidate["ocr_title_bbox"] = [[10.0, 20.0], [110.0, 20.0], [110.0, 60.0], [10.0, 60.0]]
        candidate["ocr_residual_boxes"] = [
            {
                "text": "ONLY IN THEATERS",
                "confidence": 0.91,
                "bbox": [[5.0, 5.0], [50.0, 5.0], [50.0, 15.0], [5.0, 15.0]],
                "area": 450.0,
                "geometry_valid": True,
            }
        ]
    if with_trace:
        candidate["ocr_trace"] = _sample_trace()
    if candidate_overrides:
        candidate.update(candidate_overrides)
    return {
        "run_id": "run-dev-1",
        "movie_id": 1,
        "title": "Debug Movie",
        "tmdb_id": 42,
        "status": "completed",
        "config": {"ocr": config_ocr},
        "stage_timings_seconds": {"ocr": 1.25},
        "candidates": [candidate],
    }


async def _seed_run(
    db, tmp_path: Path, *, archive_payload: dict[str, object], output_dir: Path
) -> str:
    movie = Movie(title="Debug Movie", year=2024, folder_path="/m/debug", tmdb_id=42)
    db.add(movie)
    await db.commit()
    await db.refresh(movie)

    archive_payload["movie_id"] = movie.id
    archive_path = tmp_path / "run-dev-1.json"
    archive_path.write_text(json.dumps(archive_payload), encoding="utf-8")

    db.add(
        PipelineRun(
            run_id="run-dev-1",
            movie_id=movie.id,
            status="completed",
            archive_path=str(archive_path),
            output_dir=str(output_dir),
        )
    )
    await db.commit()
    return "run-dev-1"


@pytest.mark.asyncio
async def test_capture_false_rejection_uses_this_runs_log(client, db, tmp_path):
    output_dir = tmp_path / "runs" / "Debug Movie"
    originals_dir = output_dir / "0-originals"
    originals_dir.mkdir(parents=True)
    original_image = originals_dir / "clean.jpg"
    original_image.write_bytes(b"jpeg-data")
    rejected_image = output_dir / "2-ocr-rejected" / "ocr_text_heavy__clean.jpg"
    rejected_image.parent.mkdir(parents=True)
    rejected_image.write_bytes(b"stale-jpeg-data")
    # A pipeline.log that belongs to this run (carries its run_id stamp).
    (output_dir / "pipeline.log").write_text(
        "\n".join(
            [
                "2026-06-28 | INFO | marquee | RUN START | run_id=run-dev-1 | movie=Debug Movie",
                "OCR | file=clean.jpg | accepted=False | reason=ocr_text_heavy | text='ALIEN ROMULUS'",
                'FEATURES | file=clean.jpg | raw={"knn_sim": 0.9}',
                "OCR | file=other.jpg | accepted=True | reason=None",
            ]
        ),
        encoding="utf-8",
    )

    await _seed_run(
        db,
        tmp_path,
        archive_payload=_archive_payload(
            image_path=rejected_image, config_ocr=_full_ocr_snapshot()
        ),
        output_dir=output_dir,
    )

    resp = await client.post(
        "/api/dev/ocr-labels/false-rejection",
        json={"run_id": "run-dev-1", "orig_filename": "clean.jpg"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "captured"
    assert body["label_kind"] == "false_rejection"
    assert body["image_copied"] is True
    assert body["log_captured"] is True
    assert body["missing_artifacts"] == []

    capture_dir = Path(body["path"])
    assert (capture_dir / "poster.jpg").read_bytes() == b"jpeg-data"
    log_text = (capture_dir / "log.txt").read_text(encoding="utf-8")
    assert "file=clean.jpg" in log_text
    assert "file=other.jpg" not in log_text
    capture = json.loads((capture_dir / "capture.json").read_text(encoding="utf-8"))
    assert capture["label_kind"] == "false_rejection"
    assert capture["source_resolution"]["image_source_kind"] == "originals"
    assert capture["source_resolution"]["log_source_kind"] == "pipeline.log"
    # The durable OCR read is hoisted to a top-level block regardless of the log.
    assert capture["ocr"]["available"] is True
    assert capture["ocr"]["detected_text"] == "ALIEN ROMULUS"
    assert capture["ocr"]["residual_box_count"] == 1


@pytest.mark.asyncio
async def test_capture_synthesises_from_archive_when_log_missing(client, db, tmp_path):
    output_dir = tmp_path / "runs" / "Debug Movie"
    originals_dir = output_dir / "0-originals"
    originals_dir.mkdir(parents=True)
    (originals_dir / "clean.jpg").write_bytes(b"jpeg-data")
    rejected_image = output_dir / "2-ocr-rejected" / "ocr_text_heavy__clean.jpg"
    rejected_image.parent.mkdir(parents=True)
    rejected_image.write_bytes(b"stale-jpeg-data")
    # No pipeline.log at all — the durable archive must carry the OCR read.

    await _seed_run(
        db,
        tmp_path,
        archive_payload=_archive_payload(
            image_path=rejected_image, config_ocr=_full_ocr_snapshot()
        ),
        output_dir=output_dir,
    )

    resp = await client.post(
        "/api/dev/ocr-labels/false-rejection",
        json={"run_id": "run-dev-1", "orig_filename": "clean.jpg"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["log_captured"] is True
    assert body["missing_artifacts"] == []

    capture_dir = Path(body["path"])
    log_text = (capture_dir / "log.txt").read_text(encoding="utf-8")
    assert "reconstructed from the immutable run archive" in log_text
    assert "ALIEN ROMULUS" in log_text
    assert "residual_boxes=1" in log_text
    capture = json.loads((capture_dir / "capture.json").read_text(encoding="utf-8"))
    assert capture["source_resolution"]["log_source_kind"] == "archive"
    assert capture["ocr"]["detected_text"] == "ALIEN ROMULUS"


@pytest.mark.asyncio
async def test_capture_ignores_stale_pipeline_log_from_another_run(client, db, tmp_path):
    output_dir = tmp_path / "runs" / "Debug Movie"
    originals_dir = output_dir / "0-originals"
    originals_dir.mkdir(parents=True)
    (originals_dir / "clean.jpg").write_bytes(b"jpeg-data")
    rejected_image = output_dir / "2-ocr-rejected" / "ocr_text_heavy__clean.jpg"
    rejected_image.parent.mkdir(parents=True)
    rejected_image.write_bytes(b"stale-jpeg-data")
    # A pipeline.log left behind by a LATER run of the same movie (work dir is
    # keyed by title, not run_id). It must not be mined for this run's lines.
    (output_dir / "pipeline.log").write_text(
        "\n".join(
            [
                "2026-06-28 | INFO | marquee | RUN START | run_id=some-other-run | movie=Debug Movie",
                "OCR | file=clean.jpg | accepted=False | text='STALE-LOG-DATA'",
            ]
        ),
        encoding="utf-8",
    )

    await _seed_run(
        db,
        tmp_path,
        archive_payload=_archive_payload(
            image_path=rejected_image, config_ocr=_full_ocr_snapshot()
        ),
        output_dir=output_dir,
    )

    resp = await client.post(
        "/api/dev/ocr-labels/false-rejection",
        json={"run_id": "run-dev-1", "orig_filename": "clean.jpg"},
    )
    assert resp.status_code == 200
    capture_dir = Path(resp.json()["path"])
    log_text = (capture_dir / "log.txt").read_text(encoding="utf-8")
    assert "STALE-LOG-DATA" not in log_text
    assert "reconstructed from the immutable run archive" in log_text
    capture = json.loads((capture_dir / "capture.json").read_text(encoding="utf-8"))
    assert capture["source_resolution"]["log_source_kind"] == "archive"


@pytest.mark.asyncio
async def test_capture_false_acceptance_with_nan_features(client, db, tmp_path):
    """Regression: a ranked candidate's NaN extended-feature sentinels must not
    500 the route, and capture.json must be standards-compliant JSON."""
    output_dir = tmp_path / "runs" / "Debug Movie"
    originals_dir = output_dir / "0-originals"
    originals_dir.mkdir(parents=True)
    (originals_dir / "clean.jpg").write_bytes(b"jpeg-data")

    archive_payload = _archive_payload(
        image_path=output_dir / "ranked" / "clean.jpg",
        config_ocr=_full_ocr_snapshot(),
        candidate_overrides={
            "rank": 1,
            "rejection_reason": None,
            "stage_reached": "rank",
            "final_score": 0.87,
            # The title-geometry NaN sentinels emitted when no title box exists.
            "extended_features": {
                "title_height_frac": float("nan"),
                "darkness": 0.42,
            },
        },
    )
    # json.dumps defaults to allow_nan=True, so the archive file mirrors a real
    # run archive (NaN tokens); load_archive reads them back as float('nan').
    await _seed_run(db, tmp_path, archive_payload=archive_payload, output_dir=output_dir)

    resp = await client.post(
        "/api/dev/ocr-labels/false-acceptance",
        json={"run_id": "run-dev-1", "orig_filename": "clean.jpg"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["label_kind"] == "false_acceptance"
    # NaN was sanitized in the response payload too.
    extended = body["metadata"]["candidate"]["extended_features"]
    assert extended["title_height_frac"] is None
    assert extended["darkness"] == 0.42

    capture_text = (Path(body["path"]) / "capture.json").read_text(encoding="utf-8")
    assert "NaN" not in capture_text  # standards-compliant JSON
    capture = json.loads(capture_text)
    assert capture["candidate"]["extended_features"]["title_height_frac"] is None


@pytest.mark.asyncio
async def test_capture_rejects_stale_snapshot(client, db, tmp_path):
    output_dir = tmp_path / "runs" / "Debug Movie"
    output_dir.mkdir(parents=True)
    await _seed_run(
        db,
        tmp_path,
        archive_payload=_archive_payload(
            image_path=output_dir / "missing.jpg", config_ocr={"device": "cpu"}
        ),
        output_dir=output_dir,
    )

    resp = await client.post(
        "/api/dev/ocr-labels/false-rejection",
        json={"run_id": "run-dev-1", "orig_filename": "clean.jpg"},
    )
    assert resp.status_code == 409
    assert "predates the OCR snapshot upgrade" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_capture_flags_missing_image_and_ocr_diagnostics(client, db, tmp_path):
    output_dir = tmp_path / "runs" / "Debug Movie"
    output_dir.mkdir(parents=True)
    # Pre-fix archive: full OCR config snapshot, but no durable OCR read fields.
    await _seed_run(
        db,
        tmp_path,
        archive_payload=_archive_payload(
            image_path=output_dir / "2-ocr-rejected" / "missing.jpg",
            config_ocr=_full_ocr_snapshot(),
            with_ocr=False,
        ),
        output_dir=output_dir,
    )

    resp = await client.post(
        "/api/dev/ocr-labels/false-acceptance",
        json={"run_id": "run-dev-1", "orig_filename": "clean.jpg"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["image_copied"] is False
    # The archive still lets us synthesize a log, but it lacks the OCR read.
    assert body["log_captured"] is True
    assert set(body["missing_artifacts"]) == {"image", "ocr_diagnostics"}

    capture_dir = Path(body["path"])
    capture = json.loads((capture_dir / "capture.json").read_text(encoding="utf-8"))
    assert set(capture["missing_artifacts"]) == {"image", "ocr_diagnostics"}
    assert capture["ocr"]["available"] is False


@pytest.mark.asyncio
async def test_capture_includes_full_ocr_trace(client, db, tmp_path):
    """The full per-box OCR trace is hoisted into capture.json and rendered in
    log.txt — every detected box, its classification + significance, and the
    accept/reject decision (what an LLM analyzes to tune the gate)."""
    output_dir = tmp_path / "runs" / "Debug Movie"
    originals_dir = output_dir / "0-originals"
    originals_dir.mkdir(parents=True)
    (originals_dir / "clean.jpg").write_bytes(b"jpeg-data")
    rejected_image = output_dir / "2-ocr-rejected" / "ocr_text_heavy__clean.jpg"
    rejected_image.parent.mkdir(parents=True)
    rejected_image.write_bytes(b"stale-jpeg-data")

    await _seed_run(
        db,
        tmp_path,
        archive_payload=_archive_payload(
            image_path=rejected_image, config_ocr=_full_ocr_snapshot(), with_trace=True
        ),
        output_dir=output_dir,
    )

    resp = await client.post(
        "/api/dev/ocr-labels/false-rejection",
        json={"run_id": "run-dev-1", "orig_filename": "clean.jpg"},
    )
    assert resp.status_code == 200
    capture_dir = Path(resp.json()["path"])

    capture = json.loads((capture_dir / "capture.json").read_text(encoding="utf-8"))
    trace = capture["ocr"]["trace"]
    assert trace is not None
    assert len(trace["detected_boxes"]) == 2
    assert trace["decision"]["reason"] == "text_heavy"
    assert trace["passes_run"] == ["full", "top", "bottom"]

    log_text = (capture_dir / "log.txt").read_text(encoding="utf-8")
    assert "OCR trace (every detected box" in log_text
    assert "ONLY IN THEATERS" in log_text
    assert "significant(word_level)" in log_text
    assert "decision: mode=title_only" in log_text
    assert "[bottom]" in log_text


@pytest.mark.asyncio
async def test_capture_flags_stale_null_ocr_read(client, db, tmp_path):
    """Regression for the false-clean case: a candidate that reached OCR but
    carries a *null* read (key present, value None — pre-batch-fix archives)
    must report available=False and flag ocr_diagnostics missing."""
    output_dir = tmp_path / "runs" / "Debug Movie"
    originals_dir = output_dir / "0-originals"
    originals_dir.mkdir(parents=True)
    (originals_dir / "clean.jpg").write_bytes(b"jpeg-data")
    rejected_image = output_dir / "2-ocr-rejected" / "ocr_text_heavy__clean.jpg"
    rejected_image.parent.mkdir(parents=True)
    rejected_image.write_bytes(b"stale-jpeg-data")

    await _seed_run(
        db,
        tmp_path,
        archive_payload=_archive_payload(
            image_path=rejected_image,
            config_ocr=_full_ocr_snapshot(),
            with_ocr=False,
            # Keys present but null — exactly what the pre-fix batch engine wrote.
            candidate_overrides={
                "ocr_detected_text": None,
                "ocr_title_bbox": None,
                "ocr_residual_boxes": None,
                "ocr_trace": None,
                "stage_reached": "ocr",
            },
        ),
        output_dir=output_dir,
    )

    resp = await client.post(
        "/api/dev/ocr-labels/false-rejection",
        json={"run_id": "run-dev-1", "orig_filename": "clean.jpg"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "ocr_diagnostics" in body["missing_artifacts"]
    capture = json.loads((Path(body["path"]) / "capture.json").read_text(encoding="utf-8"))
    assert capture["ocr"]["available"] is False
    assert "re-run the movie" in (Path(body["path"]) / "log.txt").read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_capture_does_not_flag_pre_ocr_reject(client, db, tmp_path):
    """A candidate rejected *before* OCR (e.g. resolution/style gate) has no OCR
    read legitimately — it must NOT be flagged as missing ocr_diagnostics."""
    output_dir = tmp_path / "runs" / "Debug Movie"
    originals_dir = output_dir / "0-originals"
    originals_dir.mkdir(parents=True)
    (originals_dir / "clean.jpg").write_bytes(b"jpeg-data")

    await _seed_run(
        db,
        tmp_path,
        archive_payload=_archive_payload(
            image_path=originals_dir / "clean.jpg",
            config_ocr=_full_ocr_snapshot(),
            with_ocr=False,
            candidate_overrides={"stage_reached": "resolution", "rejection_reason": "resolution"},
        ),
        output_dir=output_dir,
    )

    resp = await client.post(
        "/api/dev/ocr-labels/false-rejection",
        json={"run_id": "run-dev-1", "orig_filename": "clean.jpg"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "ocr_diagnostics" not in body["missing_artifacts"]


@pytest.mark.asyncio
async def test_list_run_labels_returns_persisted_filenames(client, db, tmp_path):
    output_dir = tmp_path / "runs" / "Debug Movie"
    originals_dir = output_dir / "0-originals"
    originals_dir.mkdir(parents=True)
    (originals_dir / "clean.jpg").write_bytes(b"jpeg-data")
    rejected_image = output_dir / "2-ocr-rejected" / "ocr_text_heavy__clean.jpg"
    rejected_image.parent.mkdir(parents=True)
    rejected_image.write_bytes(b"stale-jpeg-data")

    await _seed_run(
        db,
        tmp_path,
        archive_payload=_archive_payload(
            image_path=rejected_image, config_ocr=_full_ocr_snapshot()
        ),
        output_dir=output_dir,
    )
    capture_resp = await client.post(
        "/api/dev/ocr-labels/false-rejection",
        json={"run_id": "run-dev-1", "orig_filename": "clean.jpg"},
    )
    assert capture_resp.status_code == 200

    list_resp = await client.get("/api/dev/ocr-labels/run/run-dev-1")
    assert list_resp.status_code == 200
    assert list_resp.json() == {
        "run_id": "run-dev-1",
        "labels": {
            "false_rejection": ["clean.jpg"],
            "false_acceptance": [],
        },
    }


@pytest.mark.asyncio
async def test_clear_ocr_labels(client, db, tmp_path):
    root = ocr_label_capture.capture_root()
    first_capture = root / "Debug_Movie__run-a" / "false_rejection" / "clean__12345678"
    first_capture.mkdir(parents=True, exist_ok=True)
    (first_capture / "capture.json").write_text("{}", encoding="utf-8")

    resp = await client.post("/api/dev/ocr-labels/clear")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "cleared"
    assert body["deleted_run_dirs"] >= 1
    assert body["deleted_capture_dirs"] >= 1
    assert Path(body["root_path"]).exists()
    assert list(Path(body["root_path"]).iterdir()) == []


@pytest.mark.asyncio
async def test_dev_routes_absent_in_production(monkeypatch):
    import marquee.main as main_module

    monkeypatch.setattr(settings, "DEBUG", False)
    prod_module = importlib.reload(main_module)
    transport = ASGITransport(app=prod_module.app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/dev/ocr-labels/false-rejection",
                json={"run_id": "missing", "orig_filename": "missing.jpg"},
            )
        assert resp.status_code == 404
    finally:
        monkeypatch.setattr(settings, "DEBUG", True)
        importlib.reload(main_module)
