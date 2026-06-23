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

from marquee.core.media_files import ResolvedMediaFile, compute_signature, resolve_media_file
from marquee.core.subtitles import capabilities, coverage, probe, service, validation
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


def _temp_output_path(source: Path, job_id: str) -> Path:
    return source.with_name(f".{source.name}.marquee.{job_id}.partial{source.suffix}")


def _backup_dir(source: Path, source_key: str) -> Path:
    """Hidden, same-filesystem backup dir outside the title folder (§16.7)."""
    # Walk up to the configured media root; fall back to the parent's parent.
    from marquee.config import settings  # noqa: PLC0415

    root = None
    resolved = str(source)
    for candidate in settings.effective_media_roots:
        if resolved.startswith(str(candidate)):
            root = Path(candidate)
            break
    if root is None:
        root = source.parent.parent
    safe_key = source_key.replace(":", "_").replace("/", "_")
    return root / ".marquee" / "backups" / safe_key


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

    if operation == "subtitle_remove":
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
        if remove_ids and not after_tracks:
            warnings.append({"code": "all_subtitles_removed", "requires_override": True})

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
        after_tracks = list(tracks)  # metadata edits don't change coverage shape much
        if not caps["can_edit_metadata"]:
            warnings.append({"code": "metadata_edit_unsupported", "requires_override": False})

    else:
        raise PlanError(f"unsupported plan operation: {operation}")

    audio = inventory.get("audio_streams", [])
    cov_before = inventory.get("coverage", {})
    cov_after = coverage.compute_coverage(
        after_tracks, audio, preferred_languages=subtitle_settings.SUBTITLE_PREFERRED_LANGUAGES
    )

    free_bytes = (
        shutil.disk_usage(resolved.path.parent).free if resolved.path.parent.exists() else None
    )
    blocking_codes = {
        "container_not_writable",
        "mkv_track_ids_unavailable",
    }
    can_execute = not any(w.get("code") in blocking_codes for w in warnings)

    return {
        "operation": operation,
        "before": {"tracks": tracks, "coverage": cov_before},
        "after": {"tracks": after_tracks, "coverage": cov_after},
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


# ---------------------------------------------------------------------------
# Execution transaction (real binaries required — not executed in CI/sandbox)
# ---------------------------------------------------------------------------


async def execute_job(db: AsyncSession, job, emit) -> dict:
    """Run a confirmed mutation job end-to-end. Requires ffmpeg/mkvtoolnix."""
    request = json.loads(job.request_json) if job.request_json else {}
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

    family = capabilities.container_family(source_probe.container)
    adapter = adapter_for(family)
    out = _temp_output_path(resolved.path, job.job_id)

    argv, expected_delta, external_removals = await _build_argv(
        db, job, operation, request, source_probe, adapter, out, resolved
    )

    if argv is not None:
        await emit(db, job.job_id, "remux", "start", message=f"Running {adapter.binary}")
        binary, args = argv
        binary_path = binaries.resolve(binary) or binary
        cmd_args = list(args)

        # Prefix execution with nice and ionice if available to prioritize system and web server stability
        nice_bin = shutil.which("nice")
        ionice_bin = shutil.which("ionice")

        executable = binary_path
        if nice_bin or ionice_bin:
            run_args = []
            if nice_bin:
                run_args += [nice_bin, "-n", "19"]
            if ionice_bin:
                run_args += [ionice_bin, "-c", "3"]
            run_args.append(binary_path)
            run_args.extend(cmd_args)
            executable = run_args[0]
            cmd_args = run_args[1:]

        proc = await asyncio.create_subprocess_exec(
            executable,
            *cmd_args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
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

        await emit(db, job.job_id, "validate", "start")
        result = await asyncio.to_thread(
            validation.validate_output,
            source_probe,
            out,
            expected_subtitle_delta=expected_delta,
        )
        if not result.ok:
            out.unlink(missing_ok=True)
            raise PreflightError("validation_failed", "; ".join(result.problems))

        if backup_requested:
            await _make_backup(db, job, resolved)

        await emit(db, job.job_id, "replace", "start")
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
        "external_removed": external_result,
    }


async def _build_argv(db, job, operation, request, source_probe, adapter, out, resolved):
    """Translate a job request into (binary, args) + the expected subtitle delta."""
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

    if operation == "subtitle_remove":
        selected = [by_id[t] for t in request.get("track_ids", []) if t in by_id]
        remove = [t for t in selected if t.source == "embedded"]
        external_remove = [t for t in selected if t.source == "external"]
        if not remove:
            return None, 0, external_remove
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
        )
        return (
            (adapter.binary, adapter.build_remove(resolved.path, out, plan)),
            -len(remove),
            external_remove,
        )

    if operation == "subtitle_embed":
        sources = [
            EmbedSource(
                path=Path(by_id[t].external_path),
                language_tag=by_id[t].language_tag,
                title=by_id[t].title,
                is_forced=by_id[t].is_forced,
                is_sdh=by_id[t].is_sdh,
                kind=by_id[t].kind,
            )
            for t in request.get("track_ids", [])
            if t in by_id and by_id[t].external_path
        ]
        return (adapter.binary, adapter.build_embed(resolved.path, out, sources)), len(sources), []

    if operation == "subtitle_metadata":
        edits = [
            MetadataEdit(
                track_ref=e.get("tool_track_id")
                if e.get("tool_track_id") is not None
                else e.get("stream_index", 0),
                language_tag=e.get("language_tag"),
                title=e.get("title"),
                is_default=e.get("is_default"),
                is_forced=e.get("is_forced"),
                is_sdh=e.get("is_sdh"),
            )
            for e in request.get("edits", [])
        ]
        return (adapter.binary, adapter.build_metadata(resolved.path, out, edits)), 0, []

    raise PreflightError("unsupported_operation", operation)


async def _make_backup(db: AsyncSession, job, resolved: ResolvedMediaFile) -> None:
    row = (
        await db.execute(select(MediaFile).where(MediaFile.id == resolved.media_file_id))
    ).scalar_one_or_none()
    source_key = row.source_key if row else f"file-{resolved.media_file_id}"
    backup_dir = _backup_dir(resolved.path, source_key) / job.job_id

    def do_backup():
        backup_dir.mkdir(parents=True, exist_ok=True)
        try:
            os.link(resolved.path, backup_path)  # cheap same-fs hardlink
        except OSError:
            shutil.copy2(resolved.path, backup_path)

    backup_path = backup_dir / resolved.path.name
    await asyncio.to_thread(do_backup)
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
