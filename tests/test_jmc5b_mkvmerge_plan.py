"""B2 mkvmerge plan building executed against real media (JMC5B §6.1/B09).

These do not assert on argument strings alone: they run real mkvmerge over
generated fixtures and re-probe the output, because B13 makes the post-operation
inventory authoritative.  §6.1's preservation rules (attachments, chapters,
non-target streams, surviving metadata) are checked against actual output.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from marquee.core.jobs.mkvmerge_plan import (
    MkvmergePlanError,
    build_metadata_args,
    build_remove_args,
    build_reorder_args,
)
from marquee.core.jobs.progress_adapters import MkvmergeProgressAdapter
from marquee.core.jobs.track_inventory_adapter import inventory_from_probe
from marquee.core.subtitles.probe import probe_container
from tests.support.media_fixtures import (
    DEFAULT_AUDIO,
    DEFAULT_SUBTITLES,
    AudioSpec,
    build_mkv,
    require_media_tools,
)


def _inventory(path: Path, signature: str = "sha256:s"):
    result = probe_container(path)
    return inventory_from_probe(
        signature=signature,
        audio_streams=result.audio_streams,
        subtitles=result.subtitles,
        container=result.container,
    )


def _run(args) -> str:
    completed = subprocess.run(list(args), capture_output=True, text=True, timeout=120)
    assert completed.returncode == 0, completed.stderr[:400]
    return completed.stdout


@pytest.fixture
def source(tmp_path: Path) -> Path:
    require_media_tools()
    return build_mkv(
        tmp_path / "src",
        "multi.mkv",
        audio=DEFAULT_AUDIO,
        subtitles=DEFAULT_SUBTITLES,
        attachment=True,
        chapters=True,
    )


def test_removal_drops_only_the_target_and_preserves_everything_else(
    source: Path, tmp_path: Path
) -> None:
    before = _inventory(source)
    target = [e for e in before.entries if e.facts.kind == "audio" and e.facts.channels == 2][0]
    destination = tmp_path / "out.mkv"

    args = build_remove_args(
        source=str(source), destination=str(destination), inventory=before, removed=[target]
    )
    stdout = _run(args)

    after = _inventory(destination)
    audio = [e for e in after.entries if e.facts.kind == "audio"]
    subtitles = [e for e in after.entries if e.facts.kind == "subtitle"]
    assert len(audio) == 1
    assert audio[0].facts.channels == 6  # the surviving track kept its identity
    assert audio[0].facts.title == "Surround"
    assert audio[0].facts.is_default is True
    assert len(subtitles) == 2  # non-target streams preserved

    result = probe_container(destination)
    assert result.chapters_count == 1  # chapters preserved
    assert result.attachments_count == 1  # attachments preserved

    # B09: the same run yields parsable native progress reaching completion.
    samples = []
    for line in stdout.splitlines():
        samples.extend(MkvmergeProgressAdapter().feed(line + "\n"))
    assert any(s.mode == "determinate" and s.completed == 100 for s in samples)


def test_removing_every_audio_track_uses_an_explicit_no_audio_plan(
    source: Path, tmp_path: Path
) -> None:
    before = _inventory(source)
    audio = [e for e in before.entries if e.facts.kind == "audio"]
    destination = tmp_path / "silent.mkv"

    args = build_remove_args(
        source=str(source), destination=str(destination), inventory=before, removed=audio
    )
    assert "--no-audio" in args
    _run(args)

    after = _inventory(destination)
    assert not [e for e in after.entries if e.facts.kind == "audio"]
    assert len([e for e in after.entries if e.facts.kind == "subtitle"]) == 2


def test_reorder_produces_the_requested_audio_order(source: Path, tmp_path: Path) -> None:
    before = _inventory(source)
    audio = [e for e in before.entries if e.facts.kind == "audio"]
    reversed_order = list(reversed(audio))
    destination = tmp_path / "reordered.mkv"

    args = build_reorder_args(
        source=str(source),
        destination=str(destination),
        inventory=before,
        ordered_audio=reversed_order,
    )
    _run(args)

    after = _inventory(destination)
    after_audio = [e for e in after.entries if e.facts.kind == "audio"]
    assert [e.facts.channels for e in after_audio] == [2, 6]
    assert [e.facts.language_tag for e in after_audio] == ["fr", "en"]


def test_metadata_edit_changes_flags_without_touching_payloads(
    source: Path, tmp_path: Path
) -> None:
    before = _inventory(source)
    target = [e for e in before.entries if e.facts.kind == "subtitle" and not e.facts.is_forced][0]
    original_size = source.stat().st_size

    args = build_metadata_args(
        source=str(source), inventory=before, edits=[(target, {"flag-forced": True})]
    )
    assert args[0] == "mkvpropedit"
    _run(args)

    after = _inventory(source)
    forced = [e for e in after.entries if e.facts.kind == "subtitle" and e.facts.is_forced]
    assert len(forced) == 2  # the original forced track plus the edited one
    # An in-place property edit must not rewrite the stream payloads.
    assert abs(source.stat().st_size - original_size) < 4096


def test_plans_reject_unsafe_or_incomplete_requests(source: Path, tmp_path: Path) -> None:
    before = _inventory(source)
    audio = [e for e in before.entries if e.facts.kind == "audio"]

    with pytest.raises(MkvmergePlanError, match="at least one track"):
        build_remove_args(
            source=str(source), destination=str(tmp_path / "o.mkv"), inventory=before, removed=[]
        )
    with pytest.raises(MkvmergePlanError, match="only be removed once"):
        build_remove_args(
            source=str(source),
            destination=str(tmp_path / "o.mkv"),
            inventory=before,
            removed=[audio[0], audio[0]],
        )
    with pytest.raises(MkvmergePlanError, match="order every audio track"):
        build_reorder_args(
            source=str(source),
            destination=str(tmp_path / "o.mkv"),
            inventory=before,
            ordered_audio=[audio[0]],
        )
    with pytest.raises(MkvmergePlanError, match="at least one track"):
        build_metadata_args(source=str(source), inventory=before, edits=[])


def test_removal_may_not_empty_the_file(tmp_path: Path) -> None:
    require_media_tools()
    path = build_mkv(
        tmp_path / "a", "audio_only.mkv", audio=(AudioSpec(language="eng", channels=2, codec="aac"),)
    )
    inventory = _inventory(path)
    # A synthetic inventory containing only the audio track proves the guard.
    audio_only = inventory.model_copy(
        update={"entries": tuple(e for e in inventory.entries if e.facts.kind == "audio")}
    )
    with pytest.raises(MkvmergePlanError, match="may not empty the file"):
        build_remove_args(
            source=str(path),
            destination=str(tmp_path / "o.mkv"),
            inventory=audio_only,
            removed=list(audio_only.entries),
        )
