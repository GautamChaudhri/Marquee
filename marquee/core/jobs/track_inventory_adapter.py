"""Build typed track inventories from real probe output (JMC5B §5/B13).

The probe is the authority for what a file actually contains, so both the before
snapshot and the post-operation actual snapshot are produced here from the same
adapter.  Expected deltas alone can never declare success (B13).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from marquee.core.jobs.track_selectors import (
    TrackFactsV1,
    TrackInventoryV1,
    build_inventory,
)


def _get(row: Any, key: str) -> Any:
    """Read a field from either a probe mapping or a probe dataclass."""
    if isinstance(row, Mapping):
        return row.get(key)
    return getattr(row, key, None)


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def audio_facts(stream: Any) -> TrackFactsV1:
    """Typed facts for one probed embedded audio stream."""
    return TrackFactsV1(
        kind="audio",
        source="embedded",
        language_tag=_text(_get(stream, "language_tag")) or "und",
        codec=_text(_get(stream, "codec")),
        channels=_get(stream, "channels"),
        title=_text(_get(stream, "title")),
        is_default=bool(_get(stream, "is_default")),
        is_forced=bool(_get(stream, "is_forced")),
        is_hearing_impaired=bool(_get(stream, "is_sdh")),
    )


def subtitle_facts(track: Any) -> TrackFactsV1:
    """Typed facts for one probed subtitle track, embedded or external."""
    source = "external" if _text(_get(track, "source")) == "external" else "embedded"
    return TrackFactsV1(
        kind="subtitle",
        source=source,
        language_tag=_text(_get(track, "language_tag")) or "und",
        codec=_text(_get(track, "codec")),
        channels=None,
        title=_text(_get(track, "title")),
        is_default=bool(_get(track, "is_default")),
        is_forced=bool(_get(track, "is_forced")),
        is_hearing_impaired=bool(_get(track, "is_sdh")),
        managed_key=_text(_get(track, "managed_key")),
    )


def inventory_from_probe(
    *,
    signature: str,
    audio_streams: Iterable[Any],
    subtitles: Iterable[Any],
    container: str | None = None,
) -> TrackInventoryV1:
    """One typed inventory used identically for before and actual state."""
    rows: list[tuple[TrackFactsV1, int | None, int | None]] = []
    for stream in audio_streams:
        rows.append((audio_facts(stream), _get(stream, "index"), _get(stream, "tool_track_id")))
    for track in subtitles:
        rows.append(
            (subtitle_facts(track), _get(track, "stream_index"), _get(track, "tool_track_id"))
        )
    return build_inventory(signature=signature, tracks=rows, container=container)
