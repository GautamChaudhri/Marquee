"""C2 real-media gates for candidate-only tracked letterbox re-encode."""

from __future__ import annotations

from pathlib import Path

import pytest

from marquee.core.jobs.handlers_letterbox_reencode import execute_letterbox_reencode
from marquee.core.jobs.letterbox_reencode_documents import ReencodeProbeV1
from marquee.models import JobArtifact
from tests.support.jmc5b_harness import execution_context, media_file_row, real_signature
from tests.support.media_fixtures import build_mkv, require_media_tools

pytestmark = pytest.mark.usefixtures("jmc5b_media_roots")


@pytest.mark.asyncio
async def test_reencode_creates_verified_canonical_candidate_without_publishing_source(
    db, tmp_path: Path, data_dir: Path
) -> None:
    require_media_tools()
    source = build_mkv(tmp_path / "library", "reencode-source.mkv")
    media_file = await media_file_row(db, source)
    await db.commit()
    before = source.read_bytes()
    request = {
        "media_file_id": media_file.id,
        "subject_kind": "movie",
        "subject_id": 1,
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

    assert result["outcome"] == "succeeded", result
    assert result["output_probe"]["width"] == 64
    assert result["output_probe"]["height"] == 40
    assert source.read_bytes() == before
    artifact = await db.get(JobArtifact, result["artifact_id"])
    assert artifact is not None
    assert artifact.kind == "media_candidate"
    assert artifact.status == "available"
    managed = data_dir / artifact.storage_key
    assert managed.is_file()
    assert managed.read_bytes() != before


@pytest.mark.asyncio
async def test_reencode_fails_closed_for_uncertified_dolby_vision(
    db, tmp_path: Path, monkeypatch
) -> None:
    require_media_tools()
    source = build_mkv(tmp_path / "library", "dovi-source.mkv")
    media_file = await media_file_row(db, source)
    await db.commit()
    request = {
        "media_file_id": media_file.id,
        "subject_kind": "movie",
        "subject_id": 1,
        "crop_top": 4,
        "crop_bottom": 4,
        "output_height": 40,
        "source": {
            "signature": real_signature(source),
            "size_bytes": source.stat().st_size,
            "codec": "hevc",
            "width": 64,
            "height": 48,
            "duration_seconds": 1.0,
            "pixel_format": "yuv420p10le",
            "color_transfer": "smpte2084",
            "color_primaries": "bt2020",
            "color_space": "bt2020nc",
            "has_hdr": True,
            "has_dolby_vision": True,
            "video_streams": 1,
            "audio_streams": 0,
            "subtitle_streams": 0,
            "attachment_streams": 0,
        },
        "encoder": {
            "codec": "hevc",
            "encoder": "libx265",
            "family": "cpu",
            "quality": 23,
            "preset": "ultrafast",
            "used_cpu_fallback": True,
        },
    }
    context = await execution_context(
        db, tmp_path, job_type="letterbox_reencode", request=request
    )
    async def dovi_probe(_context, _path):
        return ReencodeProbeV1(
            codec="hevc",
            width=64,
            height=48,
            duration_seconds=1.0,
            has_hdr=True,
            has_dolby_vision=True,
            video_streams=1,
            audio_streams=0,
            subtitle_streams=0,
            attachment_streams=0,
        )

    monkeypatch.setattr("marquee.core.jobs.handlers_letterbox_reencode._probe", dovi_probe)
    result = await execute_letterbox_reencode(context)
    assert result["outcome"] == "failed"
    assert result["reason_code"] == "dolby_vision_uncertified"
