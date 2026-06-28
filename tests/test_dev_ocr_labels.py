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
    }


def _archive_payload(*, image_path: Path, config_ocr: dict[str, object]) -> dict[str, object]:
    return {
        "run_id": "run-dev-1",
        "movie_id": 1,
        "title": "Debug Movie",
        "tmdb_id": 42,
        "status": "completed",
        "config": {"ocr": config_ocr},
        "stage_timings_seconds": {"ocr": 1.25},
        "candidates": [
            {
                "orig_filename": "clean.jpg",
                "image_path": str(image_path),
                "rank": None,
                "stage_reached": "ocr",
                "rejection_reason": "ocr_text_heavy",
            }
        ],
    }


async def _seed_run(db, tmp_path: Path, *, archive_payload: dict[str, object], output_dir: Path) -> str:
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
async def test_capture_false_positive_success(client, db, tmp_path):
    output_dir = tmp_path / "runs" / "Debug Movie"
    originals_dir = output_dir / "0-originals"
    originals_dir.mkdir(parents=True)
    original_image = originals_dir / "clean.jpg"
    original_image.write_bytes(b"jpeg-data")
    rejected_image = output_dir / "2-ocr-rejected" / "ocr_text_heavy__clean.jpg"
    rejected_image.parent.mkdir(parents=True)
    rejected_image.write_bytes(b"stale-jpeg-data")
    (output_dir / "pipeline.log").write_text(
        "\n".join(
            [
                "OCR | file=clean.jpg | accepted=False | reason=ocr_text_heavy",
                "FEATURES | file=clean.jpg | raw={\"knn_sim\": 0.9}",
                "OCR | file=other.jpg | accepted=True | reason=None",
            ]
        ),
        encoding="utf-8",
    )

    await _seed_run(
        db,
        tmp_path,
        archive_payload=_archive_payload(image_path=rejected_image, config_ocr=_full_ocr_snapshot()),
        output_dir=output_dir,
    )

    resp = await client.post(
        "/api/dev/ocr-labels/false-positive",
        json={"run_id": "run-dev-1", "orig_filename": "clean.jpg"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "captured"
    assert body["image_copied"] is True
    assert body["log_captured"] is True
    assert body["missing_artifacts"] == []

    capture_dir = Path(body["path"])
    assert (capture_dir / "poster.jpg").read_bytes() == b"jpeg-data"
    log_text = (capture_dir / "log.txt").read_text(encoding="utf-8")
    assert "file=clean.jpg" in log_text
    assert "file=other.jpg" not in log_text
    capture = json.loads((capture_dir / "capture.json").read_text(encoding="utf-8"))
    assert capture["label_kind"] == "false_positive"
    assert capture["source_resolution"]["image_source_kind"] == "originals"


@pytest.mark.asyncio
async def test_capture_falls_back_to_pipeline_run_json_when_pipeline_log_is_missing(client, db, tmp_path):
    output_dir = tmp_path / "runs" / "Debug Movie"
    originals_dir = output_dir / "0-originals"
    originals_dir.mkdir(parents=True)
    original_image = originals_dir / "clean.jpg"
    original_image.write_bytes(b"jpeg-data")
    rejected_image = output_dir / "2-ocr-rejected" / "ocr_text_heavy__clean.jpg"
    rejected_image.parent.mkdir(parents=True)
    rejected_image.write_bytes(b"stale-jpeg-data")
    archive_payload = _archive_payload(image_path=rejected_image, config_ocr=_full_ocr_snapshot())
    (output_dir / "pipeline_run.json").write_text(json.dumps(archive_payload), encoding="utf-8")

    await _seed_run(
        db,
        tmp_path,
        archive_payload=archive_payload,
        output_dir=output_dir,
    )

    resp = await client.post(
        "/api/dev/ocr-labels/false-positive",
        json={"run_id": "run-dev-1", "orig_filename": "clean.jpg"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["log_captured"] is True
    assert body["missing_artifacts"] == []
    assert "synthesized from pipeline_run.json" in (
        Path(body["path"]) / "log.txt"
    ).read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_capture_rejects_stale_snapshot(client, db, tmp_path):
    output_dir = tmp_path / "runs" / "Debug Movie"
    output_dir.mkdir(parents=True)
    await _seed_run(
        db,
        tmp_path,
        archive_payload=_archive_payload(image_path=output_dir / "missing.jpg", config_ocr={"device": "cpu"}),
        output_dir=output_dir,
    )

    resp = await client.post(
        "/api/dev/ocr-labels/false-positive",
        json={"run_id": "run-dev-1", "orig_filename": "clean.jpg"},
    )
    assert resp.status_code == 409
    assert "predates the OCR snapshot upgrade" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_capture_succeeds_when_image_and_log_are_missing(client, db, tmp_path):
    output_dir = tmp_path / "runs" / "Debug Movie"
    output_dir.mkdir(parents=True)
    await _seed_run(
        db,
        tmp_path,
        archive_payload=_archive_payload(
            image_path=output_dir / "2-ocr-rejected" / "missing.jpg",
            config_ocr=_full_ocr_snapshot(),
        ),
        output_dir=output_dir,
    )

    resp = await client.post(
        "/api/dev/ocr-labels/false-negative",
        json={"run_id": "run-dev-1", "orig_filename": "clean.jpg"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["image_copied"] is False
    assert body["log_captured"] is False
    assert set(body["missing_artifacts"]) == {"image", "log"}

    capture_dir = Path(body["path"])
    capture = json.loads((capture_dir / "capture.json").read_text(encoding="utf-8"))
    assert capture["missing_artifacts"] == ["image", "log"]


@pytest.mark.asyncio
async def test_list_run_labels_returns_persisted_filenames(client, db, tmp_path):
    output_dir = tmp_path / "runs" / "Debug Movie"
    originals_dir = output_dir / "0-originals"
    originals_dir.mkdir(parents=True)
    original_image = originals_dir / "clean.jpg"
    original_image.write_bytes(b"jpeg-data")
    rejected_image = output_dir / "2-ocr-rejected" / "ocr_text_heavy__clean.jpg"
    rejected_image.parent.mkdir(parents=True)
    rejected_image.write_bytes(b"stale-jpeg-data")
    (output_dir / "pipeline.log").write_text(
        "OCR | file=clean.jpg | accepted=False | reason=ocr_text_heavy\n",
        encoding="utf-8",
    )

    await _seed_run(
        db,
        tmp_path,
        archive_payload=_archive_payload(image_path=rejected_image, config_ocr=_full_ocr_snapshot()),
        output_dir=output_dir,
    )
    capture_resp = await client.post(
        "/api/dev/ocr-labels/false-positive",
        json={"run_id": "run-dev-1", "orig_filename": "clean.jpg"},
    )
    assert capture_resp.status_code == 200

    list_resp = await client.get("/api/dev/ocr-labels/run/run-dev-1")
    assert list_resp.status_code == 200
    assert list_resp.json() == {
        "run_id": "run-dev-1",
        "labels": {
            "false_positive": ["clean.jpg"],
            "false_negative": [],
        },
    }


@pytest.mark.asyncio
async def test_clear_ocr_labels(client, db, tmp_path):
    root = ocr_label_capture.capture_root()
    first_capture = root / "Debug_Movie__run-a" / "false_positive" / "clean__12345678"
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
                "/api/dev/ocr-labels/false-positive",
                json={"run_id": "missing", "orig_filename": "missing.jpg"},
            )
        assert resp.status_code == 404
    finally:
        monkeypatch.setattr(settings, "DEBUG", True)
        importlib.reload(main_module)
