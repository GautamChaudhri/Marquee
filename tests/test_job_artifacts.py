"""Job artifacts: confined physical evidence and deterministic virtual documents.

An artifact is owned by the attempt that produced it. Covers path confinement and
immutability, registering existing product evidence without copying, deterministic and
redacted virtual artifacts, retention and reconciliation ordering, and rejection of
unclassified paths and unsafe names."""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, update

from marquee.config import settings
from marquee.core.jobs.artifact_service import (
    ArtifactError,
    artifact_boundary,
    expire_artifacts,
    expire_logs,
    materialize_virtual_artifact,
    reconcile_artifacts,
    register_existing_physical_artifact,
    register_physical_artifact,
    register_validation_result_artifact,
    register_virtual_artifact,
)
from marquee.main import app
from marquee.models import Job, JobArtifact, JobAttempt, JobEvent, JobLog


async def _attempt(db, label: str):
    job_id = uuid.uuid5(uuid.NAMESPACE_URL, f"artifact:{label}").hex
    job = Job(
        id=job_id,
        type="system_noop",
        payload_version=1,
        request={"echo": "safe", "api_key": "must-not-escape"},
        result={"version": 1, "outcome": "succeeded", "summary": {"value": label}},
        phase="terminal",
        outcome="succeeded",
        terminal_at=datetime.now(UTC),
        root_id=job_id,
        subject_kind="system_work",
        subject_reference="system_noop",
        subject_snapshot={
            "version": 1,
            "kind": "system_work",
            "display_id": "system:noop",
            "display_name": "System no-op",
            "work": "system_noop",
        },
        fence_token=1,
    )
    db.add(job)
    await db.flush()
    attempt = JobAttempt(
        job_id=job.id,
        number=1,
        fence_token=1,
        phase="finished",
        outcome="succeeded",
        finished_at=datetime.now(UTC),
    )
    db.add(attempt)
    await db.flush()
    job.current_attempt_id = attempt.id
    await db.commit()
    return job, attempt


def _source(key: str, content: bytes):
    boundary = artifact_boundary(settings.DATA_DIR)
    directory = boundary.from_key("data", "artifact-sources")
    boundary.create_directory(directory, parents=True)
    source = boundary.from_key("data", f"artifact-sources/{key}")
    fd = boundary.create_file(source)
    try:
        os.write(fd, content)
        os.fsync(fd)
    finally:
        os.close(fd)
    return boundary.classify(
        settings.data_dir_path / "artifact-sources" / key,
        roots=("data",),
        require_file=True,
    )


async def test_physical_artifact_is_confined_immutable_and_downloadable(db) -> None:
    job, attempt = await _attempt(db, "physical")
    job_id = job.id
    attempt_id = attempt.id
    content = b'{"status":"ok"}\n'
    row = await register_physical_artifact(
        job_id=job_id,
        attempt_id=attempt_id,
        fence_token=1,
        source=_source("report.json", content),
        kind="validation_report",
        name="Validation report.json",
        content_type="application/json",
        data_dir=settings.DATA_DIR,
    )
    assert row.status == "available"
    assert row.size_bytes == len(content)
    assert row.checksum == hashlib.sha256(content).hexdigest()
    assert row.storage_key and row.storage_key.startswith("jobs/evidence/artifacts/")
    assert not row.storage_key.startswith("/") and ".." not in row.storage_key
    stored = settings.data_dir_path / row.storage_key
    assert stored.read_bytes() == content
    assert stored.stat().st_mode & 0o777 == 0o400

    db.expire_all()
    event = (
        await db.scalars(
            select(JobEvent).where(
                JobEvent.job_id == job_id, JobEvent.event_key == "artifact.available"
            )
        )
    ).one()
    assert event.attempt_id == attempt_id

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        listing = await client.get(f"/api/jobs/{job_id}/artifacts")
        item = listing.json()["items"][0]
        assert item["available"] is True
        assert item["virtual"] is False
        assert "storage_key" not in item
        download = await client.get(item["download_url"])
        assert download.status_code == 200
        assert download.content == content
        assert download.headers["x-content-type-options"] == "nosniff"
        stored.chmod(0o600)
        stored.write_bytes(b"corrupt")
        assert (await client.get(item["download_url"])).status_code == 409
        stored.unlink()
        assert (await client.get(item["download_url"])).status_code == 404
        assert (
            await client.get(f"/api/jobs/{'0' * 32}/artifacts/{row.id}/download")
        ).status_code == 404


