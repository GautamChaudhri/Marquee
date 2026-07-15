"""B2 typed inventories built from real generated media (JMC5B §5/B13).

These run the real ffprobe/mkvmerge probe over synthesised MKV fixtures and prove
that the typed inventory and durable selectors describe actual media — not a
hand-written dict.  Fixtures are generated into tmp_path; nothing binary is
committed and no operator library is touched.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from marquee.core.jobs.track_inventory_adapter import inventory_from_probe
from marquee.core.jobs.track_selectors import (
    TrackSelectorError,
    resolve_selector,
    selector_for,
)
from marquee.core.subtitles.probe import probe_container
from tests.support.media_fixtures import (
    DEFAULT_AUDIO,
    DEFAULT_SUBTITLES,
    DUPLICATE_AUDIO,
    build_mkv,
    require_media_tools,
)


def _inventory(path: Path, signature: str = "sha256:fixture-1"):
    result = probe_container(path)
    return inventory_from_probe(
        signature=signature,
        audio_streams=result.audio_streams,
        subtitles=result.subtitles,
        container=result.container,
    )


@pytest.fixture
def multi_track(tmp_path: Path) -> Path:
    require_media_tools()
    return build_mkv(
        tmp_path / "media",
        "multi.mkv",
        audio=DEFAULT_AUDIO,
        subtitles=DEFAULT_SUBTITLES,
        attachment=True,
        chapters=True,
    )


def test_inventory_describes_every_real_track(multi_track: Path) -> None:
    inventory = _inventory(multi_track)
    audio = [e for e in inventory.entries if e.facts.kind == "audio"]
    subtitles = [e for e in inventory.entries if e.facts.kind == "subtitle"]
    assert len(audio) == 2
    assert len(subtitles) == 2

    surround = audio[0]
    assert surround.facts.channels == 6
    assert surround.facts.codec == "ac3"
    assert surround.facts.title == "Surround"
    assert surround.facts.is_default is True
    # The probe normalizes to ISO 639-1, so the selector is stable even though
    # mkvmerge was given "fra" and ffprobe reports "fre".
    assert audio[1].facts.channels == 2
    assert audio[1].facts.language_tag == "fr"
    assert audio[0].facts.language_tag == "en"

    forced = [e for e in subtitles if e.facts.is_forced]
    assert len(forced) == 1
    assert forced[0].facts.language_tag == "fr"
    assert all(e.facts.source == "embedded" for e in subtitles)


def test_selectors_resolve_against_real_probe_output(multi_track: Path) -> None:
    inventory = _inventory(multi_track)
    for entry in inventory.entries:
        selector = selector_for(entry, inventory)
        assert resolve_selector(selector, inventory).track_key == entry.track_key


def test_duplicate_real_tracks_stay_individually_addressable(tmp_path: Path) -> None:
    """Two byte-identical eng stereo AAC tracks must not collide."""
    require_media_tools()
    path = build_mkv(tmp_path / "dup", "dup.mkv", audio=DUPLICATE_AUDIO)
    inventory = _inventory(path)
    audio = [e for e in inventory.entries if e.facts.kind == "audio"]
    assert len(audio) == 2
    assert audio[0].track_key != audio[1].track_key
    assert audio[0].facts == audio[1].facts  # identical facts, distinct ordinals

    second = selector_for(audio[1], inventory)
    assert resolve_selector(second, inventory).stream_index == audio[1].stream_index


def test_a_replaced_file_fails_selector_resolution(tmp_path: Path) -> None:
    """A rebuilt file is a different source; its signature fence must refuse."""
    require_media_tools()
    path = build_mkv(tmp_path / "m", "m.mkv", audio=DEFAULT_AUDIO, subtitles=DEFAULT_SUBTITLES)
    inventory = _inventory(path, signature="sha256:before")
    selector = selector_for(inventory.entries[0], inventory)

    replaced = _inventory(path, signature="sha256:after")
    with pytest.raises(TrackSelectorError) as excinfo:
        resolve_selector(selector, replaced)
    assert excinfo.value.reason == "signature_changed"


def test_removing_a_track_makes_only_that_selector_stale(tmp_path: Path) -> None:
    """The authoritative post-operation rescan drives outcomes, not expected deltas."""
    require_media_tools()
    before_path = build_mkv(
        tmp_path / "b", "before.mkv", audio=DEFAULT_AUDIO, subtitles=DEFAULT_SUBTITLES
    )
    before = _inventory(before_path, signature="sha256:same")
    keep = [e for e in before.entries if e.facts.kind == "audio" and e.facts.channels == 6][0]
    drop = [e for e in before.entries if e.facts.kind == "audio" and e.facts.channels == 2][0]
    keep_selector = selector_for(keep, before)
    drop_selector = selector_for(drop, before)

    after_path = build_mkv(
        tmp_path / "a", "after.mkv", audio=(DEFAULT_AUDIO[0],), subtitles=DEFAULT_SUBTITLES
    )
    after = _inventory(after_path, signature="sha256:same")

    assert resolve_selector(keep_selector, after).facts.channels == 6
    with pytest.raises(TrackSelectorError) as excinfo:
        resolve_selector(drop_selector, after)
    assert excinfo.value.reason == "stale"
