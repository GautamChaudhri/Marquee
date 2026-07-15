"""B2 end-to-end track mutation handlers over real media (JMC5B §6.1/B08/B13).

These drive the enabled leaves through a real ``ProcessLauncher`` running real
mkvmerge/mkvpropedit against generated fixtures, then assert on the authoritative
post-operation rescan.  Everything is confined to ``tmp_path``; no operator
library, live provider, or normal ``DATA_DIR`` is touched.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from marquee.core.jobs.handlers_track_mutations import (
    execute_audio_remove,
    execute_audio_reorder,
    execute_subtitle_metadata,
    execute_subtitle_remove,
)
from marquee.core.jobs.track_selectors import selector_for
from marquee.core.subtitles.probe import probe_container
from tests.support.jmc5b_harness import execution_context as _context
from tests.support.jmc5b_harness import inventory_of as _inventory
from tests.support.jmc5b_harness import media_file_row as _media_file
from tests.support.media_fixtures import (
    DEFAULT_AUDIO,
    DEFAULT_SUBTITLES,
    build_mkv,
    require_media_tools,
)

pytestmark = pytest.mark.usefixtures("jmc5b_media_roots")


async def _fixture(db, tmp_path: Path, **kwargs):
    require_media_tools()
    media = tmp_path / "library"
    path = build_mkv(
        media,
        "show.mkv",
        audio=kwargs.get("audio", DEFAULT_AUDIO),
        subtitles=kwargs.get("subtitles", DEFAULT_SUBTITLES),
        attachment=True,
        chapters=True,
    )
    row = await _media_file(db, path)
    return path, row


@pytest.mark.asyncio
async def test_audio_remove_publishes_and_is_proven_by_rescan(
    db, tmp_path: Path, data_dir: Path
) -> None:
    path, row = await _fixture(db, tmp_path)
    before = _inventory(path)
    target = [e for e in before.entries if e.facts.kind == "audio" and e.facts.channels == 2][0]
    request = {
        "media_file_id": row.id,
        "selectors": [selector_for(target, before).model_dump(mode="json")],
    }
    # The handler re-probes, so the request's signature must match reality.
    context = await _context(db, tmp_path, job_type="audio_remove", request=request)
    result = await execute_audio_remove(context)

    assert result["outcome"] == "succeeded"
    assert result["atomicity"]["published"] is True
    assert len(context.writer.publish_intents) == 1
    assert len(context.writer.publications) == 1
    assert [o["status"] for o in result["target_outcomes"]] == ["succeeded"]
    assert result["target_outcomes"][0]["stage"] == "rescan"

    # B13: the authoritative rescan, not an expected delta, proves the change.
    actual = result["actual_inventory"]
    assert len([e for e in actual["entries"] if e["facts"]["kind"] == "audio"]) == 1
    assert len([e for e in actual["entries"] if e["facts"]["kind"] == "subtitle"]) == 2

    # B12: a recoverable checksummed backup exists before replacement.
    backup = result["backup"]
    assert backup["restore_eligible"] is True
    assert (data_dir / backup["artifact_key"]).exists()

    # §6.1 preservation, verified on the published file.
    published = probe_container(path)
    assert published.chapters_count == 1
    assert published.attachments_count == 1


@pytest.mark.asyncio
async def test_stale_selector_fails_before_any_mutation(
    db, tmp_path: Path, data_dir: Path
) -> None:
    path, row = await _fixture(db, tmp_path)
    before = _inventory(path)
    target = [e for e in before.entries if e.facts.kind == "audio"][0]
    selector = selector_for(target, before).model_dump(mode="json")
    selector["track_key"] = "audio:" + "f" * 32 + ":0"  # a track that is not present
    original = path.read_bytes()

    context = await _context(
        db, tmp_path, job_type="audio_remove",
        request={"media_file_id": row.id, "selectors": [selector]},
    )
    result = await execute_audio_remove(context)

    assert result["outcome"] == "failed"
    assert result["atomicity"]["published"] is False
    assert all(o["status"] == "not_applied" for o in result["target_outcomes"])
    assert result["target_outcomes"][0]["stage"] == "resolve"
    assert path.read_bytes() == original  # nothing was written


@pytest.mark.asyncio
async def test_changed_source_signature_fails_before_any_mutation(
    db, tmp_path: Path, data_dir: Path
) -> None:
    path, row = await _fixture(db, tmp_path)
    before = _inventory(path, signature="sha256:stale-plan")
    target = [e for e in before.entries if e.facts.kind == "audio"][0]
    original = path.read_bytes()

    context = await _context(
        db, tmp_path, job_type="audio_remove",
        request={
            "media_file_id": row.id,
            "selectors": [selector_for(target, before).model_dump(mode="json")],
        },
    )
    result = await execute_audio_remove(context)

    assert result["outcome"] == "failed"
    assert result["target_outcomes"][0]["reason_code"] == "signature_changed"
    assert result["atomicity"]["published"] is False
    assert path.read_bytes() == original


@pytest.mark.asyncio
async def test_multi_target_removal_reports_every_target(
    db, tmp_path: Path, data_dir: Path
) -> None:
    path, row = await _fixture(db, tmp_path)
    before = _inventory(path)
    targets = [e for e in before.entries if e.facts.kind == "subtitle"]
    request = {
        "media_file_id": row.id,
        "selectors": [selector_for(t, before).model_dump(mode="json") for t in targets],
    }
    context = await _context(db, tmp_path, job_type="subtitle_remove", request=request)
    result = await execute_subtitle_remove(context)

    assert result["outcome"] == "succeeded"
    assert len(result["target_outcomes"]) == 2
    assert all(o["status"] == "succeeded" for o in result["target_outcomes"])
    actual = result["actual_inventory"]
    assert not [e for e in actual["entries"] if e["facts"]["kind"] == "subtitle"]
    assert len([e for e in actual["entries"] if e["facts"]["kind"] == "audio"]) == 2


@pytest.mark.asyncio
async def test_audio_reorder_publishes_the_requested_order(
    db, tmp_path: Path, data_dir: Path
) -> None:
    path, row = await _fixture(db, tmp_path)
    before = _inventory(path)
    audio = [e for e in before.entries if e.facts.kind == "audio"]
    request = {
        "media_file_id": row.id,
        "ordered_selectors": [
            selector_for(e, before).model_dump(mode="json") for e in reversed(audio)
        ],
    }
    context = await _context(db, tmp_path, job_type="audio_reorder", request=request)
    result = await execute_audio_reorder(context)

    assert result["outcome"] == "succeeded"
    actual_audio = [
        e for e in result["actual_inventory"]["entries"] if e["facts"]["kind"] == "audio"
    ]
    assert [e["facts"]["language_tag"] for e in actual_audio] == ["fr", "en"]


@pytest.mark.asyncio
async def test_already_satisfied_metadata_is_a_reasoned_no_change(
    db, tmp_path: Path, data_dir: Path
) -> None:
    path, row = await _fixture(db, tmp_path)
    before = _inventory(path)
    forced = [e for e in before.entries if e.facts.kind == "subtitle" and e.facts.is_forced][0]
    original = path.read_bytes()

    request = {
        "media_file_id": row.id,
        "edits": [
            {
                "selector": selector_for(forced, before).model_dump(mode="json"),
                "is_forced": True,  # already true
            }
        ],
    }
    context = await _context(db, tmp_path, job_type="subtitle_metadata", request=request)
    result = await execute_subtitle_metadata(context)

    assert result["outcome"] == "no_change"
    assert result["reason_code"] == "already_satisfied"
    assert all(o["status"] == "skipped" for o in result["target_outcomes"])
    assert result["atomicity"]["published"] is False
    assert path.read_bytes() == original


@pytest.mark.asyncio
async def test_metadata_edit_publishes_and_rescans(
    db, tmp_path: Path, data_dir: Path
) -> None:
    path, row = await _fixture(db, tmp_path)
    before = _inventory(path)
    plain = [e for e in before.entries if e.facts.kind == "subtitle" and not e.facts.is_forced][0]

    request = {
        "media_file_id": row.id,
        "edits": [
            {
                "selector": selector_for(plain, before).model_dump(mode="json"),
                "is_forced": True,
            }
        ],
    }
    context = await _context(db, tmp_path, job_type="subtitle_metadata", request=request)
    result = await execute_subtitle_metadata(context)

    assert result["outcome"] == "succeeded"
    assert result["backup"]["checksum"]
    assert len(context.writer.publish_intents) == 1
    assert len(context.writer.publications) == 1
    actual = result["actual_inventory"]
    forced = [
        e for e in actual["entries"]
        if e["facts"]["kind"] == "subtitle" and e["facts"]["is_forced"]
    ]
    assert len(forced) == 2
