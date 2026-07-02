"""Mutation planning + execution transaction (design §23).

``build_plan`` is pure-ish (DB read + a disk-space stat, no media writes): it
produces the before/after preview, warnings, and storage estimate the UI shows
and the job stores. ``execute_job`` is the careful write path — preflight →
write to ``.partial`` beside the source → post-write validation → optional
backup → atomic ``os.replace`` → rescan. It NEVER writes in place and only runs
with real ffmpeg/mkvtoolnix present (validated, not executed, in this sandbox).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from marquee.core.jobs.child_tracking import clear_child_pid, record_child_pid
from marquee.core.media_files import ResolvedMediaFile, compute_signature, resolve_media_file
from marquee.core.subtitles import capabilities, coverage, languages, probe, service, validation
from marquee.core.subtitles.adapters import adapter_for
from marquee.core.subtitles.adapters.base import EmbedSource, MetadataEdit, RemovePlan
from marquee.core.subtitles.config import subtitle_settings
from marquee.media import binaries
from marquee.models import MediaBackup, MediaFile, SubtitleTrack

logger = logging.getLogger(__name__)


class PlanError(Exception):
    """Plan could not be built (bad track ids, unsupported op, etc.)."""


class PreflightError(Exception):
    """A structured preflight failure with a machine code."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


async def _raise_if_cancel_requested(db: AsyncSession, job, *, cleanup_paths: list[Path] | None = None):
    await db.refresh(job, ["cancel_requested"])
    if not job.cancel_requested:
        return
    for path in cleanup_paths or []:
        path.unlink(missing_ok=True)
    raise PreflightError("cancelled", "subtitle mutation cancelled")


def _temp_output_path(source: Path, job_id: str) -> Path:
    return source.with_name(f".{source.name}.marquee.{job_id}.partial{source.suffix}")


def _real_branch_root(source: Path) -> Path | None:
    """Real on-disk root backing ``source``, if it lives under a mergerfs union.

    mergerfs's create policy chooses which underlying branch a brand-new path
    lands on independently of which branch the file it's related to is
    already on, so a freshly-created backup dir can silently end up on a
    different physical disk than the source file — turning the "cheap
    hardlink" below into an always-falls-back full byte copy. Mergerfs
    exposes the true backing path via the ``user.mergerfs.basepath`` xattr;
    resolving it first makes hardlinking deterministic instead of a coin
    flip. A no-op (returns ``None``) on non-mergerfs filesystems.
    """
    import sys  # noqa: PLC0415

    if sys.platform != "linux":
        return None
    try:
        raw = os.getxattr(str(source), "user.mergerfs.basepath")
    except OSError:
        return None
    return Path(os.fsdecode(raw))


def _backup_dir(source: Path, source_key: str) -> Path:
    """Hidden, same-filesystem backup dir outside the title folder (§16.7)."""
    # Walk up to the configured media root; fall back to the parent's parent.
    from marquee.config import settings  # noqa: PLC0415

    root = _real_branch_root(source)
    if root is None:
        resolved = str(source)
        for candidate in settings.effective_media_roots:
            if resolved.startswith(str(candidate)):
                root = Path(candidate)
                break
        if root is None:
            root = source.parent.parent
    safe_key = source_key.replace(":", "_").replace("/", "_")
    return root / ".marquee" / "backups" / safe_key


def _nice_ionice_prefix() -> list[str]:
    """Best-effort low-priority prefix for heavy media I/O subprocesses.

    Without this, a full-priority backup/remux copy can saturate the disks
    the API's Postgres connection and even SSH share, stalling the whole
    host for the duration of a batch.
    """
    prefix: list[str] = []
    nice_bin = shutil.which("nice")
    if nice_bin:
        prefix += [nice_bin, "-n", "19"]
    ionice_bin = shutil.which("ionice")
    if ionice_bin:
        prefix += [ionice_bin, "-c", "3"]
    return prefix


