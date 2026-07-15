"""B3 subtitle extract/embed over real media (JMC5B §6.2/B13/B17).

Extraction must publish a managed, checksummed artifact and leave the source
byte-identical.  Embedding must consume only a validated managed key and prove
the new track by rescan.  All fixtures are confined synthetic roots.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from marquee.core.jobs.handlers_sidecars import (
    SidecarError,
    execute_subtitle_embed,
    execute_subtitle_extract,
    managed_key,
    validate_subtitle_bytes,
)
from marquee.core.jobs.track_selectors import selector_for
from marquee.models import ManagedSubtitleAsset
from tests.support.jmc5b_harness import execution_context as _context
from tests.support.jmc5b_harness import inventory_of as _inventory
from tests.support.jmc5b_harness import media_file_row as _media_file
from tests.support.media_fixtures import (
    AudioSpec,
    SubtitleSpec,
    build_mkv,
    require_media_tools,
)
from tests.test_jmc5b_track_mutation_handlers import _fixture

pytestmark = pytest.mark.usefixtures("jmc5b_media_roots")


def test_subtitle_validation_rejects_empty_binary_and_untimed_output() -> None:
    with pytest.raises(SidecarError, match="empty"):
        validate_subtitle_bytes(b"")
    with pytest.raises(SidecarError, match="UTF-8"):
        validate_subtitle_bytes(b"\xff\xfe\x00binary")
    with pytest.raises(SidecarError, match="cue timing"):
        validate_subtitle_bytes(b"just some prose with no cues")
    validate_subtitle_bytes(b"1\n00:00:00,000 --> 00:00:01,000\nhello\n")


@pytest.mark.asyncio
async def test_extract_publishes_managed_sidecar_and_leaves_source_untouched(
    db, tmp_path: Path, data_dir: Path
) -> None:
    path, row = await _fixture(db, tmp_path)
    before = _inventory(path)
    target = [e for e in before.entries if e.facts.kind == "subtitle" and e.facts.is_forced][0]
    original = path.read_bytes()

    context = await _context(
        db, tmp_path, job_type="subtitle_extract",
        request={
            "media_file_id": row.id,
            "selector": selector_for(target, before).model_dump(mode="json"),
        },
    )
    result = await execute_subtitle_extract(context)

    assert result["outcome"] == "succeeded"
    assert result["target_outcomes"][0]["stage"] == "sidecar"
    # B17: a registered artifact addressed by key + checksum, never a raw path.
    sidecar = result["sidecar"]
    assert sidecar["storage_key"] == managed_key(sidecar["checksum"])
    assert str(tmp_path) not in sidecar["storage_key"]
    stored = data_dir / sidecar["storage_key"]
    assert stored.exists()
    assert hashlib.sha256(stored.read_bytes()).hexdigest() == sidecar["checksum"]
    assert b"bonjour" in stored.read_bytes()  # the forced French track

    # Extraction never alters the source.
    assert path.read_bytes() == original

    asset = await db.get(ManagedSubtitleAsset, sidecar["managed_asset_id"])
    assert asset is not None and asset.active is True
    assert asset.cache_path == sidecar["storage_key"]


@pytest.mark.asyncio
async def test_repeated_extract_is_content_addressed_and_reuses_the_asset(
    db, tmp_path: Path, data_dir: Path
) -> None:
    path, row = await _fixture(db, tmp_path)
    before = _inventory(path)
    target = [e for e in before.entries if e.facts.kind == "subtitle"][0]
    request = {
        "media_file_id": row.id,
        "selector": selector_for(target, before).model_dump(mode="json"),
    }
    first = await execute_subtitle_extract(
        await _context(db, tmp_path, job_type="subtitle_extract", request=request)
    )
    second = await execute_subtitle_extract(
        await _context(db, tmp_path, job_type="subtitle_extract", request=request)
    )
    assert first["sidecar"]["checksum"] == second["sidecar"]["checksum"]
    assert first["sidecar"]["managed_asset_id"] == second["sidecar"]["managed_asset_id"]
    parent = (data_dir / "jmc5/managed-subtitles")
    assert len([p for p in parent.iterdir() if not p.name.startswith(".")]) == 1


@pytest.mark.asyncio
async def test_extract_of_a_stale_selector_fails_before_writing(
    db, tmp_path: Path, data_dir: Path
) -> None:
    path, row = await _fixture(db, tmp_path)
    before = _inventory(path)
    target = [e for e in before.entries if e.facts.kind == "subtitle"][0]
    selector = selector_for(target, before).model_dump(mode="json")
    selector["track_key"] = "subtitle:" + "e" * 32 + ":0"

    context = await _context(
        db, tmp_path, job_type="subtitle_extract",
        request={"media_file_id": row.id, "selector": selector},
    )
    result = await execute_subtitle_extract(context)
    assert result["outcome"] == "failed"
    assert result["target_outcomes"][0]["stage"] == "resolve"
    assert result["sidecar"] is None
    assert not (data_dir / "jmc5/managed-subtitles").exists() or not list(
        (data_dir / "jmc5/managed-subtitles").iterdir()
    )


@pytest.mark.asyncio
async def test_extract_then_embed_round_trip_is_proven_by_rescan(
    db, tmp_path: Path, data_dir: Path
) -> None:
    require_media_tools()
    # A file with one subtitle track; we extract it, then embed it back as a
    # second, distinctly-tagged track.
    media = tmp_path / "library"
    path = build_mkv(
        media,
        "show.mkv",
        audio=(AudioSpec(language="eng", channels=2, codec="aac"),),
        subtitles=(SubtitleSpec(language="eng", text="hello"),),
    )
    row = await _media_file(db, path)
    before = _inventory(path)
    target = [e for e in before.entries if e.facts.kind == "subtitle"][0]

    extracted = await execute_subtitle_extract(
        await _context(
            db, tmp_path, job_type="subtitle_extract",
            request={
                "media_file_id": row.id,
                "selector": selector_for(target, before).model_dump(mode="json"),
            },
        )
    )
    assert extracted["outcome"] == "succeeded"
    asset_id = extracted["sidecar"]["managed_asset_id"]

    embedded = await execute_subtitle_embed(
        await _context(
            db, tmp_path, job_type="subtitle_embed",
            request={
                "media_file_id": row.id,
                "managed_asset_id": asset_id,
                "language_tag": "deu",
                "title": "Embedded",
                "is_forced": True,
            },
        )
    )
    assert embedded["outcome"] == "succeeded"
    assert embedded["atomicity"]["published"] is True
    assert embedded["backup"]["restore_eligible"] is True

    # B13: the rescan proves the exact new track exists.
    actual = embedded["actual_inventory"]
    subtitles = [e for e in actual["entries"] if e["facts"]["kind"] == "subtitle"]
    assert len(subtitles) == 2
    added = [e for e in subtitles if e["facts"]["is_forced"]]
    assert len(added) == 1
    assert added[0]["facts"]["language_tag"] == "de"
    assert added[0]["facts"]["title"] == "Embedded"


@pytest.mark.asyncio
async def test_embed_refuses_an_unknown_managed_asset(
    db, tmp_path: Path, data_dir: Path
) -> None:
    path, row = await _fixture(db, tmp_path)
    original = path.read_bytes()
    context = await _context(
        db, tmp_path, job_type="subtitle_embed",
        request={
            "media_file_id": row.id,
            "managed_asset_id": "does-not-exist",
            "language_tag": "eng",
        },
    )
    result = await execute_subtitle_embed(context)
    assert result["outcome"] == "failed"
    assert result["target_outcomes"][0]["reason_code"] == "asset_missing"
    assert result["atomicity"]["published"] is False
    assert path.read_bytes() == original


@pytest.mark.asyncio
async def test_embed_refuses_a_tampered_managed_asset(
    db, tmp_path: Path, data_dir: Path
) -> None:
    path, row = await _fixture(db, tmp_path)
    before = _inventory(path)
    target = [e for e in before.entries if e.facts.kind == "subtitle"][0]
    extracted = await execute_subtitle_extract(
        await _context(
            db, tmp_path, job_type="subtitle_extract",
            request={
                "media_file_id": row.id,
                "selector": selector_for(target, before).model_dump(mode="json"),
            },
        )
    )
    stored = data_dir / extracted["sidecar"]["storage_key"]
    stored.write_text("1\n00:00:00,000 --> 00:00:01,000\ntampered\n\n")
    original = path.read_bytes()

    result = await execute_subtitle_embed(
        await _context(
            db, tmp_path, job_type="subtitle_embed",
            request={
                "media_file_id": row.id,
                "managed_asset_id": extracted["sidecar"]["managed_asset_id"],
                "language_tag": "eng",
            },
        )
    )
    assert result["outcome"] == "failed"
    assert result["target_outcomes"][0]["stage"] == "validate"
    assert result["atomicity"]["published"] is False
    assert path.read_bytes() == original
