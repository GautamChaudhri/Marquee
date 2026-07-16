"""C3 real-media gates for canonical publish, restore, discard, and reconciliation."""

from __future__ import annotations

from pathlib import Path

import pytest

from marquee.api.routes.letterbox import replace_reencode_original
from marquee.core.jobs.handlers_letterbox_publication import (
    execute_letterbox_reencode_discard,
    execute_letterbox_reencode_publish,
    execute_letterbox_reencode_restore,
)
from marquee.core.jobs.handlers_letterbox_reencode import execute_letterbox_reencode
from marquee.core.jobs.mutation_planning import confirm_mutation
from marquee.core.jobs.submission import Initiator
from marquee.models import Job, JobArtifact, Movie
from tests.support.jmc5b_harness import execution_context, media_file_row, real_signature
from tests.support.media_fixtures import build_mkv, require_media_tools

pytestmark = pytest.mark.usefixtures("jmc5b_media_roots")


async def _candidate(db, tmp_path: Path) -> tuple[Path, object, JobArtifact, bytes, dict]:
    source = build_mkv(tmp_path / "library", "publication-source.mkv")
    media_file = await media_file_row(db, source)
    movie = Movie(
        title="Publication Fixture",
        year=2026,
        folder_path=str(source.parent),
        movie_file_path=str(source),
        tmdb_id=900001,
    )
    db.add(movie)
    await db.flush()
    media_file.movie_id = movie.id
    await db.commit()
    original = source.read_bytes()
    request = {
        "media_file_id": media_file.id,
        "subject_kind": "movie",
        "subject_id": movie.id,
        "crop_top": 4,
        "crop_bottom": 4,
        "output_height": 40,
        "source": {
            "signature": real_signature(source),
            "size_bytes": source.stat().st_size,
            "codec": "h264",
            "width": 64,
            "height": 48,
            "duration_seconds": 1.0,
            "pixel_format": "yuv420p",
            "color_transfer": None,
            "color_primaries": None,
            "color_space": None,
            "has_hdr": False,
            "has_dolby_vision": False,
            "video_streams": 1,
            "audio_streams": 0,
            "subtitle_streams": 0,
            "attachment_streams": 0,
        },
        "encoder": {
            "codec": "h264",
            "encoder": "libx264",
            "family": "cpu",
            "quality": 23,
            "preset": "ultrafast",
            "used_cpu_fallback": True,
        },
    }
    context = await execution_context(
        db, tmp_path, job_type="letterbox_reencode", request=request
    )
    result = await execute_letterbox_reencode(context)
    artifact = await db.get(JobArtifact, result["artifact_id"])
    assert artifact is not None
    return source, media_file, artifact, original, request


def _publish_request(media_file, artifact: JobArtifact) -> dict:
    metadata = artifact.artifact_metadata or {}
    return {
        "media_file_id": media_file.id,
        "subject_kind": metadata["subject_kind"],
        "subject_id": metadata["subject_id"],
        "candidate_artifact_id": artifact.id,
        "candidate_job_id": artifact.job_id,
        "candidate_checksum": artifact.checksum,
        "candidate_size_bytes": artifact.size_bytes,
        "expected_source_signature": metadata["source_signature"],
        "crop_top": metadata["crop_top"],
        "crop_bottom": metadata["crop_bottom"],
        "source_probe": metadata["source_probe"],
        "candidate_probe": metadata["output_probe"],
    }


@pytest.mark.asyncio
async def test_publish_route_creates_a_confirmable_path_free_plan(
    db, tmp_path: Path, data_dir: Path, installed_pgqueuer
) -> None:
    require_media_tools()
    source, _media_file, candidate, original, _ = await _candidate(db, tmp_path)

    document = await replace_reencode_original(candidate.id, db)
    assert document["status"] == "planned"
    assert document["plan_version"]
    assert document["configuration_version"] >= 1
    job = await db.get(Job, document["job_id"])
    assert job is not None
    assert job.type == "letterbox_reencode_publish"
    assert job.phase == "planned"
    assert "path" not in str(job.request).lower()
    assert source.read_bytes() == original
    await confirm_mutation(
        db,
        job_id=job.id,
        expected_plan_version=document["plan_version"],
        current_input_signature=(candidate.artifact_metadata or {})["source_signature"],
        confirmed_by=Initiator(kind="user", identifier="test"),
        expected_configuration_version=document["configuration_version"],
    )
    await db.commit()
    await db.refresh(job)
    assert job.phase == "queued"


