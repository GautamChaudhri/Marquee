"""C4 gates for canonical Dolby Vision conversion and publication."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi import HTTPException

from marquee.api.routes.hdr import DoviConvertRequest, convert_movie_dovi
from marquee.config import settings
from marquee.core.jobs.artifact_service import register_physical_artifact
from marquee.core.jobs.dovi_conversion_documents import DoviConvertRequestV1, DoviProbeV1
from marquee.core.jobs.handlers_dovi_conversion import _validation_problems
from marquee.core.jobs.handlers_dovi_publication import (
    execute_dovi_discard,
    execute_dovi_publish,
    execute_dovi_restore,
)
from marquee.core.jobs.mutation_planning import confirm_mutation
from marquee.core.jobs.submission import Initiator
from marquee.models import DoviState, Job, JobArtifact, Movie
from tests.support.jmc5b_harness import execution_context, media_file_row, real_signature
from tests.support.media_fixtures import AudioSpec, build_mkv, require_media_tools

pytestmark = pytest.mark.usefixtures("jmc5b_media_roots")


def _probe(profile: int, *, audio: int = 0, el: bool = False) -> DoviProbeV1:
    return DoviProbeV1(
        codec="hevc",
        width=64,
        height=48,
        duration_seconds=1.0,
        has_hdr=True,
        has_dolby_vision=True,
        video_streams=1,
        audio_streams=audio,
        subtitle_streams=0,
        attachment_streams=0,
        dovi_profile=profile,
        enhancement_layer_present=el,
        bl_signal_compatibility_id=1 if profile == 8 else 0,
    )


def test_conversion_documents_and_validation_are_profile_strict() -> None:
    request = DoviConvertRequestV1(
        media_file_id=1,
        movie_id=1,
        kind="p5_to_p81",
        source_signature="sha256:source",
        source_size_bytes=1000,
        source_probe=_probe(5),
    )
    assert _validation_problems(request, _probe(5), _probe(8)) == ()
    with pytest.raises(ValueError, match="does not match"):
        DoviConvertRequestV1(
            media_file_id=1,
            movie_id=1,
            kind="p5_to_p81",
            source_signature="sha256:source",
            source_size_bytes=1000,
            source_probe=_probe(7, el=True),
            source_el_type="FEL",
        )


@pytest.mark.asyncio
async def test_convert_route_creates_confirmable_path_free_canonical_plan(
    db, tmp_path: Path, installed_pgqueuer, monkeypatch
) -> None:
    require_media_tools()
    source = build_mkv(tmp_path / "library", "dovi-plan-source.mkv")
    media_file = await media_file_row(db, source)
    movie = Movie(
        title="DoVi Plan Fixture",
        year=2026,
        folder_path=str(source.parent),
        movie_file_path=str(source),
        tmdb_id=910001,
        video_width=64,
        video_height=48,
        has_hdr=True,
        has_dv=True,
    )
    db.add(movie)
    await db.flush()
    media_file.movie_id = movie.id
    db.add(
        DoviState(
            movie_id=movie.id,
            media_type="movie",
            status="analyzed",
            dovi_profile=5,
            el_present=False,
            source_codec="hevc",
        )
    )
    await db.commit()
    monkeypatch.setattr(settings, "JOB_DOVI_CONVERSION_CERTIFIED", True)
    monkeypatch.setattr("marquee.api.routes.hdr.binaries.resolve", lambda name: f"/bin/{name}")

    document = await convert_movie_dovi(movie.id, DoviConvertRequest(kind="p5_to_p81"), db)
    job = await db.get(Job, document["job_id"])
    assert job is not None
    assert job.type == "dovi_convert"
    assert job.phase == "planned"
    assert "path" not in str(job.request).lower()
    await confirm_mutation(
        db,
        job_id=job.id,
        expected_plan_version=document["plan_version"],
        current_input_signature=real_signature(source),
        confirmed_by=Initiator(kind="user", identifier="test"),
        expected_configuration_version=document["configuration_version"],
    )
    await db.commit()
    await db.refresh(job)
    assert job.phase == "queued"


@pytest.mark.asyncio
async def test_convert_route_is_disabled_without_real_fixture_certification(
    db, monkeypatch
) -> None:
    monkeypatch.setattr(settings, "JOB_DOVI_CONVERSION_CERTIFIED", False)

    with pytest.raises(HTTPException) as captured:
        await convert_movie_dovi(1, DoviConvertRequest(kind="p5_to_p81"), db)

    assert getattr(captured.value, "status_code", None) == 503
    assert getattr(captured.value, "detail", {}).get("code") == "dovi_conversion_not_certified"


async def _publication_fixture(db, tmp_path: Path):
    source = build_mkv(tmp_path / "library", "dovi-source.mkv")
    candidate_file = build_mkv(
        tmp_path / "candidate",
        "dovi-candidate.mkv",
        audio=(AudioSpec(language="eng", channels=2, codec="aac"),),
    )
    media_file = await media_file_row(db, source)
    movie = Movie(
        title="DoVi Publication Fixture",
        year=2026,
        folder_path=str(source.parent),
        movie_file_path=str(source),
        tmdb_id=910002,
        video_width=64,
        video_height=48,
        has_hdr=True,
        has_dv=True,
    )
    db.add(movie)
    await db.flush()
    media_file.movie_id = movie.id
    db.add(
        DoviState(
            movie_id=movie.id,
            media_type="movie",
            status="analyzed",
            dovi_profile=5,
            el_present=False,
            source_codec="hevc",
        )
    )
    await db.commit()
    context = await execution_context(
        db,
        tmp_path,
        job_type="dovi_convert",
        request={"media_file_id": media_file.id},
    )
    staged, fd = context.workspace.staging_file("dovi-candidate.mkv")
    os.write(fd, candidate_file.read_bytes())
    os.close(fd)
    artifact = await register_physical_artifact(
        job_id=context.delivery.canonical_job_id,
        attempt_id=context.attempt.attempt_id,
        fence_token=context.attempt.fence_token,
        source=staged,
        kind="media_candidate",
        name="dovi-candidate.mkv",
        content_type="video/x-matroska",
        retention_class="extended",
        metadata={
            "operation_family": "dovi",
            "media_file_id": media_file.id,
            "movie_id": movie.id,
            "source_signature": real_signature(source),
            "source_probe": _probe(5).model_dump(mode="json"),
            "output_probe": _probe(8, audio=1).model_dump(mode="json"),
        },
    )
    return source, media_file, movie, artifact


@pytest.mark.asyncio
async def test_dovi_publish_restore_discard_use_canonical_evidence(
    db, tmp_path: Path, data_dir: Path, monkeypatch
) -> None:
    require_media_tools()
    source, media_file, movie, candidate = await _publication_fixture(db, tmp_path)
    original = source.read_bytes()
    metadata = candidate.artifact_metadata or {}
    monkeypatch.setattr(
        "marquee.core.jobs.handlers_dovi_publication._probe",
        lambda *_args, **_kwargs: _async_value(_probe(8, audio=1)),
    )
    publish_context = await execution_context(
        db,
        tmp_path,
        job_type="dovi_publish",
        request={
            "media_file_id": media_file.id,
            "movie_id": movie.id,
            "candidate_artifact_id": candidate.id,
            "candidate_job_id": candidate.job_id,
            "candidate_checksum": candidate.checksum,
            "candidate_size_bytes": candidate.size_bytes,
            "expected_source_signature": metadata["source_signature"],
            "source_probe": metadata["source_probe"],
            "candidate_probe": metadata["output_probe"],
        },
    )
    published = await execute_dovi_publish(publish_context)
    assert published["outcome"] == "succeeded", published
    backup = await db.get(JobArtifact, published["backup_artifact_id"])
    assert backup is not None
    assert (data_dir / backup.storage_key).read_bytes() == original

    monkeypatch.setattr(
        "marquee.core.jobs.handlers_dovi_publication._probe",
        lambda *_args, **_kwargs: _async_value(_probe(5)),
    )
    restore_context = await execution_context(
        db,
        tmp_path,
        job_type="dovi_restore",
        request={
            "media_file_id": media_file.id,
            "movie_id": movie.id,
            "candidate_artifact_id": candidate.id,
            "backup_artifact_id": backup.id,
            "backup_checksum": backup.checksum,
            "backup_size_bytes": backup.size_bytes,
            "expected_destination_signature": real_signature(source),
            "published_checksum": candidate.checksum,
        },
    )
    restored = await execute_dovi_restore(restore_context)
    assert restored["outcome"] == "succeeded", restored
    assert source.read_bytes() == original
    candidate = await db.get(JobArtifact, candidate.id)
    assert candidate is not None
    discard_context = await execution_context(
        db,
        tmp_path,
        job_type="dovi_discard",
        request={
            "media_file_id": media_file.id,
            "movie_id": movie.id,
            "candidate_artifact_id": candidate.id,
            "candidate_checksum": candidate.checksum,
        },
    )
    discarded = await execute_dovi_discard(discard_context)
    assert discarded["outcome"] == "succeeded", discarded


async def _async_value(value):
    return value


def test_canonical_dovi_handler_and_route_have_no_legacy_process_or_manager_authority() -> None:
    root = Path(__file__).parents[1]
    handler = (root / "marquee/core/jobs/handlers_dovi_conversion.py").read_text()
    route = (root / "marquee/api/routes/hdr.py").read_text()
    assert "create_subprocess_exec" not in handler
    assert "subprocess.run" not in handler
    assert "job_manager.create(" not in route
    assert "candidate_path" not in handler
