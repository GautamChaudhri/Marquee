"""Coverage classification (design §17.1) — pure logic over normalized tracks.

Turns a list of normalized track dicts + audio streams into the server-computed
coverage summary the frontend filters on (full-dialogue languages, forced-only,
SDH, commentary, external-vs-embedded, missing preferred languages, unknowns).

A *full-dialogue* subtitle is one that is neither forced nor commentary — i.e.
it can stand in for the whole spoken track. Forced/commentary do NOT count as
full coverage (§16.9).
"""

from __future__ import annotations

from collections.abc import Iterable

from marquee.core.subtitles import languages


def _track_lang(track: dict) -> str:
    return track.get("language_tag") or languages.UNDETERMINED


def is_full_dialogue(track: dict) -> bool:
    return not track.get("is_forced") and not track.get("is_commentary")


def compute_coverage(
    tracks: Iterable[dict],
    audio_streams: Iterable[dict],
    *,
    preferred_languages: Iterable[str] = (),
) -> dict:
    """Summarize subtitle coverage for a media file."""
    tracks = list(tracks)
    full: set[str] = set()
    forced: set[str] = set()
    sdh: set[str] = set()
    commentary = external = embedded = unknown = generated = False

    for track in tracks:
        lang = _track_lang(track)
        if track.get("is_commentary"):
            commentary = True
        if track.get("is_generated"):
            generated = True
        if track.get("source") == "external":
            external = True
        else:
            embedded = True
        if lang == languages.UNDETERMINED:
            unknown = True
        if track.get("is_sdh"):
            sdh.add(lang)
        if track.get("is_forced"):
            forced.add(lang)
        if is_full_dialogue(track):
            full.add(lang)

    audio_langs = sorted({a.get("language_tag") or languages.UNDETERMINED for a in audio_streams})
    preferred = {languages.normalize(p)[0] for p in preferred_languages}
    missing_preferred = sorted(preferred - full)

    return {
        "audio_languages": audio_langs,
        "full_dialogue_languages": sorted(full),
        "forced_only_languages": sorted(forced - full),
        "sdh_languages": sorted(sdh),
        "commentary_present": commentary,
        "external_present": external,
        "embedded_present": embedded,
        "generated_present": generated,
        "unknown_present": unknown,
        "missing_preferred_languages": missing_preferred,
        "track_count": len(tracks),
    }
