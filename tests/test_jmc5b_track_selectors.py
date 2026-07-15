"""B1 durable track selector and inventory contracts (JMC5B §5 / B07).

These prove a selector survives the identities that are *not* durable — the
rescan-regenerated inventory row id and the container stream index — while
failing closed on real drift.
"""

from __future__ import annotations

import pytest

from marquee.core.jobs.track_selectors import (
    TrackFactsV1,
    TrackSelectorError,
    build_inventory,
    derive_track_key,
    resolve_all,
    resolve_selector,
    selector_for,
)

SIG = "sha256:source-1"


def _audio(language: str = "eng", *, channels: int = 6, title: str | None = None) -> TrackFactsV1:
    return TrackFactsV1(
        kind="audio",
        source="embedded",
        language_tag=language,
        codec="eac3",
        channels=channels,
        title=title,
    )


def _sub(language: str = "eng", *, source: str = "embedded", **kwargs) -> TrackFactsV1:
    return TrackFactsV1(kind="subtitle", source=source, language_tag=language, codec="subrip", **kwargs)


def test_identical_tracks_receive_distinct_ordinal_keys() -> None:
    """The §9 duplicate-metadata fixture must stay individually addressable."""
    first, second = _audio(), _audio()
    inventory = build_inventory(signature=SIG, tracks=[(first, 1, 0), (second, 2, 1)])
    keys = [entry.track_key for entry in inventory.entries]
    assert keys[0] != keys[1]
    assert keys == [derive_track_key(first, 0), derive_track_key(second, 1)]


def test_selector_resolves_across_stream_index_drift() -> None:
    """B07: a raw stream index is a hint, never the selector."""
    keep, target = _audio("eng"), _audio("fra")
    inventory = build_inventory(signature=SIG, tracks=[(keep, 1, 0), (target, 2, 1)])
    selector = selector_for(inventory.entries[1], inventory)
    assert selector.stream_index_hint == 2

    # An earlier removal shifts every later index; the selector must still resolve.
    drifted = build_inventory(signature=SIG, tracks=[(keep, 9, 4), (target, 11, 5)])
    resolved = resolve_selector(selector, drifted)
    assert resolved.facts.language_tag == "fra"
    assert resolved.stream_index == 11


def test_changed_inventory_signature_fails_before_mutation() -> None:
    facts = _audio()
    inventory = build_inventory(signature=SIG, tracks=[(facts, 1, 0)])
    selector = selector_for(inventory.entries[0], inventory)
    replaced = build_inventory(signature="sha256:replaced", tracks=[(facts, 1, 0)])
    with pytest.raises(TrackSelectorError) as excinfo:
        resolve_selector(selector, replaced)
    assert excinfo.value.reason == "signature_changed"


def test_vanished_track_is_stale() -> None:
    target, other = _sub("eng"), _sub("fra")
    inventory = build_inventory(signature=SIG, tracks=[(target, 2, 0), (other, 3, 1)])
    selector = selector_for(inventory.entries[0], inventory)
    without = build_inventory(signature=SIG, tracks=[(other, 2, 0)])
    with pytest.raises(TrackSelectorError) as excinfo:
        resolve_selector(selector, without)
    assert excinfo.value.reason == "stale"


def test_changed_track_facts_are_stale() -> None:
    """A retitled/redisposed track is a different track, not a silent match."""
    inventory = build_inventory(signature=SIG, tracks=[(_sub("eng", is_forced=True), 2, 0)])
    selector = selector_for(inventory.entries[0], inventory)
    mutated = build_inventory(signature=SIG, tracks=[(_sub("eng", is_forced=False), 2, 0)])
    with pytest.raises(TrackSelectorError) as excinfo:
        resolve_selector(selector, mutated)
    assert excinfo.value.reason == "stale"


def test_external_sidecar_uses_managed_key_not_a_path() -> None:
    facts = _sub("eng", source="external", managed_key="managed/eng.forced.srt")
    inventory = build_inventory(signature=SIG, tracks=[(facts, None, None)])
    selector = selector_for(inventory.entries[0], inventory)
    assert selector.facts.managed_key == "managed/eng.forced.srt"
    assert selector.stream_index_hint is None
    assert resolve_selector(selector, inventory).facts.source == "external"


def test_repeated_selector_in_one_request_is_ambiguous() -> None:
    inventory = build_inventory(signature=SIG, tracks=[(_audio(), 1, 0)])
    selector = selector_for(inventory.entries[0], inventory)
    with pytest.raises(TrackSelectorError) as excinfo:
        resolve_all([selector, selector], inventory)
    assert excinfo.value.reason == "ambiguous"


def test_resolve_all_returns_every_requested_target_in_order() -> None:
    a, b, c = _audio("eng"), _audio("fra"), _sub("deu")
    inventory = build_inventory(signature=SIG, tracks=[(a, 1, 0), (b, 2, 1), (c, 3, 2)])
    selectors = [selector_for(inventory.entries[i], inventory) for i in (2, 0)]
    resolved = resolve_all(selectors, inventory)
    assert [entry.facts.language_tag for entry in resolved] == ["deu", "eng"]


def test_selector_key_shape_is_bounded_and_typed() -> None:
    inventory = build_inventory(signature=SIG, tracks=[(_audio(), 1, 0)])
    selector = selector_for(inventory.entries[0], inventory)
    # Pattern-guarded: kind:fingerprint:ordinal, carrying no path or raw index.
    kind, fingerprint, ordinal = selector.track_key.split(":")
    assert kind == "audio"
    assert len(fingerprint) == 32
    assert ordinal == "0"