async def link_or_copy(src: Path, dst: Path, *, bwlimit_kbps: int) -> str:
    """Materialize ``dst`` as a copy of ``src``, cheapest method first.

    Tries a same-filesystem hardlink (instant, zero extra bytes), then a
    reflink/COW clone (instant, shares blocks until either side is modified),
    then a deprioritized + bandwidth-capped full copy as the last resort.
    Returns which method won: ``"hardlink"``, ``"reflink"``, or ``"copy"``.
    """
    dst.parent.mkdir(parents=True, exist_ok=True)

    def try_link() -> bool:
        try:
            os.link(src, dst)
            return True
        except OSError:
            return False

    if await asyncio.to_thread(try_link):
        return "hardlink"

    prefix = _nice_ionice_prefix()
    cp_bin = shutil.which("cp")
    if cp_bin:
        proc = await asyncio.create_subprocess_exec(
            *prefix,
            cp_bin,
            "--reflink=always",
            str(src),
            str(dst),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        await record_child_pid(proc.pid)
        try:
            await proc.communicate()
        finally:
            await clear_child_pid(proc.pid)
        if proc.returncode == 0:
            return "reflink"
        dst.unlink(missing_ok=True)

    rsync_bin = shutil.which("rsync")
    if rsync_bin:
        proc = await asyncio.create_subprocess_exec(
            *prefix,
            rsync_bin,
            f"--bwlimit={bwlimit_kbps}",
            str(src),
            str(dst),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        await record_child_pid(proc.pid)
        try:
            _, stderr = await proc.communicate()
        finally:
            await clear_child_pid(proc.pid)
        if proc.returncode == 0:
            return "copy"
        dst.unlink(missing_ok=True)
        raise OSError((stderr or b"").decode(errors="replace")[:500])

    if cp_bin:
        proc = await asyncio.create_subprocess_exec(
            *prefix,
            cp_bin,
            str(src),
            str(dst),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        await record_child_pid(proc.pid)
        try:
            _, stderr = await proc.communicate()
        finally:
            await clear_child_pid(proc.pid)
        if proc.returncode == 0:
            return "copy"
        dst.unlink(missing_ok=True)
        raise OSError((stderr or b"").decode(errors="replace")[:500])

    # No external copy tools at all — last resort, unprioritized.
    await asyncio.to_thread(shutil.copy2, src, dst)
    return "copy"


def _as_bool(value) -> bool | None:
    if value is None:
        return None
    return bool(value)


def _apply_metadata_edits(
    tracks: list[dict],
    audio_streams: list[dict],
    edits: list[dict],
) -> tuple[list[dict], list[dict]]:
    """Return preview copies with user metadata/flag edits applied."""
    after_tracks = [dict(track) for track in tracks]
    after_audio = [dict(stream) for stream in audio_streams]
    tracks_by_id = {track["id"]: track for track in after_tracks}
    audio_by_index = {stream.get("index"): stream for stream in after_audio}

    for edit in edits:
        target = None
        if edit.get("track_id"):
            target = tracks_by_id.get(edit["track_id"])
        elif edit.get("stream_type") == "audio" or edit.get("audio_stream_index") is not None:
            target = audio_by_index.get(edit.get("audio_stream_index", edit.get("stream_index")))
        if target is None:
            continue

        if "language_tag" in edit:
            target["language_tag"] = edit["language_tag"]
        if "title" in edit:
            target["title"] = edit["title"]
        for field in ("is_default", "is_forced", "is_sdh", "is_commentary"):
            if field in edit:
                target[field] = _as_bool(edit[field])
                if "disposition" in target:
                    disposition_key = {
                        "is_default": "default",
                        "is_forced": "forced",
                        "is_sdh": "hearing_impaired",
                        "is_commentary": "comment",
                    }[field]
                    target["disposition"][disposition_key] = int(bool(edit[field]))

    return after_tracks, after_audio


def _stream_positions(source_probe: probe.ProbeResult) -> tuple[dict[int, int], dict[int, int]]:
    subtitle_positions = {
        stream.stream_index: idx
        for idx, stream in enumerate(source_probe.subtitles)
        if stream.stream_index is not None
    }
    audio_positions: dict[int, int] = {}
    for idx, stream in enumerate(source_probe.audio_streams):
        index = stream.get("index")
        if isinstance(index, int):
            audio_positions[index] = idx
    return subtitle_positions, audio_positions


def _metadata_track_ref(
    *,
    family: str,
    stream_type: str,
    stream_index: int | None,
    tool_track_id: int | None,
    subtitle_positions: dict[int, int],
    audio_positions: dict[int, int],
) -> int | None:
    if family == "mkv":
        return tool_track_id
    if stream_index is None:
        return None
    positions = audio_positions if stream_type == "audio" else subtitle_positions
    return positions.get(stream_index)


def _metadata_bool(edit: dict, source: dict, field: str) -> bool | None:
    if field in edit:
        return _as_bool(edit[field])
    if field in source:
        return _as_bool(source[field])
    disposition = source.get("disposition") or {}
    disposition_key = {
        "is_default": "default",
        "is_forced": "forced",
        "is_sdh": "hearing_impaired",
        "is_commentary": "comment",
    }[field]
    if disposition_key in disposition:
        return bool(disposition[disposition_key])
    return None


def _build_audio_reorder_args(
    src: Path,
    out: Path,
    source_probe: probe.ProbeResult,
    requested_order: list[int],
) -> list[str]:
    """Build an ffmpeg stream-copy remux that reorders audio streams only."""
    requested = list(dict.fromkeys(requested_order))
    args = ["-y", "-i", binaries.safe_media_path(src)]
    inserted_audio = False

    for stream in source_probe.streams:
        index = stream.get("index")
        if index is None:
            continue
        if stream.get("codec_type") == "audio":
            if inserted_audio:
                continue
            for audio_index in requested:
                args += ["-map", f"0:{audio_index}"]
            inserted_audio = True
            continue
        args += ["-map", f"0:{index}"]

    if not inserted_audio:
        for audio_index in requested:
            args += ["-map", f"0:{audio_index}"]
    args += ["-c", "copy", binaries.safe_media_path(out)]
    return args


# ---------------------------------------------------------------------------
# Planning (no media writes)
# ---------------------------------------------------------------------------


async def build_plan(
    db: AsyncSession,
    resolved: ResolvedMediaFile,
    inventory: dict,
    *,
    operation: str,
    params: dict,
    backup_requested: bool = False,
) -> dict:
    """Build the before/after plan for a mutation. Pure read + disk stat."""
    tracks = inventory["tracks"]
    by_id = {t["id"]: t for t in tracks}
    family = inventory["container_family"]
    caps = inventory["capabilities"]
    warnings: list[dict] = []
    after_tracks: list[dict]
    audio = inventory.get("audio_streams", [])
    after_audio: list[dict] = list(audio)

    if operation in ("audio_remove", "subtitle_remove", "track_remove"):
        remove_ids = set(params.get("track_ids", []))
        unknown = remove_ids - set(by_id)
        if unknown:
            raise PlanError(f"unknown track ids: {sorted(unknown)}")
        if not caps["can_remove"]:
            warnings.append({"code": caps["can_remove_reason"], "requires_override": False})
        if family == "mkv":
            missing_tool_ids = sorted(
                tid
                for tid in remove_ids
                if by_id[tid].get("source") == "embedded"
                and by_id[tid].get("tool_track_id") is None
            )
            if missing_tool_ids:
                warnings.append(
                    {
                        "code": "mkv_track_ids_unavailable",
                        "track_ids": missing_tool_ids,
                        "requires_override": False,
                    }
                )
        for tid in remove_ids:
            if by_id[tid].get("is_forced"):
                warnings.append(
                    {"code": "forced_track_selected", "track_id": tid, "requires_override": True}
                )
        after_tracks = [t for t in tracks if t["id"] not in remove_ids]
        if remove_ids and not after_tracks and tracks:
            warnings.append({"code": "all_subtitles_removed", "requires_override": True})

        # Audio stream removal validation and planning
        audio_remove_indices = set(params.get("audio_stream_indices", []))
        audio_indices = {a.get("index") for a in audio}
        unknown_audio = audio_remove_indices - audio_indices
        if unknown_audio:
            raise PlanError(f"unknown audio stream indices: {sorted(unknown_audio)}")

        after_audio = [a for a in audio if a.get("index") not in audio_remove_indices]
        if audio_remove_indices and not after_audio:
            warnings.append({"code": "all_audio_removed", "requires_override": True})

        if family == "mkv" and audio_remove_indices:
            missing_audio_tool_ids = sorted(
                a.get("index")
                for a in audio
                if a.get("index") in audio_remove_indices and a.get("tool_track_id") is None
            )
            if missing_audio_tool_ids:
                warnings.append(
                    {
                        "code": "mkv_audio_track_ids_unavailable",
                        "stream_indices": missing_audio_tool_ids,
                        "requires_override": False,
                    }
                )

    elif operation == "subtitle_embed":
        embed_ids = params.get("track_ids", [])
        after_tracks = list(tracks)
        for tid in embed_ids:
            track = by_id.get(tid)
            if track is None or track["source"] != "external":
                raise PlanError(f"{tid} is not an external track")
            ok, reason = capabilities.can_embed(family, kind=track["kind"])
            if not ok:
                warnings.append({"code": reason, "track_id": tid, "requires_override": False})
            after_tracks.append({**track, "source": "embedded"})

    elif operation == "subtitle_metadata":
        edits = params.get("edits", [])
        edit_track_ids = {e.get("track_id") for e in edits if e.get("track_id")}
        unknown = edit_track_ids - set(by_id)
        if unknown:
            raise PlanError(f"unknown track ids: {sorted(unknown)}")
        audio_indices = {a.get("index") for a in audio}
        edit_audio_indices = {
            e.get("audio_stream_index", e.get("stream_index"))
            for e in edits
            if e.get("stream_type") == "audio" or e.get("audio_stream_index") is not None
        }
        edit_audio_indices.discard(None)
        unknown_audio = edit_audio_indices - audio_indices
        if unknown_audio:
            raise PlanError(f"unknown audio stream indices: {sorted(unknown_audio)}")
        if not caps["can_edit_metadata"]:
            warnings.append({"code": "metadata_edit_unsupported", "requires_override": False})

        if family == "mkv":
            missing_track_ids = sorted(
                tid
                for tid in edit_track_ids
                if by_id[tid].get("source") == "embedded"
                and by_id[tid].get("tool_track_id") is None
            )
            if missing_track_ids:
                warnings.append(
                    {
                        "code": "mkv_metadata_track_ids_unavailable",
                        "track_ids": missing_track_ids,
                        "requires_override": False,
                    }
                )
            audio_by_index = {a.get("index"): a for a in audio}
            missing_audio_ids = sorted(
                index
                for index in edit_audio_indices
                if audio_by_index[index].get("tool_track_id") is None
            )
            if missing_audio_ids:
                warnings.append(
                    {
                        "code": "mkv_audio_metadata_track_ids_unavailable",
                        "stream_indices": missing_audio_ids,
                        "requires_override": False,
                    }
                )

        after_tracks, after_audio = _apply_metadata_edits(tracks, audio, edits)

    elif operation == "audio_reorder":
        requested_order = params.get("audio_stream_order", [])
        if not caps["can_remove"]:
            warnings.append({"code": caps["can_remove_reason"], "requires_override": False})
        audio_indices = [a.get("index") for a in audio if a.get("index") is not None]
        if not audio_indices:
            raise PlanError("no audio streams available to reorder")
        if len(requested_order) != len(audio_indices) or set(requested_order) != set(
            audio_indices
        ):
            raise PlanError("audio_stream_order must contain every audio stream index once")
        audio_by_index = {a.get("index"): a for a in audio}
        after_audio = [audio_by_index[index] for index in requested_order]
        after_tracks = list(tracks)

    else:
        raise PlanError(f"unsupported plan operation: {operation}")

    cov_before = inventory.get("coverage", {})
    cov_after = coverage.compute_coverage(
        after_tracks,
        after_audio,
        preferred_languages=subtitle_settings.SUBTITLE_PREFERRED_LANGUAGES,
        preferred_audio_languages=subtitle_settings.SUBTITLE_PREFERRED_AUDIO_LANGUAGES,
        preferred_subtitle_languages=subtitle_settings.SUBTITLE_PREFERRED_SUBTITLE_LANGUAGES,
    )

    free_bytes = (
        shutil.disk_usage(resolved.path.parent).free if resolved.path.parent.exists() else None
    )
    blocking_codes = {
        "container_not_writable",
        "mkv_track_ids_unavailable",
        "mkv_metadata_track_ids_unavailable",
        "mkv_audio_metadata_track_ids_unavailable",
    }
    can_execute = not any(w.get("code") in blocking_codes for w in warnings)

    return {
        "operation": operation,
        "before": {"tracks": tracks, "audio_streams": audio, "coverage": cov_before},
        "after": {"tracks": after_tracks, "audio_streams": after_audio, "coverage": cov_after},
        "warnings": warnings,
        "capabilities": {"can_execute": can_execute},
        "storage": {
            "source_bytes": resolved.size_bytes,
            "estimated_temp_bytes": resolved.size_bytes,
            "free_bytes": free_bytes,
            "backup_requested": backup_requested,
        },
        "hardlinked": resolved.is_hardlinked,
        "input_signature": resolved.signature,
        "confirmation_required": True,
    }


# ---------------------------------------------------------------------------
# Preflight (no media writes)
# ---------------------------------------------------------------------------


async def preflight(
    db: AsyncSession, media_file_id: int, *, saved_signature: str | None, allow_break: bool
) -> ResolvedMediaFile:
    """Re-validate everything immediately before execution (§23.2)."""
    resolved = await resolve_media_file(db, media_file_id)  # path validate + stat
    if not resolved.path.is_file():
        raise PreflightError("path_not_writable", "target is not a regular file")
    if saved_signature and resolved.signature != saved_signature:
        raise PreflightError("plan_stale", "file changed since the plan was created")
    if not os.access(resolved.path, os.W_OK):
        raise PreflightError("path_not_writable", "file is not writable")
    if resolved.is_hardlinked and not allow_break:
        raise PreflightError("hardlink_protected", f"file has {resolved.st_nlink} hardlinks")
    # Free space for a full replacement + margin.
    free = shutil.disk_usage(resolved.path.parent).free
    margin = resolved.size_bytes * (1 + subtitle_settings.SUBTITLE_TEMP_SPACE_MARGIN_PERCENT / 100)
    if free < margin:
        raise PreflightError("insufficient_space", f"need ~{int(margin)} bytes, have {free}")
    return resolved


def _track_summary(track: dict) -> dict:
    return {
        "id": track.get("id"),
        "source": track.get("source"),
        "language_tag": track.get("language_tag"),
        "title": track.get("title"),
        "kind": track.get("kind"),
        "stream_index": track.get("stream_index"),
        "tool_track_id": track.get("tool_track_id"),
        "is_default": track.get("is_default"),
        "is_forced": track.get("is_forced"),
        "is_sdh": track.get("is_sdh"),
        "is_commentary": track.get("is_commentary"),
        "external_path": track.get("external_path"),
    }


def _audio_summary(stream: dict) -> dict:
    return {
        "index": stream.get("index"),
        "language_tag": stream.get("language_tag"),
        "title": stream.get("title"),
        "codec_name": stream.get("codec_name"),
        "channels": stream.get("channels"),
        "channel_layout": stream.get("channel_layout"),
        "tool_track_id": stream.get("tool_track_id"),
        "is_default": stream.get("is_default"),
        "is_forced": stream.get("is_forced"),
        "is_sdh": stream.get("is_sdh"),
        "is_commentary": stream.get("is_commentary"),
    }


def _selection_counts(plan: dict | None) -> dict | None:
    if not isinstance(plan, dict):
        return None
    before = plan.get("before", {})
    after = plan.get("after", {})
    return {
        "subtitle_tracks_before": len(before.get("tracks", []) or []),
        "subtitle_tracks_after": len(after.get("tracks", []) or []),
        "audio_streams_before": len(before.get("audio_streams", []) or []),
        "audio_streams_after": len(after.get("audio_streams", []) or []),
    }


def _selection_result(operation: str, request: dict, plan: dict | None) -> dict | None:
    if not isinstance(plan, dict):
        return None

    before = plan.get("before", {})
    before_tracks = before.get("tracks", []) or []
    before_audio = before.get("audio_streams", []) or []
    track_by_id = {track.get("id"): track for track in before_tracks if track.get("id")}
    audio_by_index = {
        stream.get("index"): stream for stream in before_audio if stream.get("index") is not None
    }

    if operation in ("audio_remove", "subtitle_remove", "track_remove"):
        removed_track_ids = request.get("track_ids", []) or []
        removed_audio_indices = request.get("audio_stream_indices", []) or []
        return {
            "tracks_removed": [
                _track_summary(track_by_id[track_id])
                for track_id in removed_track_ids
                if track_id in track_by_id
            ],
            "audio_removed": [
                _audio_summary(audio_by_index[index])
                for index in removed_audio_indices
                if index in audio_by_index
            ],
            "counts": _selection_counts(plan),
        }

    if operation == "subtitle_embed":
        embed_ids = request.get("track_ids", []) or []
        return {
            "tracks_embedded": [
                _track_summary(track_by_id[track_id])
                for track_id in embed_ids
                if track_id in track_by_id
            ],
            "counts": _selection_counts(plan),
        }

    if operation == "subtitle_metadata":
        after = plan.get("after", {})
        after_tracks = {
            track.get("id"): track for track in (after.get("tracks", []) or []) if track.get("id")
        }
        after_audio = {
            stream.get("index"): stream
            for stream in (after.get("audio_streams", []) or [])
            if stream.get("index") is not None
        }
        track_edits: list[dict] = []
        audio_edits: list[dict] = []
        for edit in request.get("edits", []) or []:
            if edit.get("stream_type") == "audio" or edit.get("audio_stream_index") is not None:
                index = edit.get("audio_stream_index", edit.get("stream_index"))
                before_stream = audio_by_index.get(index, {})
                after_stream = after_audio.get(index, {})
                audio_edits.append(
                    {
                        "stream_index": index,
                        "before": _audio_summary(before_stream) if before_stream else None,
                        "after": _audio_summary(after_stream) if after_stream else None,
                    }
                )
                continue
            track_id = edit.get("track_id")
            before_track = track_by_id.get(track_id, {})
            after_track = after_tracks.get(track_id, {})
            track_edits.append(
                {
                    "track_id": track_id,
                    "before": _track_summary(before_track) if before_track else None,
                    "after": _track_summary(after_track) if after_track else None,
                }
            )
        return {
            "subtitle_edits": track_edits,
            "audio_edits": audio_edits,
            "counts": _selection_counts(plan),
        }

    if operation == "audio_reorder":
        return {
            "audio_stream_order_before": [
                stream.get("index") for stream in before_audio if stream.get("index") is not None
            ],
            "audio_stream_order_after": request.get("audio_stream_order", []) or [],
            "audio_streams": [_audio_summary(stream) for stream in before_audio],
            "counts": _selection_counts(plan),
        }

    return None


def _track_phrase(track: dict) -> str:
    """``"English subtitle (SDH)"`` from a plan-snapshot track dict."""
    lang = languages.display_name(track.get("language_tag"))
    qualifiers = [
        label
        for flag, label in (
            ("is_forced", "Forced"),
            ("is_sdh", "SDH"),
            ("is_commentary", "Commentary"),
        )
        if track.get(flag)
    ]
    if not qualifiers and track.get("title"):
        qualifiers = [str(track["title"])]
    suffix = f" ({', '.join(qualifiers)})" if qualifiers else ""
    return f"{lang} subtitle{suffix}"


def _describe_operation(operation: str, request: dict, plan: dict | None) -> str | None:
    """Human summary of what this mutation does, for the live progress bar.

    Best-effort: returns None (caller falls back to the generic message)
    whenever the plan snapshot is missing or malformed.
    """
    try:
        before = (plan or {}).get("before") or {}
        tracks_by_id = {
            t.get("id"): t for t in before.get("tracks") or [] if isinstance(t, dict)
        }
        audio_by_index = {
            a.get("index"): a for a in before.get("audio_streams") or [] if isinstance(a, dict)
        }
        if operation in ("audio_remove", "subtitle_remove", "track_remove"):
            parts = []
            subs = [t for t in (tracks_by_id.get(tid) for tid in request.get("track_ids") or []) if t]
            if subs:
                if len(subs) <= 2:
                    parts.append(" & ".join(_track_phrase(t) for t in subs))
                else:
                    langs = ", ".join(
                        sorted({languages.display_name(t.get("language_tag")) for t in subs})
                    )
                    parts.append(f"{len(subs)} subtitles ({langs})")
            audio = [
                a
                for a in (audio_by_index.get(i) for i in request.get("audio_stream_indices") or [])
                if a
            ]
            if audio:
                langs = sorted({languages.display_name(a.get("language_tag")) for a in audio})
                if len(audio) == 1:
                    parts.append(f"{langs[0]} audio")
                else:
                    parts.append(f"{len(audio)} audio streams ({', '.join(langs)})")
            return f"Removing {' & '.join(parts)}" if parts else None
        if operation == "subtitle_embed":
            subs = [t for t in (tracks_by_id.get(tid) for tid in request.get("track_ids") or []) if t]
            if not subs:
                return None
            if len(subs) <= 2:
                return "Embedding " + " & ".join(_track_phrase(t) for t in subs)
            return f"Embedding {len(subs)} subtitles"
        if operation == "subtitle_metadata":
            count = len(request.get("edits") or [])
            return f"Editing metadata on {count} track{'s' if count != 1 else ''}"
        if operation == "audio_reorder":
            count = len(request.get("audio_stream_order") or [])
            return f"Reordering {count} audio streams"
    except Exception:  # noqa: BLE001 — a describe bug must never kill a mutation
        logger.debug("could not describe %s operation", operation, exc_info=True)
    return None


def _plan_snapshot_tracks(plan: dict | None) -> dict[str, dict]:
    """Index the plan's before-tracks snapshot by the track id it was built with."""
    if not isinstance(plan, dict):
        return {}
    tracks = (plan.get("before") or {}).get("tracks") or []
    return {t.get("id"): t for t in tracks if isinstance(t, dict) and t.get("id")}


def _stable_track_key(source, stream_index, external_path) -> tuple | None:
    """Identity that survives inventory rescans (uuids do not)."""
    if source == "embedded" and stream_index is not None:
        return ("embedded", int(stream_index))
    if source == "external" and external_path:
        return ("external", str(external_path))
    return None


def _resolve_track_ids(track_ids, by_id: dict, plan: dict | None) -> dict:
    """Resolve requested track ids against the live inventory, surviving rescans.

    Every inventory rescan regenerates all SubtitleTrack uuids — even when the
    file is untouched — and a ``subtitle_scan`` job can run while this mutation
    waits for its ``media_write`` slot. Ids captured at plan time may therefore
    no longer exist at execution. Re-match them through the plan snapshot's
    stable identity (embedded → stream_index, external → sidecar path) and
    refuse to run if anything stays unresolved: silently skipping ids used to
    turn "remove eng+fra subtitles + eng audio" into an audio-only remux that
    still reported success.
    """
    resolved = {tid: by_id[tid] for tid in track_ids if tid in by_id}
    missing = [tid for tid in track_ids if tid not in by_id]
    if not missing:
        return resolved
    snapshot = _plan_snapshot_tracks(plan)
    current_by_key = {}
    for track in by_id.values():
        key = _stable_track_key(track.source, track.stream_index, track.external_path)
        if key is not None:
            current_by_key[key] = track
    unresolved = []
    for tid in missing:
        snap = snapshot.get(tid)
        key = (
            _stable_track_key(
                snap.get("source"), snap.get("stream_index"), snap.get("external_path")
            )
            if snap
            else None
        )
        track = current_by_key.get(key) if key is not None else None
        if track is None:
            unresolved.append(tid)
        else:
            resolved[tid] = track
    if unresolved:
        raise PreflightError(
            "plan_stale",
            "track ids no longer present — inventory changed since the plan "
            f"was created: {sorted(unresolved)}",
        )
    return resolved


# ---------------------------------------------------------------------------
# Execution transaction (real binaries required — not executed in CI/sandbox)
# ---------------------------------------------------------------------------


async def execute_job(db: AsyncSession, job, emit) -> dict:
    """Run a confirmed mutation job end-to-end. Requires ffmpeg/mkvtoolnix."""
    request = json.loads(job.request_json) if job.request_json else {}
    plan = json.loads(job.plan_json) if job.plan_json else None
    operation = job.operation
    allow_break = request.get("allow_break", False)
    backup_requested = request.get(
        "backup", subtitle_settings.SUBTITLE_BACKUP_MODE == "keep_original"
    )

    await emit(db, job.job_id, "preflight", "start")
    resolved = await preflight(
        db, job.media_file_id, saved_signature=job.input_signature, allow_break=allow_break
    )
    source_probe = await asyncio.to_thread(probe.probe_container, resolved.path)
    if source_probe is None:
        raise PreflightError("probe_failed", "could not probe source before mutation")
    await _raise_if_cancel_requested(db, job)

    family = capabilities.container_family(source_probe.container)
    adapter = adapter_for(family)
    out = _temp_output_path(resolved.path, job.job_id)

    argv, expected_delta, expected_audio_delta, external_removals = await _build_argv(
        db, job, operation, request, plan, source_probe, adapter, out, resolved
    )
    await _raise_if_cancel_requested(db, job, cleanup_paths=[out])

    if argv is not None:
        binary, args = argv
        description = _describe_operation(operation, request, plan)
        await emit(
            db,
            job.job_id,
            "remux",
            "start",
            message=f"{description} — {binary}" if description else f"Running {binary}",
        )
        binary_path = binaries.resolve(binary) or binary

        # Prefix execution with nice and ionice if available to prioritize system and web server stability
        run_args = [*_nice_ionice_prefix(), binary_path, *args]
        executable, cmd_args = run_args[0], run_args[1:]

        proc = await asyncio.create_subprocess_exec(
            executable,
            *cmd_args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await record_child_pid(proc.pid)
        try:
            _, stderr = await proc.communicate()
        finally:
            await clear_child_pid(proc.pid)
        if proc.returncode != 0:
            stderr_text = (stderr or b"").decode(errors="replace")[:500]
            logger.error(
                "%s remux failed (exit %d): %s",
                adapter.binary,
                proc.returncode,
                stderr_text or "(no output — possible crash or signal)",
            )
            out.unlink(missing_ok=True)
            raise PreflightError("remux_failed", stderr_text)
        await _raise_if_cancel_requested(db, job, cleanup_paths=[out])

        await emit(db, job.job_id, "validate", "start")
        result = await asyncio.to_thread(
            validation.validate_output,
            source_probe,
            out,
            expected_subtitle_delta=expected_delta,
            expected_audio_delta=expected_audio_delta,
        )
        if not result.ok:
            out.unlink(missing_ok=True)
            raise PreflightError("validation_failed", "; ".join(result.problems))
        await _raise_if_cancel_requested(db, job, cleanup_paths=[out])

        if backup_requested:
            await emit(db, job.job_id, "backup", "start")
            await _make_backup(db, job, resolved, emit)
            await _raise_if_cancel_requested(db, job, cleanup_paths=[out])

        await emit(db, job.job_id, "replace", "start")
        await _raise_if_cancel_requested(db, job, cleanup_paths=[out])
        os.replace(out, resolved.path)

    external_result = []
    if external_removals:
        external_result = await _remove_external_sidecars(
            db, job, resolved, external_removals, emit
        )

    # Refresh MediaFile metadata + rescan inventory before completing.
    media_file = (
        await db.execute(select(MediaFile).where(MediaFile.id == resolved.media_file_id))
    ).scalar_one_or_none()
    if media_file is not None:
        media_file.size_bytes = resolved.path.stat().st_size
        await db.commit()
    fresh = await resolve_media_file(db, resolved.media_file_id)
    await service.scan_inventory(db, fresh)
    await emit(db, job.job_id, "done", "complete")
    return {
        "path": str(resolved.path),
        "operation": operation,
        "request": request,
        "selection": _selection_result(operation, request, plan),
        "external_removed": external_result,
    }


async def _build_argv(db, job, operation, request, plan, source_probe, adapter, out, resolved):
    """Translate a job request into (binary, args) + the expected subtitle and audio deltas."""
    tracks = (
        (
            await db.execute(
                select(SubtitleTrack).where(SubtitleTrack.inventory_id == request["inventory_id"])
            )
        )
        .scalars()
        .all()
    )
    by_id = {t.id: t for t in tracks}

    # Heal missing tool_track_ids on the fly using fresh probe data if available
    probe_subs_by_index = {
        s.stream_index: s for s in source_probe.subtitles if s.stream_index is not None
    }
    for t in tracks:
        if t.source == "embedded" and t.tool_track_id is None:
            aligned = probe_subs_by_index.get(t.stream_index)
            if aligned and aligned.tool_track_id is not None:
                t.tool_track_id = aligned.tool_track_id

    if operation in ("audio_remove", "subtitle_remove", "track_remove"):
        requested_ids = request.get("track_ids", [])
        resolved_map = _resolve_track_ids(requested_ids, by_id, plan)
        selected = [resolved_map[t] for t in requested_ids]
        remove = [t for t in selected if t.source == "embedded"]
        external_remove = [t for t in selected if t.source == "external"]

        # Audio track deletion support
        audio_remove_indices = request.get("audio_stream_indices", [])
        probe_audio = source_probe.audio_streams if source_probe else []
        missing_audio = set(audio_remove_indices) - {
            a.get("index") for a in probe_audio if a.get("index") is not None
        }
        if missing_audio:
            raise PreflightError(
                "plan_stale",
                f"audio stream indices no longer present: {sorted(missing_audio)}",
            )

        remove_audio_tool_track_ids = []
        remove_audio_stream_indices = []
        keep_audio_tool_track_ids = []

        for aud in probe_audio:
            idx = aud.get("index")
            tid = aud.get("tool_track_id")
            if idx in audio_remove_indices:
                remove_audio_stream_indices.append(idx)
                if tid is not None:
                    remove_audio_tool_track_ids.append(tid)
            else:
                if tid is not None:
                    keep_audio_tool_track_ids.append(tid)

        if not remove and not remove_audio_stream_indices:
            return None, 0, 0, external_remove

        plan = RemovePlan(
            remove_tool_track_ids=[t.tool_track_id for t in remove if t.tool_track_id is not None],
            remove_stream_indices=[t.stream_index for t in remove if t.stream_index is not None],
            keep_tool_track_ids=[
                t.tool_track_id
                for t in tracks
                if t.source == "embedded"
                and t.id not in {r.id for r in remove}
                and t.tool_track_id is not None
            ],
            remove_audio_tool_track_ids=remove_audio_tool_track_ids,
            remove_audio_stream_indices=remove_audio_stream_indices,
            keep_audio_tool_track_ids=keep_audio_tool_track_ids,
        )
        return (
            (adapter.binary, adapter.build_remove(resolved.path, out, plan)),
            -len(remove),
            -len(remove_audio_stream_indices),
            external_remove,
        )

    if operation == "subtitle_embed":
        embed_ids = request.get("track_ids", [])
        resolved_map = _resolve_track_ids(embed_ids, by_id, plan)
        sources = []
        for t in embed_ids:
            track = resolved_map[t]
            if not track.external_path:
                raise PreflightError(
                    "plan_stale", f"track {t} is no longer an external sidecar"
                )
            sources.append(
                EmbedSource(
                    path=Path(track.external_path),
                    language_tag=track.language_tag,
                    title=track.title,
                    is_forced=track.is_forced,
                    is_sdh=track.is_sdh,
                    kind=track.kind,
                )
            )
        return (
            (adapter.binary, adapter.build_embed(resolved.path, out, sources)),
            len(sources),
            0,
            [],
        )

    if operation == "subtitle_metadata":
        family = capabilities.container_family(source_probe.container)
        subtitle_positions, audio_positions = _stream_positions(source_probe)
        source_audio_by_index = {
            stream.get("index"): stream
            for stream in source_probe.audio_streams
            if stream.get("index") is not None
        }
        subtitle_edit_ids = [
            e.get("track_id")
            for e in request.get("edits", [])
            if e.get("track_id")
            and e.get("stream_type") != "audio"
            and e.get("audio_stream_index") is None
        ]
        resolved_edit_map = _resolve_track_ids(subtitle_edit_ids, by_id, plan)
        edits: list[MetadataEdit] = []
        for edit in request.get("edits", []):
            if edit.get("stream_type") == "audio" or edit.get("audio_stream_index") is not None:
                stream_index = edit.get("audio_stream_index", edit.get("stream_index"))
                source_audio = source_audio_by_index.get(stream_index)
                if source_audio is None:
                    continue
                track_ref = _metadata_track_ref(
                    family=family,
                    stream_type="audio",
                    stream_index=stream_index,
                    tool_track_id=source_audio.get("tool_track_id"),
                    subtitle_positions=subtitle_positions,
                    audio_positions=audio_positions,
                )
                if track_ref is None:
                    raise PreflightError(
                        "metadata_track_ref_unavailable",
                        f"audio stream {stream_index} cannot be addressed for metadata edits",
                    )
                edits.append(
                    MetadataEdit(
                        track_ref=track_ref,
                        stream_type="audio",
                        language_tag=edit.get("language_tag"),
                        title=edit.get("title"),
                        is_default=_metadata_bool(edit, source_audio, "is_default"),
                        is_forced=_metadata_bool(edit, source_audio, "is_forced"),
                        is_sdh=_metadata_bool(edit, source_audio, "is_sdh"),
                        is_commentary=_metadata_bool(edit, source_audio, "is_commentary"),
                    )
                )
                continue

            track = resolved_edit_map.get(edit.get("track_id"))
            if track is None or track.source != "embedded":
                # External-track edits are DB-side; only embedded edits remux.
                continue
            track_ref = _metadata_track_ref(
                family=family,
                stream_type="subtitle",
                stream_index=track.stream_index,
                tool_track_id=track.tool_track_id,
                subtitle_positions=subtitle_positions,
                audio_positions=audio_positions,
            )
            if track_ref is None:
                raise PreflightError(
                    "metadata_track_ref_unavailable",
                    f"subtitle track {track.id} cannot be addressed for metadata edits",
                )
            source_track = {
                "is_default": track.is_default,
                "is_forced": track.is_forced,
                "is_sdh": track.is_sdh,
                "is_commentary": track.is_commentary,
            }
            edits.append(
                MetadataEdit(
                    track_ref=track_ref,
                    stream_type="subtitle",
                    language_tag=edit.get("language_tag"),
                    title=edit.get("title"),
                    is_default=_metadata_bool(edit, source_track, "is_default"),
                    is_forced=_metadata_bool(edit, source_track, "is_forced"),
                    is_sdh=_metadata_bool(edit, source_track, "is_sdh"),
                    is_commentary=_metadata_bool(edit, source_track, "is_commentary"),
                )
            )
        return (adapter.binary, adapter.build_metadata(resolved.path, out, edits)), 0, 0, []

    if operation == "audio_reorder":
        order = request.get("audio_stream_order", [])
        current_order = [stream.get("index") for stream in source_probe.audio_streams]
        if order == current_order:
            return None, 0, 0, []
        return ("ffmpeg", _build_audio_reorder_args(resolved.path, out, source_probe, order)), 0, 0, []

    raise PreflightError("unsupported_operation", operation)


async def _make_backup(db: AsyncSession, job, resolved: ResolvedMediaFile, emit) -> None:
    row = (
        await db.execute(select(MediaFile).where(MediaFile.id == resolved.media_file_id))
    ).scalar_one_or_none()
    source_key = row.source_key if row else f"file-{resolved.media_file_id}"
    backup_path = _backup_dir(resolved.path, source_key) / job.job_id / resolved.path.name

    started = asyncio.get_running_loop().time()
    method = await link_or_copy(
        resolved.path,
        backup_path,
        bwlimit_kbps=subtitle_settings.SUBTITLE_BACKUP_COPY_BWLIMIT_KBPS,
    )
    duration_s = round(asyncio.get_running_loop().time() - started, 1)
    await emit(
        db,
        job.job_id,
        "backup",
        "complete",
        message=f"backup via {method}",
        progress={"method": method, "bytes": resolved.size_bytes, "duration_s": duration_s},
    )
    db.add(
        MediaBackup(
            id=uuid4().hex,
            job_id=job.job_id,
            media_file_id=resolved.media_file_id,
            original_path=str(resolved.path),
            backup_path=str(backup_path),
            original_signature=resolved.signature,
            size_bytes=resolved.size_bytes,
            status="available",
        )
    )
    await db.commit()


async def _remove_external_sidecars(
    db: AsyncSession,
    job,
    resolved: ResolvedMediaFile,
    tracks: list[SubtitleTrack],
    emit,
) -> list[dict]:
    """Quarantine/delete external sidecars after confirmation."""
    await emit(db, job.job_id, "external", "start", message="removing external sidecars")
    removed: list[dict] = []
    parent = resolved.path.parent.resolve()
    mode = subtitle_settings.SUBTITLE_EXTERNAL_DELETE_MODE

    for track in tracks:
        paths = [Path(track.external_path)] if track.external_path else []
        if track.paired_path:
            paths.append(Path(track.paired_path))
        for raw_path in paths:
            path = raw_path.resolve()
            if path.parent != parent:
                raise PreflightError(
                    "external_path_escape", f"refusing external path outside media folder: {path}"
                )
            if not path.is_file():
                removed.append({"path": str(path), "status": "missing"})
                continue
            if mode == "delete":
                signature = compute_signature(path)
                size = path.stat().st_size
                path.unlink()
                removed.append(
                    {
                        "path": str(path),
                        "status": "deleted",
                        "size_bytes": size,
                        "signature": signature,
                    }
                )
                continue

            quarantine_dir = parent / ".marquee" / "quarantine" / job.job_id
            quarantine_dir.mkdir(parents=True, exist_ok=True)
            target = quarantine_dir / path.name
            if target.exists():
                target = quarantine_dir / f"{uuid4().hex}_{path.name}"
            signature = compute_signature(path)
            size = path.stat().st_size
            os.replace(path, target)
            db.add(
                MediaBackup(
                    id=uuid4().hex,
                    job_id=job.job_id,
                    media_file_id=resolved.media_file_id,
                    original_path=str(path),
                    backup_path=str(target),
                    original_signature=signature,
                    size_bytes=size,
                    status="available",
                )
            )
            removed.append(
                {
                    "path": str(path),
                    "status": "quarantined",
                    "quarantined_to": str(target),
                    "size_bytes": size,
                }
            )

    await db.commit()
    return removed


def signature_of(path: Path) -> str:
    """Public re-export for callers that only need the signature helper."""
    return compute_signature(path)


def plan_to_json(plan: dict) -> str:
    return json.dumps(
        plan, default=lambda o: asdict(o) if hasattr(o, "__dataclass_fields__") else str(o)
    )


def now_plus_ttl() -> datetime:
    from datetime import timedelta  # noqa: PLC0415

    return datetime.now(UTC) + timedelta(minutes=subtitle_settings.SUBTITLE_PLAN_TTL_MINUTES)
