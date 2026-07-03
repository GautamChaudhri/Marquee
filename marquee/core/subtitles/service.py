"""Subtitle inventory service — scan, persist, and shape for the API.

Read-only orchestration: probe the container (embedded tracks + audio +
duration), discover sidecars, normalize into a unified track model, compute
coverage + capabilities, and persist one current ``SubtitleInventory`` (+ its
``SubtitleTrack`` rows) per media file. Also provides safe text-cue preview.

No media file is written here — this is the inspection half of the feature.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from uuid import uuid4

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from marquee.core.media_files import ResolvedMediaFile, resolve_media_file
from marquee.core.subtitles import capabilities, coverage, external, probe
from marquee.core.subtitles.config import subtitle_settings
from marquee.media import binaries
from marquee.models import SubtitleInventory, SubtitleTrack

logger = logging.getLogger(__name__)

_SUBTITLE_CODEC_LABELS = {
    "ass": "ASS",
    "dvb_subtitle": "DVB",
    "dvbsub": "DVB",
    "dvb_teletext": "DVB Teletext",
    "dvd_subtitle": "DVD VobSub",
    "dvdsub": "DVD VobSub",
    "hdmv_pgs_subtitle": "PGS",
    "hdmv_text_subtitle": "HDMV Text",
    "mov_text": "MOV Text",
    "pgssub": "PGS",
    "ssa": "SSA",
    "srt": "SRT",
    "subrip": "SRT",
    "text": "Text",
    "vobsub": "VobSub",
    "webvtt": "WebVTT",
    "vtt": "WebVTT",
    "xsub": "XSUB",
}

_KIND_LABELS = {
    "text": "Text",
    "bitmap": "Bitmap",
    "teletext": "Teletext",
    "unknown": "Unknown",
}


def codec_label(codec: str | None) -> str:
    key = (codec or "").lower()
    if key in _SUBTITLE_CODEC_LABELS:
        return _SUBTITLE_CODEC_LABELS[key]
    return (codec or "unknown").replace("_", " ").upper()


def _embedded_to_dict(sub: probe.EmbeddedSub) -> dict:
    return {
        "id": uuid4().hex,
        "source": "embedded",
        "stream_index": sub.stream_index,
        "tool_track_id": sub.tool_track_id,
        "external_path": None,
        "paired_path": None,
        "codec": sub.codec,
        "kind": sub.kind,
        "language_raw": sub.language_raw,
        "language_tag": sub.language_tag,
        "language_source": sub.language_source,
        "title": sub.title,
        "is_default": sub.is_default,
        "is_forced": sub.is_forced,
        "is_sdh": sub.is_sdh,
        "is_commentary": sub.is_commentary,
        "is_generated": False,
        "size_bytes": None,
        "content_sha256": None,
    }


def _external_to_dict(sub: external.ExternalSub) -> dict:
    return {
        "id": uuid4().hex,
        "source": "external",
        "stream_index": None,
        "tool_track_id": None,
        "external_path": str(sub.path),
        "paired_path": str(sub.paired_path) if sub.paired_path else None,
        "codec": sub.ext.lstrip("."),
        "kind": sub.kind,
        "language_raw": None,
        "language_tag": sub.language_tag,
        "language_source": sub.language_source,
        "title": None,
        "is_default": False,
        "is_forced": sub.is_forced,
        "is_sdh": sub.is_sdh,
        "is_commentary": sub.is_commentary,
        "is_generated": sub.is_generated,
        "size_bytes": sub.size_bytes,
        "content_sha256": None,
    }


def build_track_dicts(probe_result: probe.ProbeResult | None, externals: list) -> list[dict]:
    """Unify embedded + external subtitles into one ordered list of track dicts."""
    tracks: list[dict] = []
    if probe_result is not None:
        tracks.extend(_embedded_to_dict(s) for s in probe_result.subtitles)
    tracks.extend(_external_to_dict(s) for s in externals)
    return tracks


async def scan_inventory(db: AsyncSession, resolved: ResolvedMediaFile) -> SubtitleInventory:
    """Probe + discover + persist the current inventory for a media file."""
    probe_result = await asyncio.to_thread(probe.probe_container, resolved.path)
    externals = await asyncio.to_thread(external.discover, resolved.path)
    tracks = build_track_dicts(probe_result, externals)

    container = probe_result.container if probe_result else resolved.container
    family = capabilities.container_family(container)
    cov = coverage.compute_coverage(
        tracks,
        probe_result.audio_streams if probe_result else [],
        preferred_languages=subtitle_settings.SUBTITLE_PREFERRED_LANGUAGES,
        preferred_audio_languages=subtitle_settings.SUBTITLE_PREFERRED_AUDIO_LANGUAGES,
        preferred_subtitle_languages=subtitle_settings.SUBTITLE_PREFERRED_SUBTITLE_LANGUAGES,
    )

    inventory = (
        await db.execute(
            select(SubtitleInventory).where(
                SubtitleInventory.media_file_id == resolved.media_file_id
            )
        )
    ).scalar_one_or_none()
    if inventory is None:
        inventory = SubtitleInventory(media_file_id=resolved.media_file_id)
        db.add(inventory)
    else:
        # Replace tracks transactionally on rescan.
        await db.execute(delete(SubtitleTrack).where(SubtitleTrack.inventory_id == inventory.id))

    inventory.file_signature = resolved.signature
    inventory.container = family
    inventory.duration_seconds = probe_result.duration_seconds if probe_result else None
    inventory.audio_streams_json = json.dumps(probe_result.audio_streams if probe_result else [])
    inventory.chapters_count = probe_result.chapters_count if probe_result else 0
    inventory.attachments_count = probe_result.attachments_count if probe_result else 0
    inventory.coverage_json = json.dumps(cov)
    inventory.probe_tool_versions_json = json.dumps(binaries.availability())
    inventory.error = None if probe_result else "probe_failed"
    await db.flush()  # assign inventory.id for new rows

    for track in tracks:
        db.add(SubtitleTrack(inventory_id=inventory.id, **track))
    await db.commit()
    await db.refresh(inventory)
    return inventory


async def _tracks_for(db: AsyncSession, inventory_id: int) -> list[SubtitleTrack]:
    return list(
        (await db.execute(select(SubtitleTrack).where(SubtitleTrack.inventory_id == inventory_id)))
        .scalars()
        .all()
    )


async def get_inventory_dict(
    db: AsyncSession,
    media_file_id: int,
    *,
    force: bool = False,
    preferred_audio_languages: list[str] | None = None,
    preferred_subtitle_languages: list[str] | None = None,
) -> dict:
    """Return the cached inventory when its signature still matches, else rescan.

    Single-file detail requests refresh inline (expected to be fast); bulk
    callers should prefer enqueuing a scan job instead.
    """
    resolved = await resolve_media_file(db, media_file_id)
    inventory = (
        await db.execute(
            select(SubtitleInventory).where(SubtitleInventory.media_file_id == media_file_id)
        )
    ).scalar_one_or_none()

    if not force and inventory is not None and inventory.file_signature == resolved.signature:
        tracks = await _tracks_for(db, inventory.id)
        result = inventory_to_dict(
            inventory,
            tracks,
            preferred_audio_languages=preferred_audio_languages,
            preferred_subtitle_languages=preferred_subtitle_languages,
        )
        result["stale"] = False
        return result

    inventory = await scan_inventory(db, resolved)
    tracks = await _tracks_for(db, inventory.id)
    result = inventory_to_dict(
        inventory,
        tracks,
        preferred_audio_languages=preferred_audio_languages,
        preferred_subtitle_languages=preferred_subtitle_languages,
    )
    result["stale"] = False
    return result


async def get_track(db: AsyncSession, media_file_id: int, track_id: str) -> SubtitleTrack | None:
    """Look up a track by id, scoped to the media file's current inventory."""
    inventory = (
        await db.execute(
            select(SubtitleInventory).where(SubtitleInventory.media_file_id == media_file_id)
        )
    ).scalar_one_or_none()
    if inventory is None:
        return None
    return (
        await db.execute(
            select(SubtitleTrack).where(
                SubtitleTrack.id == track_id,
                SubtitleTrack.inventory_id == inventory.id,
            )
        )
    ).scalar_one_or_none()