async def test_existing_product_and_validation_evidence_are_registered_without_copy(db) -> None:
    job, attempt = await _attempt(db, "product-evidence")
    source = _source("original-poster.jpg", b"\xff\xd8\xff\xe0original-poster-bytes")
    payload = (settings.data_dir_path / source.key.value).read_bytes()
    checksum = hashlib.sha256(payload).hexdigest()
    row = await register_existing_physical_artifact(
        job_id=job.id,
        attempt_id=attempt.id,
        fence_token=1,
        source=source,
        kind="product_backup",
        name="Recoverable product backup",
        checksum=checksum,
        size_bytes=len(payload),
    )
    assert row.storage_key == source.key.value
    assert row.artifact_metadata["subject_reference"] == "system_noop"

    job.result = {
        "version": 1,
        "outcome": "succeeded",
        "validation": {"verdict": "passed"},
    }
    await db.commit()
    validation = await register_validation_result_artifact(
        job_id=job.id, attempt_id=attempt.id, fence_token=1
    )
    assert validation is not None
    materialized = json.loads(await materialize_virtual_artifact(validation))
    assert materialized["value"] == {"validation": {"verdict": "passed"}}


async def test_virtual_artifact_is_deterministic_bounded_and_redacted(db) -> None:
    job, attempt = await _attempt(db, "virtual")
    row = await register_virtual_artifact(
        job_id=job.id,
        attempt_id=attempt.id,
        fence_token=1,
        source="request",
        name="Canonical request",
    )
    first = await materialize_virtual_artifact(row)
    second = await materialize_virtual_artifact(row)
    assert first == second
    assert hashlib.sha256(first).hexdigest() == row.checksum
    document = json.loads(first)
    assert document["value"]["api_key"] == "[REDACTED]"
    assert b"must-not-escape" not in first

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(f"/api/jobs/{job.id}/artifacts/{row.id}/download")
        assert response.status_code == 200
        assert response.content == first
        assert response.headers["content-type"].startswith("application/json")
        presentation = await client.get(f"/api/jobs/{job.id}/presentation")
        assert presentation.status_code == 200
        assert presentation.json()["evidence"]["artifacts_available"] is True


async def test_physical_policy_failure_leaves_failed_evidence(db) -> None:
    job, attempt = await _attempt(db, "too-large")
    job_id = job.id
    source = _source("oversize.txt", b"x" * (1024 * 1024 + 1))
    with pytest.raises(ArtifactError, match="exceeds"):
        await register_physical_artifact(
            job_id=job.id,
            attempt_id=attempt.id,
            fence_token=1,
            source=source,
            kind="diagnostic_text",
            name="Oversize diagnostics.txt",
            content_type="text/plain",
            data_dir=settings.DATA_DIR,
        )
    db.expire_all()
    row = (await db.scalars(select(JobArtifact).where(JobArtifact.job_id == job_id))).one()
    assert row.status == "failed"
    assert row.artifact_metadata["failure_code"] == "ArtifactError"
    failed = (
        await db.scalars(
            select(JobEvent).where(
                JobEvent.job_id == job_id, JobEvent.event_key == "artifact.failed"
            )
        )
    ).one()
    assert failed.detail["artifact_id"] == row.id


