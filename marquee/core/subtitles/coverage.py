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

_CHANNEL_LAYOUT_LABELS = {
    "mono": "1.0",
    "stereo": "2.0",
    "2.1": "2.1",
    "3.0": "3.0",
    "3.1": "3.1",
    "quad": "4.0",
    "4.0": "4.0",
    "5.0": "5.0",
    "5.1": "5.1",
    "5.1(side)": "5.1",
    "6.0": "6.0",
    "6.1": "6.1",
    "7.0": "7.0",
    "7.1": "7.1",
    "7.1(wide)": "7.1",
    "7.1(wide-side)": "7.1",
    "octagonal": "8.0",
}


def _track_lang(track: dict) -> str:
    return track.get("language_tag") or languages.UNDETERMINED


def is_full_dialogue(track: dict) -> bool:
    return not track.get("is_forced") and not track.get("is_commentary")


def normalize_language_list(values: Iterable[str] | None) -> list[str]:
    """Normalize, dedupe, and preserve user order for preferred languages."""
    result: list[str] = []
    seen: set[str] = set()
    for value in values or []:
        tag = languages.normalize(value)[0]
        if tag not in seen:
            result.append(tag)
            seen.add(tag)
    return result


def channel_label(stream: dict) -> str | None:
    """Return a home-theater style channel label when ffprobe gives enough data."""
    layout = (stream.get("channel_layout") or "").strip().lower()
    if layout in _CHANNEL_LAYOUT_LABELS:
        return _CHANNEL_LAYOUT_LABELS[layout]

    for marker in ("7.1", "6.1", "5.1", "5.0", "4.0", "3.1", "3.0", "2.1"):
        if marker in layout:
            return marker

    channels = stream.get("channels")
    if channels is None:
        return None
    try:
        count = int(channels)
    except (TypeError, ValueError):
        return None

    return {
        1: "1.0",
        2: "2.0",
        3: "3.0",
        4: "4.0",
        5: "5.0",
        6: "6ch",
        7: "6.1",
        8: "7.1",
    }.get(count, f"{count}ch")


def effective_preferred_languages(
    *,
    preferred_languages: Iterable[str] = (),
    preferred_audio_languages: Iterable[str] | None = None,
    preferred_subtitle_languages: Iterable[str] | None = None,
) -> tuple[list[str], list[str]]:
    """Resolve shared/split preferred language settings into audio/subtitle lists."""
    shared = normalize_language_list(preferred_languages)
    audio = normalize_language_list(preferred_audio_languages)
    subtitles = normalize_language_list(preferred_subtitle_languages)
    return audio or shared, subtitles or shared


def apply_preferences(
    summary: dict,
    *,
    preferred_languages: Iterable[str] = (),
    preferred_audio_languages: Iterable[str] | None = None,
    preferred_subtitle_languages: Iterable[str] | None = None,
) -> dict:
    """Attach missing-preferred fields and combined status to a coverage summary."""
    preferred_audio, preferred_subtitle = effective_preferred_languages(
        preferred_languages=preferred_languages,
        preferred_audio_languages=preferred_audio_languages,
        preferred_subtitle_languages=preferred_subtitle_languages,
    )
    audio_languages = set(summary.get("audio_languages") or [])
    subtitle_languages = set(summary.get("full_dialogue_languages") or [])
    missing_audio = [lang for lang in preferred_audio if lang not in audio_languages]
    missing_subtitles = [lang for lang in preferred_subtitle if lang not in subtitle_languages]

    with_preferences = dict(summary)
    with_preferences.update(
        {
            "preferred_audio_languages": preferred_audio,
            "preferred_subtitle_languages": preferred_subtitle,
            "missing_preferred_audio_languages": missing_audio,
            "missing_preferred_languages": missing_subtitles,
            "audio_status": "gap" if missing_audio else "ok",
            "subtitle_status": "gap" if missing_subtitles else "ok",
            "status": "gap" if missing_audio or missing_subtitles else "ok",
        }
    )
    return with_preferences


def compute_coverage(
    tracks: Iterable[dict],
    audio_streams: Iterable[dict],
    *,
    preferred_languages: Iterable[str] = (),
    preferred_audio_languages: Iterable[str] | None = None,
    preferred_subtitle_languages: Iterable[str] | None = None,
) -> dict:
    """Summarize subtitle coverage for a media file."""
    tracks = list(tracks)
    audio_streams = list(audio_streams)
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
    channels_by_language: dict[str, list[str]] = {}
    for stream in audio_streams:
        lang = stream.get("language_tag") or languages.UNDETERMINED
        label = stream.get("channel_label") or channel_label(stream)
        if not label:
            continue
        channels_by_language.setdefault(lang, [])
        if label not in channels_by_language[lang]:
            channels_by_language[lang].append(label)

    summary = {
        "audio_languages": audio_langs,
        "audio_channels_by_language": {
            lang: sorted(labels) for lang, labels in sorted(channels_by_language.items())
        },
        "full_dialogue_languages": sorted(full),
        "forced_only_languages": sorted(forced - full),
        "sdh_languages": sorted(sdh),
        "commentary_present": commentary,
        "external_present": external,
        "embedded_present": embedded,
        "generated_present": generated,
        "unknown_present": unknown,
        "track_count": len(tracks),
    }
    return apply_preferences(
        summary,
        preferred_languages=preferred_languages,
        preferred_audio_languages=preferred_audio_languages,
        preferred_subtitle_languages=preferred_subtitle_languages,
    )