def inventory_to_dict(
    inventory: SubtitleInventory,
    tracks: list[SubtitleTrack],
    *,
    preferred_audio_languages: list[str] | None = None,
    preferred_subtitle_languages: list[str] | None = None,
) -> dict:
    """API shape for an inventory + its tracks (+ capabilities + coverage)."""
    family = capabilities.container_family(inventory.container)
    caps = capabilities.capabilities_for(family)
    stored_coverage = json.loads(inventory.coverage_json) if inventory.coverage_json else {}
    audio_streams = json.loads(inventory.audio_streams_json) if inventory.audio_streams_json else []
    if stored_coverage and "audio_channels_by_language" not in stored_coverage:
        by_language: dict[str, list[str]] = {}
        for stream in audio_streams:
            lang = stream.get("language_tag") or "und"
            label = stream.get("channel_label") or coverage.channel_label(stream)
            if label:
                by_language.setdefault(lang, [])
                if label not in by_language[lang]:
                    by_language[lang].append(label)
        stored_coverage["audio_channels_by_language"] = {
            lang: sorted(labels) for lang, labels in by_language.items()
        }
    effective_coverage = coverage.apply_preferences(
        stored_coverage,
        preferred_languages=subtitle_settings.SUBTITLE_PREFERRED_LANGUAGES,
        preferred_audio_languages=(
            preferred_audio_languages
            if preferred_audio_languages is not None
            else subtitle_settings.SUBTITLE_PREFERRED_AUDIO_LANGUAGES
        ),
        preferred_subtitle_languages=(
            preferred_subtitle_languages
            if preferred_subtitle_languages is not None
            else subtitle_settings.SUBTITLE_PREFERRED_SUBTITLE_LANGUAGES
        ),
    )
    return {
        "inventory_id": inventory.id,
        "media_file_id": inventory.media_file_id,
        "container": inventory.container,
        "container_family": family,
        "duration_seconds": inventory.duration_seconds,
        "chapters_count": inventory.chapters_count,
        "attachments_count": inventory.attachments_count,
        "capabilities": caps,
        "coverage": effective_coverage,
        "audio_streams": audio_streams,
        "scanned_at": inventory.scanned_at.isoformat() if inventory.scanned_at else None,
        "error": inventory.error,
        "tracks": [_track_to_dict(t, caps) for t in tracks],
    }


