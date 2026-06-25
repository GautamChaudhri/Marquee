"""ffprobe-backed container inspection (design §4, §20).

Produces the container-independent read model the API serves: embedded subtitle
streams (codec/kind/language/dispositions), audio languages, duration, chapter
and attachment counts. For MKV we additionally align ffprobe subtitle streams to
``mkvmerge`` track IDs, which the Matroska mutation adapter needs (ffprobe stream
indices and mkvmerge track IDs are not the same numbering).

Read-only. No media file is ever written here.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

from marquee.core.subtitles import coverage, languages
from marquee.media import binaries

logger = logging.getLogger(__name__)

_TEXT_CODECS = {"subrip", "srt", "ass", "ssa", "mov_text", "webvtt", "vtt", "text", "stl"}
_BITMAP_CODECS = {
    "hdmv_pgs_subtitle",
    "pgssub",
    "dvd_subtitle",
    "dvdsub",
    "vobsub",
    "xsub",
    "dvbsub",
}
_TELETEXT_CODECS = {"dvb_teletext"}

_AUDIO_FORMAT_LABELS = {
    "aac": "AAC",
    "ac3": "Dolby Digital",
    "eac3": "Dolby Digital Plus",
    "truehd": "Dolby TrueHD",
    "mlp": "MLP",
    "dts": "DTS",
    "dts_hd": "DTS-HD",
    "flac": "FLAC",
    "alac": "ALAC",
    "opus": "Opus",
    "vorbis": "Vorbis",
    "mp3": "MP3",
}


def codec_kind(codec: str | None) -> str:
    c = (codec or "").lower()
    if c in _TEXT_CODECS:
        return "text"
    if c in _BITMAP_CODECS:
        return "bitmap"
    if c in _TELETEXT_CODECS:
        return "teletext"
    return "unknown"


def _audio_format_label(stream: dict, tags: dict) -> str | None:
    codec = (stream.get("codec_name") or "").lower()
    profile = (stream.get("profile") or "").strip()
    title = (tags.get("title") or "").lower()
    probe_text = " ".join(
        str(value).lower()
        for value in (
            stream.get("codec_long_name"),
            profile,
            tags.get("title"),
            tags.get("handler_name"),
        )
        if value
    )

    if "atmos" in probe_text or "joc" in probe_text:
        if codec == "truehd":
            return "Dolby TrueHD Atmos"
        if codec == "eac3":
            return "Dolby Atmos"
        return "Dolby Atmos"
    if "dts:x" in title or "dtsx" in title:
        return "DTS:X"
    if codec.startswith("pcm_"):
        return "PCM"
    if codec == "dts" and "ma" in profile.lower():
        return "DTS-HD MA"
    return _AUDIO_FORMAT_LABELS.get(codec) or profile or stream.get("codec_long_name")


@dataclass
class EmbeddedSub:
    stream_index: int
    codec: str | None
    kind: str
    language_raw: str | None
    language_tag: str
    language_source: str
    title: str | None
    is_default: bool
    is_forced: bool
    is_sdh: bool
    is_commentary: bool
    tool_track_id: int | None = None


@dataclass
class ProbeResult:
    container: str | None
    duration_seconds: float | None
    streams: list[dict] = field(default_factory=list)
    audio_streams: list[dict] = field(default_factory=list)
    subtitles: list[EmbeddedSub] = field(default_factory=list)
    chapters_count: int = 0
    attachments_count: int = 0
    video_count: int = 0


def _ffprobe_json(path: Path | str) -> dict | None:
    if binaries.resolve("ffprobe") is None:
        return None
    result = binaries.run(
        "ffprobe",
        [
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            "-show_chapters",
            str(path),
        ],
        timeout=60.0,
    )
    if not result.ok:
        logger.warning("ffprobe failed for %s: %s", path, result.stderr.strip()[:200])
        return None
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return None


def probe_container(path: Path | str) -> ProbeResult | None:
    """Probe *path* for subtitle/audio/container facts. None if ffprobe fails."""
    data = _ffprobe_json(path)
    if data is None:
        return None

    fmt = data.get("format", {})
    duration = None
    if fmt.get("duration") is not None:
        try:
            duration = float(fmt["duration"])
        except (TypeError, ValueError):
            duration = None

    audio: list[dict] = []
    subs: list[EmbeddedSub] = []
    streams: list[dict] = []
    video_count = attachments = 0

    for stream in data.get("streams", []):
        codec_type = stream.get("codec_type")
        streams.append(
            {
                "index": stream.get("index"),
                "codec_type": codec_type,
                "codec": stream.get("codec_name"),
            }
        )
        if codec_type == "video":
            video_count += 1
        elif codec_type == "attachment":
            attachments += 1
        elif codec_type == "audio":
            tags = stream.get("tags", {}) or {}
            raw_lang = tags.get("language")
            tag, _ = languages.normalize(raw_lang)
            disposition = stream.get("disposition", {}) or {}
            title_low = (tags.get("title") or "").lower()
            audio.append(
                {
                    "index": stream.get("index"),
                    "codec": stream.get("codec_name"),
                    "codec_long_name": stream.get("codec_long_name"),
                    "profile": stream.get("profile"),
                    "format_label": _audio_format_label(stream, tags),
                    "language": tag,
                    "language_raw": raw_lang,
                    "language_tag": tag,
                    "language_source": "metadata" if raw_lang else "unknown",
                    "title": tags.get("title"),
                    "channels": stream.get("channels"),
                    "channel_layout": stream.get("channel_layout"),
                    "channel_label": coverage.channel_label(stream),
                    "disposition": disposition,
                    "is_default": bool(disposition.get("default")),
                    "is_forced": bool(disposition.get("forced")),
                    "is_sdh": bool(disposition.get("hearing_impaired")),
                    "is_commentary": bool(disposition.get("comment"))
                    or bool(disposition.get("commentary"))
                    or "commentary" in title_low,
                }
            )
        elif codec_type == "subtitle":
            subs.append(_embedded_sub(stream))

    if path and str(path).lower().endswith(".mkv"):
        _align_mkv_track_ids(path, subs, audio)

    return ProbeResult(
        container=fmt.get("format_name"),
        duration_seconds=duration,
        streams=streams,
        audio_streams=audio,
        subtitles=subs,
        chapters_count=len(data.get("chapters", [])),
        attachments_count=attachments,
        video_count=video_count,
    )


def _embedded_sub(stream: dict) -> EmbeddedSub:
    tags = stream.get("tags", {}) or {}
    disp = stream.get("disposition", {}) or {}
    raw_lang = tags.get("language")
    tag, _ = languages.normalize(raw_lang)
    title = tags.get("title")
    title_low = (title or "").lower()
    return EmbeddedSub(
        stream_index=stream.get("index"),
        codec=stream.get("codec_name"),
        kind=codec_kind(stream.get("codec_name")),
        language_raw=raw_lang,
        language_tag=tag,
        language_source="metadata" if raw_lang else "unknown",
        title=title,
        is_default=bool(disp.get("default")),
        is_forced=bool(disp.get("forced")),
        is_sdh=bool(disp.get("hearing_impaired")) or "sdh" in title_low,
        is_commentary=bool(disp.get("comment")) or "commentary" in title_low,
    )


def _align_mkv_track_ids(path: Path | str, subs: list[EmbeddedSub], audio: list[dict]) -> None:
    """Fill ``tool_track_id`` for MKV subs and audio by aligning mkvmerge tracks.

    ffprobe lists tracks in the same relative order mkvmerge does, so
    we zip the two sequences. Best-effort — leaves None if mkvmerge is
    unavailable or the shapes disagree.
    """
    if binaries.resolve("mkvmerge") is None:
        return
    result = binaries.run("mkvmerge", ["-J", binaries.safe_media_path(path)], timeout=30.0)
    if not result.ok:
        return
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return

    tracks_data = data.get("tracks", [])

    sub_track_ids = [t.get("id") for t in tracks_data if t.get("type") == "subtitles"]
    if len(sub_track_ids) == len(subs):
        for sub, track_id in zip(subs, sub_track_ids, strict=True):
            sub.tool_track_id = track_id

    audio_track_ids = [t.get("id") for t in tracks_data if t.get("type") == "audio"]
    if len(audio_track_ids) == len(audio):
        for aud, track_id in zip(audio, audio_track_ids, strict=True):
            aud["tool_track_id"] = track_id
