from __future__ import annotations

import json
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

from marquee.config import settings
from marquee.models import Job, JobArtifact, PipelineRun
from marquee.models.job import JobAttempt


async def seed_canonical_pipeline_run(
    db,
    *,
    run_id: str,
    archive: dict,
    movie_id: int | None = None,
    series_id: int | None = None,
    season_id: int | None = None,
    media_type: str = "movie",
    status: str = "completed",
    scorer_name: str | None = "weighted",
    auto_pick_filename: str | None = None,
    feedback_event_id: str | None = None,
) -> PipelineRun:
    """Seed the complete immutable product projection used by API tests."""
    job_id = uuid4().hex
    now = datetime.now(UTC)
    subject_id = movie_id if media_type == "movie" else season_id or series_id
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
        subject_snapshot={"version": 1, "kind": media_type, "id": subject_id},
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

    selected = None
    for index, candidate in enumerate(archive.get("candidates", [])):
        filename = str(candidate.get("orig_filename") or f"candidate-{index}.jpg")
        content = f"canonical-candidate:{run_id}:{filename}".encode()
        key = f"test-artifacts/{job_id}/candidate-{index}.jpg"
        path = Path(settings.DATA_DIR) / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        artifact = JobArtifact(
            job_id=job_id,
            attempt_id=attempt.id,
            kind="evidence_image",
            name=filename,
            status="available",
            storage_key=key,
            content_type="image/jpeg",
            size_bytes=len(content),
            checksum=sha256(content).hexdigest(),
            artifact_metadata={
                "family": "poster_pipeline_candidate",
                "run_id": run_id,
                "orig_filename": filename,
            },
        )
        db.add(artifact)
        await db.flush()
        candidate["artifact_id"] = artifact.id
        candidate["artifact_storage_key"] = key
        candidate["artifact_checksum"] = artifact.checksum
        if candidate.get("rank") == 1 or filename == auto_pick_filename:
            selected = artifact

    payload = json.dumps(archive, allow_nan=False).encode()
    archive_key = f"test-artifacts/{job_id}/pipeline-run.json"
    archive_path = Path(settings.DATA_DIR) / archive_key
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    archive_path.write_bytes(payload)
    archive_artifact = JobArtifact(
        job_id=job_id,
        attempt_id=attempt.id,
        kind="command_report",
        name="pipeline-run.json",
        status="available",
        storage_key=archive_key,
        content_type="application/json",
        size_bytes=len(payload),
        checksum=sha256(payload).hexdigest(),
        artifact_metadata={"family": "poster_pipeline", "run_id": run_id},
    )
    db.add(archive_artifact)
    await db.flush()

    run = PipelineRun(
        run_id=run_id,
        movie_id=movie_id,
        series_id=series_id,
        season_id=season_id,
        media_type=media_type,
        subject_snapshot={"version": 1, "kind": media_type, "id": subject_id},
        status=status,
        scorer_name=scorer_name,
        job_id=job_id,
        attempt_id=attempt.id,
        fence_token=attempt.fence_token,
        selected_artifact_id=selected.id if selected is not None else None,
        archive_artifact_id=archive_artifact.id,
        auto_pick_filename=auto_pick_filename,
        feedback_event_id=feedback_event_id,
    )
    db.add(run)
    await db.flush()
    return run