@pytest.mark.asyncio
async def test_publish_restore_and_discard_use_canonical_evidence(
    db, tmp_path: Path, data_dir: Path
) -> None:
    require_media_tools()
    source, media_file, candidate, original, _ = await _candidate(db, tmp_path)
    publish_context = await execution_context(
        db,
        tmp_path,
        job_type="letterbox_reencode_publish",
        request=_publish_request(media_file, candidate),
    )
    published = await execute_letterbox_reencode_publish(publish_context)
    assert published["outcome"] == "succeeded", published
    assert published["rescanned"] is True
    assert source.read_bytes() != original
    backup = await db.get(JobArtifact, published["backup_artifact_id"])
    assert backup is not None
    assert backup.kind == "media_backup"
    assert backup.status == "available"
    assert (data_dir / backup.storage_key).read_bytes() == original

    restore_request = {
        "media_file_id": media_file.id,
        "subject_kind": (candidate.artifact_metadata or {})["subject_kind"],
        "subject_id": (candidate.artifact_metadata or {})["subject_id"],
        "candidate_artifact_id": candidate.id,
        "backup_artifact_id": backup.id,
        "backup_checksum": backup.checksum,
        "backup_size_bytes": backup.size_bytes,
        "expected_destination_signature": real_signature(source),
        "published_checksum": candidate.checksum,
    }
    restore_context = await execution_context(
        db, tmp_path, job_type="letterbox_reencode_restore", request=restore_request
    )
    restored = await execute_letterbox_reencode_restore(restore_context)
    assert restored["outcome"] == "succeeded", restored
    assert restored["rescanned"] is True
    assert source.read_bytes() == original

    await db.refresh(candidate)
    discard_context = await execution_context(
        db,
        tmp_path,
        job_type="letterbox_reencode_discard",
        request={
            "media_file_id": media_file.id,
            "subject_kind": (candidate.artifact_metadata or {})["subject_kind"],
            "subject_id": (candidate.artifact_metadata or {})["subject_id"],
            "candidate_artifact_id": candidate.id,
            "candidate_checksum": candidate.checksum,
        },
    )
    discarded = await execute_letterbox_reencode_discard(discard_context)
    assert discarded["outcome"] == "succeeded", discarded
    await db.refresh(candidate)
    assert candidate.status == "expired"
    assert not (data_dir / candidate.storage_key).exists()


@pytest.mark.asyncio
async def test_publish_reconciles_crash_after_atomic_replacement(
    db, tmp_path: Path, data_dir: Path, monkeypatch
) -> None:
    require_media_tools()
    source, media_file, candidate, original, _ = await _candidate(db, tmp_path)
    context = await execution_context(
        db,
        tmp_path,
        job_type="letterbox_reencode_publish",
        request=_publish_request(media_file, candidate),
    )
    from marquee.core.jobs import handlers_letterbox_publication as publication

    persist = publication._persist_publish
    calls = 0

    async def crash_once(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("simulated worker death after atomic replacement")
        return await persist(*args, **kwargs)

    monkeypatch.setattr(publication, "_persist_publish", crash_once)
    with pytest.raises(RuntimeError, match="simulated worker death"):
        await execute_letterbox_reencode_publish(context)
    published_bytes = source.read_bytes()
    assert published_bytes != original

    reconciled = await execute_letterbox_reencode_publish(context)
    assert reconciled["outcome"] == "succeeded", reconciled
    assert reconciled["reason_code"] == "publish_reconciled"
    assert reconciled["reconciled"] is True
    assert source.read_bytes() == published_bytes


@pytest.mark.asyncio
async def test_corrupt_candidate_is_rejected_without_source_mutation(
    db, tmp_path: Path, data_dir: Path
) -> None:
    require_media_tools()
    source, media_file, candidate, original, _ = await _candidate(db, tmp_path)
    stored = data_dir / candidate.storage_key
    stored.chmod(0o600)
    stored.write_bytes(b"corrupt")
    context = await execution_context(
        db,
        tmp_path,
        job_type="letterbox_reencode_publish",
        request=_publish_request(media_file, candidate),
    )

    result = await execute_letterbox_reencode_publish(context)

    assert result["outcome"] == "failed"
    assert result["reason_code"] == "preflight_failed"
    assert source.read_bytes() == original


@pytest.mark.asyncio
async def test_published_candidate_cannot_be_discarded_before_restore(
    db, tmp_path: Path, data_dir: Path
) -> None:
    require_media_tools()
    source, media_file, candidate, original, _ = await _candidate(db, tmp_path)
    publish_context = await execution_context(
        db,
        tmp_path,
        job_type="letterbox_reencode_publish",
        request=_publish_request(media_file, candidate),
    )
    published = await execute_letterbox_reencode_publish(publish_context)
    assert published["outcome"] == "succeeded", published
    await db.refresh(candidate)
    published_bytes = source.read_bytes()
    assert published_bytes != original
    discard_context = await execution_context(
        db,
        tmp_path,
        job_type="letterbox_reencode_discard",
        request={
            "media_file_id": media_file.id,
            "subject_kind": (candidate.artifact_metadata or {})["subject_kind"],
            "subject_id": (candidate.artifact_metadata or {})["subject_id"],
            "candidate_artifact_id": candidate.id,
            "candidate_checksum": candidate.checksum,
        },
    )

    discarded = await execute_letterbox_reencode_discard(discard_context)

    assert discarded["outcome"] == "failed"
    assert discarded["reason_code"] == "candidate_in_use"
    assert source.read_bytes() == published_bytes
    assert (data_dir / candidate.storage_key).exists()