def _track_to_dict(track: SubtitleTrack, caps: dict) -> dict:
    # Per-track available actions + reasons (frontend renders them directly).
    actions: dict[str, dict] = {}
    actions["remove"] = {"available": caps["can_remove"], "reason": caps["can_remove_reason"]}
    if track.source == "external":
        embed_ok, embed_reason = (
            (caps["can_embed_text"], caps["can_embed_text_reason"])
            if track.kind == "text"
            else (caps["can_embed_image"], caps["can_embed_image_reason"])
        )
        actions["embed"] = {"available": embed_ok, "reason": embed_reason}
        actions["delete_external"] = {"available": True, "reason": None}
    else:
        actions["extract"] = {"available": caps["can_extract"], "reason": None}
        actions["edit_metadata"] = {"available": caps["can_edit_metadata"], "reason": None}
    return {
        "id": track.id,
        "source": track.source,
        "stream_index": track.stream_index,
        "tool_track_id": track.tool_track_id,
        "codec": track.codec,
        "codec_label": codec_label(track.codec),
        "kind": track.kind,
        "kind_label": _KIND_LABELS.get(track.kind, track.kind.title()),
        "language_raw": track.language_raw,
        "language_tag": track.language_tag,
        "language_source": track.language_source,
        "title": track.title,
        "is_default": track.is_default,
        "is_forced": track.is_forced,
        "is_sdh": track.is_sdh,
        "is_commentary": track.is_commentary,
        "is_generated": track.is_generated,
        "size_bytes": track.size_bytes,
        "text_previewable": track.kind == "text",
        "actions": actions,
    }


def text_preview(path: Path | str, *, max_cues: int | None = None) -> dict:
    """First N text cues for SRT/ASS/SSA/VTT; a notice for bitmap subtitles."""
    path = Path(path)
    ext = path.suffix.lower()
    if ext in external.BITMAP_EXTS or ext == ".sub":
        return {"previewable": False, "reason": "bitmap subtitle - text preview unavailable"}

    max_cues = max_cues or subtitle_settings.SUBTITLE_PREVIEW_MAX_CUES
    try:
        import pysubs2  # noqa: PLC0415
        from charset_normalizer import from_path  # noqa: PLC0415

        best = from_path(str(path)).best()
        encoding = best.encoding if best else "utf-8"
        subs = pysubs2.load(str(path), encoding=encoding)
    except Exception as exc:  # noqa: BLE001
        return {"previewable": False, "reason": f"could not parse subtitle: {exc}"}

    cues = [
        {
            "start_ms": ev.start,
            "end_ms": ev.end,
            "text": ev.plaintext[:200],
        }
        for ev in subs.events[:max_cues]
        if ev.is_comment is False
    ]
    return {
        "previewable": True,
        "encoding": encoding,
        "cue_count": len(subs.events),
        "cues": cues,
    }
