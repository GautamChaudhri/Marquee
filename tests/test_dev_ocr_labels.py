from __future__ import annotations

import json
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

import marquee.pipeline.ocr_label_capture as ocr_label_capture
from marquee.config import settings
from marquee.main import app
from marquee.models import Job, JobArtifact, Movie, PipelineRun, Season, Series
from marquee.models.job import JobAttempt


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture(autouse=True)
def isolated_storage(tmp_path, monkeypatch):
    capture = tmp_path / "ocr-labels"
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path)
    monkeypatch.setattr(ocr_label_capture, "capture_root", lambda: _capture_root(capture))
    yield


def _capture_root(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def _ocr_snapshot() -> dict[str, object]:
    return dict.fromkeys(ocr_label_capture.REQUIRED_OCR_SNAPSHOT_KEYS, 0)


async def _seed_run(
    db,
    *,
    snapshot: dict[str, object] | None = None,
    valid_candidate_owner: bool = True,
    media_type: str = "movie",
    candidate_role: str = "rejected_candidate",
) -> str:
    movie = None
    series = None
    season = None
    if media_type == "movie":
        movie = Movie(title="Debug Movie", year=2024, folder_path="/m/debug", tmdb_id=42)
        db.add(movie)
    else:
        series = Series(title="Debug Show", year=2024, series_path="/tv/debug", tmdb_id=84)
        db.add(series)
    await db.flush()

    if media_type == "season":
        assert series is not None
        season = Season(series_id=series.id, season_number=1)
        db.add(season)
        await db.flush()

    if media_type == "movie":
        assert movie is not None
        title = movie.title
        subject_id = movie.id
        subject = {"movie_id": movie.id, "title": movie.title}
    elif media_type == "series":
        assert series is not None
        title = series.title
        subject_id = series.id
        subject = {"series_id": series.id, "title": series.title}
    elif media_type == "season":
        assert series is not None and season is not None
        title = f"{series.title} - Season {season.season_number:02d}"
        subject_id = season.id
        subject = {
            "series_id": series.id,
            "series_title": series.title,
            "season_id": season.id,
            "season_number": season.season_number,
            "title": title,
        }
    else:
        raise ValueError(f"Unsupported test media type: {media_type}")

    run_id = "run-dev-1"
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
        subject_kind=media_type,
        subject_reference=str(subject_id),
        subject_snapshot={"version": 1, "kind": media_type, **subject},
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

    image = b"canonical-candidate-image"
    image_key = f"test-artifacts/{job_id}/clean.jpg"
    image_path = Path(settings.DATA_DIR) / image_key
    image_path.parent.mkdir(parents=True, exist_ok=True)
    image_path.write_bytes(image)
    candidate_artifact = JobArtifact(
        job_id=job_id,
        attempt_id=attempt.id,
        kind="evidence_image",
        name="clean.jpg",
        status="available",
        storage_key=image_key,
        content_type="image/jpeg",
        size_bytes=len(image),
        checksum=sha256(image).hexdigest(),
        artifact_metadata={
            "family": "poster_pipeline" if valid_candidate_owner else "unrelated_evidence",
            "role": candidate_role,
            "run_id": run_id,
            "candidate_reference": "clean.jpg",
        },
    )
    db.add(candidate_artifact)
    await db.flush()

    archive = {
        "run_id": run_id,
        "media_type": media_type,
        "movie_id": movie.id if movie is not None else None,
        "subject": subject,
        "title": title,
        "tmdb_id": movie.tmdb_id
        if movie is not None
        else series.tmdb_id
        if series is not None
        else None,
        "status": "completed",
        "config": {"ocr": _ocr_snapshot() if snapshot is None else snapshot},
        "stage_timings_seconds": {"ocr": 1.25},
        "candidates": [
            {
                "orig_filename": "clean.jpg",
                "artifact_id": candidate_artifact.id,
                "rank": None,
                "stage_reached": "ocr",
                "rejection_reason": "ocr_text_heavy",
                "ocr_detected_text": "DEBUG MOVIE",
                "ocr_title_bbox": [[10, 20], [110, 20], [110, 60], [10, 60]],
                "ocr_residual_boxes": [{"text": "EXTRA", "confidence": 0.9}],
            }
        ],
    }
    image_identity = {
        "reference": "clean.jpg",
        "artifact_id": candidate_artifact.id,
        "artifact_storage_key": candidate_artifact.storage_key,
        "artifact_checksum": candidate_artifact.checksum,
    }
    if candidate_role == "rejected_candidate":
        archive["review_evidence"] = {
            "candidates": [{**image_identity, "objective_eligible": False}]
        }
    else:
        archive["review"] = {"survivors": [{**image_identity, "objective_eligible": True}]}
    archive_bytes = json.dumps(archive, allow_nan=False).encode()
    archive_key = f"test-artifacts/{job_id}/pipeline-run.json"
    archive_path = Path(settings.DATA_DIR) / archive_key
    archive_path.write_bytes(archive_bytes)
    archive_artifact = JobArtifact(
        job_id=job_id,
        attempt_id=attempt.id,
        kind="command_report",
        name="pipeline-run.json",
        status="available",
        storage_key=archive_key,
        content_type="application/json",
        size_bytes=len(archive_bytes),
        checksum=sha256(archive_bytes).hexdigest(),
        artifact_metadata={"family": "poster_pipeline", "run_id": run_id},
    )
    db.add(archive_artifact)
    await db.flush()
    db.add(
        PipelineRun(
            run_id=run_id,
            movie_id=movie.id if movie is not None else None,
            media_type=media_type,
            series_id=series.id if series is not None else None,
            season_id=season.id if season is not None else None,
            status="completed",
            job_id=job_id,
            attempt_id=attempt.id,
            fence_token=attempt.fence_token,
            selected_artifact_id=candidate_artifact.id,
            archive_artifact_id=archive_artifact.id,
        )
    )
    await db.commit()
    return run_id


@pytest.mark.asyncio
async def test_capture_reads_canonical_archive_and_candidate_artifact(client, db):
    run_id = await _seed_run(db)
    response = await client.post(
        "/api/dev/ocr-labels/false-rejection",
        json={"run_id": run_id, "orig_filename": "clean.jpg"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["image_copied"] is True
    assert body["log_captured"] is True
    assert body["missing_artifacts"] == []
    capture_dir = Path(body["path"])
    assert capture_dir.parents[2].name == "movie-posters"
    assert (capture_dir / "poster.jpg").read_bytes() == b"canonical-candidate-image"
    assert json.loads((capture_dir / "ocr.json").read_text())["detected_text"] == "DEBUG MOVIE"
    assert json.loads((capture_dir / "pipeline-run.json").read_text())["run_id"] == run_id
    capture = json.loads((capture_dir / "capture.json").read_text())
    assert capture["subject"]["media_type"] == "movie"
    assert capture["source_resolution"]["image_source_kind"] == "job_artifact"
    assert capture["source_resolution"]["log_source_kind"] == "archive"
    assert capture["ocr"]["detected_text"] == "DEBUG MOVIE"
    assert capture["run"]["archive_artifact_id"] is not None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("media_type", "capture_folder"),
    [
        ("movie", "movie-posters"),
        ("series", "show-posters"),
        ("season", "season-posters"),
    ],
)
async def test_capture_keeps_each_poster_kind_in_its_own_analysis_folder(
    client, db, media_type, capture_folder
):
    run_id = await _seed_run(db, media_type=media_type, candidate_role="review_candidate")

    response = await client.post(
        "/api/dev/ocr-labels/false-acceptance",
        json={"run_id": run_id, "orig_filename": "clean.jpg"},
    )

    assert response.status_code == 200
    capture_dir = Path(response.json()["path"])
    assert capture_dir.parents[2].name == capture_folder
    assert {
        "poster.jpg",
        "log.txt",
        "ocr.json",
        "pipeline-run.json",
        "capture.json",
    } <= {path.name for path in capture_dir.iterdir()}
    capture = json.loads((capture_dir / "capture.json").read_text())
    assert capture["subject"]["media_type"] == media_type
    assert json.loads((capture_dir / "pipeline-run.json").read_text())["media_type"] == media_type


@pytest.mark.asyncio
async def test_capture_rejects_archive_without_complete_ocr_snapshot(client, db):
    run_id = await _seed_run(db, snapshot={})
    response = await client.post(
        "/api/dev/ocr-labels/false-rejection",
        json={"run_id": run_id, "orig_filename": "clean.jpg"},
    )
    assert response.status_code == 409
    assert "re-run it first" in response.json()["detail"]


@pytest.mark.asyncio
async def test_capture_does_not_read_unowned_candidate_artifact(client, db):
    run_id = await _seed_run(db, valid_candidate_owner=False, candidate_role="review_candidate")
    response = await client.post(
        "/api/dev/ocr-labels/false-acceptance",
        json={"run_id": run_id, "orig_filename": "clean.jpg"},
    )
    assert response.status_code == 200
    assert response.json()["image_copied"] is False
    assert response.json()["missing_artifacts"] == ["image"]


@pytest.mark.asyncio
async def test_capture_unknown_candidate_is_404(client, db):
    run_id = await _seed_run(db)
    response = await client.post(
        "/api/dev/ocr-labels/false-rejection",
        json={"run_id": run_id, "orig_filename": "missing.jpg"},
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_list_and_clear_canonical_captures(client, db):
    run_id = await _seed_run(db)
    captured = await client.post(
        "/api/dev/ocr-labels/false-rejection",
        json={"run_id": run_id, "orig_filename": "clean.jpg"},
    )
    assert captured.status_code == 200

    listed = await client.get(f"/api/dev/ocr-labels/run/{run_id}")
    assert listed.json()["labels"]["false_rejection"] == ["clean.jpg"]

    cleared = await client.post("/api/dev/ocr-labels/clear")
    assert cleared.status_code == 200
    assert cleared.json()["deleted_capture_dirs"] == 1


def test_replay_capture_reclassifies_orphaned_multi_pass_season_word(tmp_path: Path):
    capture_dir = tmp_path / "season-posters" / "30-rock" / "false_rejection" / "candidate"
    capture_dir.mkdir(parents=True)

    def region(text: str, bbox: list[list[float]], category: str) -> dict[str, object]:
        return {
            "text": text,
            "confidence": 0.96,
            "bbox": bbox,
            "geometry_valid": True,
            "category": category,
            "is_significant": True,
        }

    payload = {
        "label_kind": "false_rejection",
        "subject": {"media_type": "season", "season_number": 5},
        "ocr": {
            "trace": {
                "image_size": {"width": 500, "height": 750},
                "season_context": {"number": 5, "title": None},
                "decision": {
                    "mode": "custom",
                    "allow_map": {"season": True, "billing": False, "other": False},
                    "max_residual_boxes": 0,
                    "max_residual_area_fraction": 0.04,
                },
                "detected_boxes": [
                    region(
                        "SEASON FIVE",
                        [[155, 680], [355, 680], [355, 740], [155, 740]],
                        "season",
                    ),
                    region(
                        "SEASON",
                        [[160, 680], [275, 680], [275, 712], [160, 712]],
                        "other",
                    ),
                    region(
                        "FIVE",
                        [[278, 726], [350, 726], [350, 750], [278, 750]],
                        "billing",
                    ),
                ],
            }
        },
    }
    (capture_dir / "capture.json").write_text(json.dumps(payload), encoding="utf-8")

    replay = ocr_label_capture.replay_ocr_label_capture(capture_dir)

    assert replay["replayed_acceptance"] is True
    assert replay["remaining_denied_count"] == 0
    assert {change["text"] for change in replay["changed_regions"]} == {"SEASON", "FIVE"}
    corpus = ocr_label_capture.replay_ocr_label_corpus(tmp_path)
    assert corpus["errors"] == []
    assert len(corpus["captures"]) == 1