async def test_artifact_expiration_and_reconciliation_are_idempotent(db) -> None:
    job, attempt = await _attempt(db, "expiry")
    job_id = job.id
    row = await register_physical_artifact(
        job_id=job.id,
        attempt_id=attempt.id,
        fence_token=1,
        source=_source("expiry.txt", b"expire"),
        kind="diagnostic_text",
        name="Expiring diagnostics.txt",
        content_type="text/plain",
        retention_class="ephemeral",
        data_dir=settings.DATA_DIR,
    )
    row_id = row.id
    await db.execute(
        update(JobArtifact)
        .where(JobArtifact.id == row_id)
        .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
    )
    await db.commit()
    first = await expire_artifacts(data_dir=settings.DATA_DIR)
    second = await expire_artifacts(data_dir=settings.DATA_DIR)
    assert first == {
        "claimed": 1,
        "expired": 1,
        "deleted": 1,
        "missing": 0,
        "failed": 0,
    }
    assert second == {
        "claimed": 0,
        "expired": 0,
        "deleted": 0,
        "missing": 0,
        "failed": 0,
    }
    db.expire_all()
    assert (await db.get(JobArtifact, row_id)).status == "expired"
    expired = (
        await db.scalars(
            select(JobEvent).where(
                JobEvent.job_id == job_id, JobEvent.event_key == "artifact.expired"
            )
        )
    ).one()
    assert expired.detail["artifact_id"] == row_id

    orphan = _source("untracked.txt", b"orphan")
    # Reconciliation is alert-only and cannot delete an untracked file.
    managed = settings.data_dir_path / "jobs" / "evidence" / "artifacts" / "orphan"
    managed.mkdir(parents=True)
    orphan_path = managed / "artifact-orphan"
    orphan_path.write_bytes((settings.data_dir_path / orphan.key.value).read_bytes())
    counts = await reconcile_artifacts(data_dir=settings.DATA_DIR)
    assert counts["untracked"] >= 1
    assert orphan_path.exists()


async def test_log_retention_deletes_before_metadata_expiration(db) -> None:
    job, attempt = await _attempt(db, "log-expiry")
    boundary = artifact_boundary(settings.DATA_DIR)
    key = f"jobs/evidence/logs/{job.id}/{attempt.id}/segment-0.log"
    target = boundary.from_key("data", key)
    boundary.create_directory(
        boundary.from_key("data", f"jobs/evidence/logs/{job.id}/{attempt.id}"),
        parents=True,
    )
    fd = boundary.create_file(target)
    try:
        os.write(fd, b"sealed log\n")
        os.fsync(fd)
    finally:
        os.close(fd)
    row = JobLog(
        job_id=job.id,
        attempt_id=attempt.id,
        segment=0,
        storage_key=key,
        seal_status="sealed",
        expires_at=datetime.now(UTC) - timedelta(seconds=1),
    )
    db.add(row)
    await db.commit()

    result = await expire_logs(data_dir=settings.DATA_DIR)
    await db.refresh(row)
    assert result["expired"] == 1
    assert result["deleted"] == 1
    assert result["failed"] == 0
    assert row.seal_status == "expired"
    assert not (settings.data_dir_path / key).exists()


async def test_physical_expiration_failure_remains_retryable(
    db, monkeypatch: pytest.MonkeyPatch
) -> None:
    job, attempt = await _attempt(db, "expiry-retry")
    row = await register_physical_artifact(
        job_id=job.id,
        attempt_id=attempt.id,
        fence_token=1,
        source=_source("retry.txt", b"retry"),
        kind="diagnostic_text",
        name="Retry diagnostics.txt",
        content_type="text/plain",
        retention_class="ephemeral",
        data_dir=settings.DATA_DIR,
    )
    row_id = row.id
    await db.execute(
        update(JobArtifact)
        .where(JobArtifact.id == row_id)
        .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
    )
    await db.commit()

    monkeypatch.setattr(
        "marquee.core.jobs.artifact_service.FilesystemBoundary.delete_file",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("storage unavailable")),
    )
    result = await expire_artifacts(data_dir=settings.DATA_DIR)
    await db.rollback()
    row = await db.get(JobArtifact, row_id)
    assert row is not None
    assert result["failed"] == 1
    assert row.status == "available"
    assert row.artifact_metadata["expiration_failure"] == "OSError"


async def test_artifact_rejects_unclassified_paths_and_unsafe_names(db) -> None:
    job, attempt = await _attempt(db, "reject")
    with pytest.raises(ArtifactError, match="classified"):
        await register_physical_artifact(
            job_id=job.id,
            attempt_id=attempt.id,
            fence_token=1,
            source="/etc/passwd",  # type: ignore[arg-type]
            kind="diagnostic_text",
            name="passwd.txt",
            content_type="text/plain",
        )
    with pytest.raises(ArtifactError, match="unsafe"):
        await register_physical_artifact(
            job_id=job.id,
            attempt_id=attempt.id,
            fence_token=1,
            source=_source("safe.txt", b"safe"),
            kind="diagnostic_text",
            name="../../unsafe.txt",
            content_type="text/plain",
        )
