"""Durable audio/subtitle track selectors and inventories (JMC5B §5 / B07).

Neither of the obvious identities is durable:

* ``SubtitleTrack.id`` is a fresh ``uuid4`` on every rescan, so it cannot survive
  the gap between planning and confirmation;
* a raw container stream index shifts the moment any earlier track is removed or
  reordered, so B07 forbids it as a selector on its own.

A selector therefore carries a **derived** stable key built from the track's
immutable facts plus an occurrence ordinal among otherwise identical tracks, the
inventory signature that proves the whole file has not drifted, and the original
stream/tool identity kept strictly as a diagnostic hint.

Resolution fails closed: a drifted signature, a vanished track, or an ambiguous
match is refused **before** any mutation runs.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Sequence
from typing import Literal

from pydantic import Field

from marquee.core.jobs.documents import StrictDocument

TrackKind = Literal["audio", "subtitle"]
TrackSource = Literal["embedded", "external"]

SelectorFailure = Literal["ambiguous", "signature_changed", "stale"]


class TrackSelectorError(Exception):
    """A requested selector cannot be resolved against current reality."""

    def __init__(self, reason: SelectorFailure, message: str) -> None:
        super().__init__(message)
        self.reason: SelectorFailure = reason


class TrackFactsV1(StrictDocument):
    """Immutable facts that identify a track independently of its position."""

    kind: TrackKind
    source: TrackSource
    language_tag: str = Field(min_length=1, max_length=40)
    codec: str | None = Field(default=None, max_length=40)
    channels: int | None = Field(default=None, ge=0, le=64)
    title: str | None = Field(default=None, max_length=300)
    is_default: bool = False
    is_forced: bool = False
    is_hearing_impaired: bool = False
    #: Source-relative managed key for an external sidecar; never an absolute path.
    managed_key: str | None = Field(default=None, max_length=240)

    def fingerprint(self) -> str:
        """Content fingerprint of the immutable facts only."""
        material = json.dumps(
            self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), allow_nan=False
        )
        return hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]


class TrackSelectorV1(StrictDocument):
    """One requested target: derived stable key + facts + drift fences."""

    track_key: str = Field(min_length=1, max_length=80, pattern=r"^[a-z]+:[0-9a-f]{32}:[0-9]+$")
    facts: TrackFactsV1
    inventory_signature: str = Field(min_length=1, max_length=160)
    #: Diagnostics only.  B07 forbids these from deciding a resolution.
    stream_index_hint: int | None = Field(default=None, ge=0)
    tool_track_id_hint: int | None = Field(default=None, ge=0)


class TrackEntryV1(StrictDocument):
    """One track as observed in a before/actual inventory."""

    track_key: str = Field(min_length=1, max_length=80)
    facts: TrackFactsV1
    stream_index: int | None = Field(default=None, ge=0)
    tool_track_id: int | None = Field(default=None, ge=0)


class TrackInventoryV1(StrictDocument):
    """A complete typed inventory used identically for before and actual state."""

    signature: str = Field(min_length=1, max_length=160)
    container: str | None = Field(default=None, max_length=40)
    entries: tuple[TrackEntryV1, ...] = ()

    def by_key(self, track_key: str) -> tuple[TrackEntryV1, ...]:
        return tuple(entry for entry in self.entries if entry.track_key == track_key)


def derive_track_key(facts: TrackFactsV1, ordinal: int) -> str:
    """Stable key: kind + fact fingerprint + occurrence ordinal among identical facts."""
    if ordinal < 0:
        raise ValueError("track ordinal must be non-negative")
    return f"{facts.kind}:{facts.fingerprint()}:{ordinal}"


def build_inventory(
    *,
    signature: str,
    tracks: Iterable[tuple[TrackFactsV1, int | None, int | None]],
    container: str | None = None,
) -> TrackInventoryV1:
    """Assign deterministic ordinals so duplicate-metadata tracks stay addressable."""
    seen: dict[str, int] = {}
    entries: list[TrackEntryV1] = []
    for facts, stream_index, tool_track_id in tracks:
        fingerprint = facts.fingerprint()
        ordinal = seen.get(fingerprint, 0)
        seen[fingerprint] = ordinal + 1
        entries.append(
            TrackEntryV1(
                track_key=derive_track_key(facts, ordinal),
                facts=facts,
                stream_index=stream_index,
                tool_track_id=tool_track_id,
            )
        )
    return TrackInventoryV1(signature=signature, container=container, entries=tuple(entries))


def selector_for(entry: TrackEntryV1, inventory: TrackInventoryV1) -> TrackSelectorV1:
    """Build a durable selector from an observed inventory entry."""
    return TrackSelectorV1(
        track_key=entry.track_key,
        facts=entry.facts,
        inventory_signature=inventory.signature,
        stream_index_hint=entry.stream_index,
        tool_track_id_hint=entry.tool_track_id,
    )


def resolve_selector(
    selector: TrackSelectorV1, inventory: TrackInventoryV1
) -> TrackEntryV1:
    """Resolve one selector or fail closed before any mutation runs."""
    if selector.inventory_signature != inventory.signature:
        raise TrackSelectorError(
            "signature_changed", "the source file changed since the selector was captured"
        )
    matches = inventory.by_key(selector.track_key)
    if not matches:
        raise TrackSelectorError("stale", "the requested track is no longer present")
    if len(matches) > 1:
        raise TrackSelectorError("ambiguous", "the requested track is ambiguous")
    match = matches[0]
    if match.facts != selector.facts:
        raise TrackSelectorError("stale", "the requested track changed since it was captured")
    return match


def resolve_all(
    selectors: Sequence[TrackSelectorV1], inventory: TrackInventoryV1
) -> tuple[TrackEntryV1, ...]:
    """Resolve every selector, refusing duplicates within one request."""
    seen: set[str] = set()
    resolved: list[TrackEntryV1] = []
    for selector in selectors:
        if selector.track_key in seen:
            raise TrackSelectorError(
                "ambiguous", "the same track was requested more than once"
            )
        seen.add(selector.track_key)
        resolved.append(resolve_selector(selector, inventory))
    return tuple(resolved)
