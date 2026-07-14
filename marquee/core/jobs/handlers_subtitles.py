"""Migrated audio/subtitle inventory handler (JMC4B B3).

``subtitle_scan`` is a read-only media inspection of one media file.  It reuses the existing
subtitle probe/inventory domain but routes ffprobe/mkvmerge through the JMC3 tracked launcher
(confined workspace, process-group/cgroup containment) and writes only the derived
``SubtitleInventory`` projection.  It never mutates the media file or its sidecars.
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any

from sqlalchemy import func, select

from marquee.core.jobs.artifact_service import register_physical_artifact
from marquee.core.jobs.delivery import ExecutionContext, register_execution_handler
from marquee.core.jobs.process_launcher import ProcessLaunchError
from marquee.core.jobs.progress import MeasurementMode, ProgressMeasurementUpdate
from marquee.core.jobs.progress_service import ProgressObservation, progress_writer
from marquee.core.media_files import (
    MediaFileNotFoundError,
    MediaFileUnavailableError,
    resolve_media_file,
)
from marquee.core.subtitles.policy import evaluate_policy
from marquee.core.subtitles.service import inventory_to_dict, scan_inventory
from marquee.media import binaries
from marquee.models import EpisodeMediaFile, MediaFile, SubtitleInventory, SubtitleTrack

_AUDIT_SUBJECT_CAP = 200
_AUDIT_REPORT_BYTE_CAP = 900 * 1024

_FFPROBE_ARGS = (
    "-v",
    "error",
    "-print_format",
    "json",
    "-show_format",
    "-show_streams",
    "-show_chapters",
)


class _SubtitleScanError(RuntimeError):
    """Permanent, path-free failure for a media file that cannot be inventoried."""


async def _emit(context: ExecutionContext, *, stage_key: str, ordinal: int) -> None:
    await progress_writer.safe_write(
        job_id=context.delivery.canonical_job_id,
        attempt_id=context.attempt.attempt_id,
        fence_token=context.attempt.fence_token,
        observation=ProgressObservation(
            stage_key=stage_key,
            overall=ProgressMeasurementUpdate(
                scope_id="subtitle-scan:overall", mode=MeasurementMode.INDETERMINATE
            ),
            current=ProgressMeasurementUpdate(
                scope_id=f"subtitle-scan:{stage_key}", mode=MeasurementMode.INDETERMINATE
            ),
            producer_ordinal=ordinal,
        ),
    )


async def _run_tool_json(context: ExecutionContext, tool: str, args: list[str]) -> dict | None:
    """Run one allowlisted read-only tool through the tracked launcher; parse its JSON stdout."""
    process = await context.process_launcher.launch(tool, args)
    summary = await process.wait()
    if summary.exit_code != 0:
        return None
    try:
        return json.loads(summary.stdout.captured.decode("utf-8", "replace"))
    except (json.JSONDecodeError, ValueError):
        return None


async def execute_subtitle_scan(context: ExecutionContext) -> dict[str, Any]:
    """Inventory embedded/external audio+subtitle tracks for one media file (read-only)."""
    if context.cancellation.cancel_called:
        raise asyncio.CancelledError
    media_file_id = int(context.subject["media_file_id"])
    force = bool(context.request.get("force", False))

    async with context.session_factory() as db:
        try:
            resolved = await resolve_media_file(db, media_file_id)
        except MediaFileNotFoundError as exc:
            raise _SubtitleScanError("media file no longer exists") from exc
        except MediaFileUnavailableError as exc:
            raise _SubtitleScanError("media file is unavailable") from exc

        existing = await db.scalar(
            select(SubtitleInventory).where(
                SubtitleInventory.media_file_id == media_file_id
            )
        )
        if (
            not force
            and existing is not None
            and existing.file_signature == resolved.signature
            and existing.error is None
        ):
            return {
                "outcome": "no_change",
                "summary": {
                    "media_file_id": media_file_id,
                    "reason": "inventory_up_to_date",
                },
            }

        await _emit(context, stage_key="probing", ordinal=1)
        try:
            probe_json = await _run_tool_json(
                context, "ffprobe", [*_FFPROBE_ARGS, str(resolved.path)]
            )
        except ProcessLaunchError as exc:
            # Tool unavailable/uncontained: leave the prior valid inventory untouched.
            raise _SubtitleScanError("subtitle probe tool is unavailable") from exc
        if probe_json is None:
            # Probe failed: retain the prior valid inventory rather than overwriting it.
            raise _SubtitleScanError("media file could not be probed")

        mkvmerge_json: dict | None = None
        if str(resolved.path).lower().endswith(".mkv"):
            with_mkv = binaries.safe_media_path(str(resolved.path))
            mkvmerge_json = await _run_tool_json(context, "mkvmerge", ["-J", with_mkv])

        await _emit(context, stage_key="inventorying", ordinal=2)
        inventory = await scan_inventory(
            db, resolved, probe_json=probe_json, mkvmerge_json=mkvmerge_json
        )
        track_count = await db.scalar(
            select(func.count())
            .select_from(SubtitleTrack)
            .where(SubtitleTrack.inventory_id == inventory.id)
        )

    return {
        "outcome": "succeeded",
        "summary": {
            "media_file_id": media_file_id,
            "container": inventory.container,
            "tracks": int(track_count or 0),
            "signature_changed": existing is None
            or existing.file_signature != resolved.signature,
        },
    }


register_execution_handler("subtitle_scan", execute_subtitle_scan)


def _audit_scope_stmt(scope: str):
    stmt = select(SubtitleInventory, MediaFile).join(
        MediaFile, MediaFile.id == SubtitleInventory.media_file_id
    )
    if scope == "movies":
        stmt = stmt.where(MediaFile.movie_id.is_not(None))
    elif scope == "tv":
        stmt = stmt.where(
            SubtitleInventory.media_file_id.in_(select(EpisodeMediaFile.media_file_id))
        )
    return stmt.order_by(SubtitleInventory.media_file_id)


def _audit_scope_media_stmt(scope: str):
    stmt = select(MediaFile.id, MediaFile.is_active, MediaFile.is_present)
    if scope == "movies":
        stmt = stmt.where(MediaFile.movie_id.is_not(None))
    elif scope == "tv":
        stmt = stmt.where(MediaFile.id.in_(select(EpisodeMediaFile.media_file_id)))
    return stmt.order_by(MediaFile.id)


def _coverage_totals(evaluation: Any) -> dict[str, int]:
    """Keep audit coverage useful and bounded without exposing track documents."""
    before = evaluation.coverage_before
    after = evaluation.coverage_after
    return {
        "before_full_dialogue_subjects": int(bool(before.get("full_dialogue_languages"))),
        "after_full_dialogue_subjects": int(bool(after.get("full_dialogue_languages"))),
        "before_full_dialogue_languages": len(before.get("full_dialogue_languages", [])),
        "after_full_dialogue_languages": len(after.get("full_dialogue_languages", [])),
        "before_unknown_subjects": int(bool(before.get("unknown_present"))),
        "after_unknown_subjects": int(bool(after.get("unknown_present"))),
    }


async def _publish_policy_audit_report(
    context: ExecutionContext, report: dict[str, Any]
) -> None:
    """Publish a confined, bounded report without exposing paths or track documents."""
    staged, write_fd = context.workspace.staging_file("subtitle-policy-audit.json")
    payload = json.dumps(report, allow_nan=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    try:
        view = memoryview(payload)
        while view:
            written = await asyncio.to_thread(os.write, write_fd, view)
            if written <= 0:
                raise OSError("policy audit report write made no progress")
            view = view[written:]
        await asyncio.to_thread(os.fsync, write_fd)
    finally:
        os.close(write_fd)
    try:
        await register_physical_artifact(
            job_id=context.delivery.canonical_job_id,
            attempt_id=context.attempt.attempt_id,
            fence_token=context.attempt.fence_token,
            source=staged,
            kind="command_report",
            name="Subtitle policy audit report",
            content_type="application/json",
            metadata={"report": "subtitle_policy_audit", "version": 1},
        )
    finally:
        context.workspace.boundary.delete_file(staged, missing_ok=True)


async def execute_subtitle_policy_audit(context: ExecutionContext) -> dict[str, Any]:
    """Dry-run a subtitle policy over existing inventory (read-only; no plan, no mutation)."""
    if context.cancellation.cancel_called:
        raise asyncio.CancelledError
    policy_snapshot = dict(context.request.get("policy_snapshot") or {})
    scope = context.request.get("scope", "all")
    policy_id = context.request.get("policy_id")

    proposed_removals = 0
    protected_tracks = 0
    review_subjects = 0
    subjects_with_changes = 0
    subjects_scanned = 0
    warnings = 0
    per_subject: list[dict[str, Any]] = []
    report_subjects: list[dict[str, Any]] = []
    report_bytes = 2
    report_truncated = False
    coverage = {
        "before_full_dialogue_subjects": 0,
        "after_full_dialogue_subjects": 0,
        "before_full_dialogue_languages": 0,
        "after_full_dialogue_languages": 0,
        "before_unknown_subjects": 0,
        "after_unknown_subjects": 0,
    }
    missing_inventory = 0
    unavailable_media = 0

    await _emit_policy_stage(context, stage_key="selecting", ordinal=1)
    async with context.session_factory() as db:
        scoped_media = (await db.execute(_audit_scope_media_stmt(scope))).all()
        inventory_rows = (await db.execute(_audit_scope_stmt(scope))).all()
        inventory_media_ids = {inventory.media_file_id for inventory, _media in inventory_rows}
        missing_inventory = sum(media.id not in inventory_media_ids for media in scoped_media)
        await _emit_policy_stage(context, stage_key="evaluating", ordinal=2)
        for inventory, media in inventory_rows:
            if context.cancellation.cancel_called:
                raise asyncio.CancelledError
            if not media.is_active or not media.is_present or inventory.error is not None:
                unavailable_media += 1
                continue
            tracks = (
                await db.execute(
                    select(SubtitleTrack).where(SubtitleTrack.inventory_id == inventory.id)
                )
            ).scalars().all()
            inventory_dict = inventory_to_dict(inventory, list(tracks))
            evaluation = evaluate_policy(
                inventory_dict["tracks"], inventory_dict.get("audio_streams", []), policy_snapshot
            )
            subjects_scanned += 1
            proposed_removals += len(evaluation.removals)
            protected_tracks += len(evaluation.protected)
            warnings += len(evaluation.warnings)
            if evaluation.review_required:
                review_subjects += 1
            for key, value in _coverage_totals(evaluation).items():
                coverage[key] += value
            if evaluation.changes:
                subjects_with_changes += 1
                subject_report = {
                    "media_file_id": inventory.media_file_id,
                    "removals": len(evaluation.removals),
                    "protected": len(evaluation.protected),
                    "review_required": len(evaluation.review_required),
                    "coverage_before": {
                        "full_dialogue_languages": evaluation.coverage_before.get(
                            "full_dialogue_languages", []
                        ),
                        "unknown_present": bool(evaluation.coverage_before.get("unknown_present")),
                    },
                    "coverage_after": {
                        "full_dialogue_languages": evaluation.coverage_after.get(
                            "full_dialogue_languages", []
                        ),
                        "unknown_present": bool(evaluation.coverage_after.get("unknown_present")),
                    },
                    "warning_codes": sorted(
                        str(warning.get("code", "unknown")) for warning in evaluation.warnings
                    ),
                }
                if len(per_subject) < _AUDIT_SUBJECT_CAP:
                    per_subject.append({
                        key: subject_report[key]
                        for key in ("media_file_id", "removals", "protected", "review_required")
                    })
                encoded_subject = json.dumps(
                    subject_report, allow_nan=False, separators=(",", ":"), sort_keys=True
                ).encode("utf-8")
                if report_bytes + len(encoded_subject) + 1 <= _AUDIT_REPORT_BYTE_CAP:
                    report_subjects.append(subject_report)
                    report_bytes += len(encoded_subject) + 1
                else:
                    report_truncated = True

    report_needed = subjects_with_changes > _AUDIT_SUBJECT_CAP or report_truncated
    if report_needed:
        await _publish_policy_audit_report(
            context,
            {
                "policy_id": policy_id,
                "scope": scope,
                "subjects": report_subjects,
                "truncated": report_truncated,
                "version": 1,
            },
        )

    outcome = "no_change" if (proposed_removals == 0 and review_subjects == 0) else "succeeded"
    return {
        "outcome": outcome,
        "summary": {
            "policy_id": policy_id,
            "scope": scope,
            "subjects_scanned": subjects_scanned,
            "subjects_with_changes": subjects_with_changes,
            "proposed_removals": proposed_removals,
            "protected_tracks": protected_tracks,
            "review_required_subjects": review_subjects,
            "warnings": warnings,
            "coverage": coverage,
            "missing_inventory": missing_inventory,
            "unavailable_media": unavailable_media,
            "per_subject": per_subject,
            "per_subject_truncated": subjects_with_changes > len(per_subject),
            "full_report_registered": report_needed,
            "full_report_truncated": report_truncated,
        },
    }


async def _emit_policy_stage(context: ExecutionContext, *, stage_key: str, ordinal: int) -> None:
    await progress_writer.safe_write(
        job_id=context.delivery.canonical_job_id,
        attempt_id=context.attempt.attempt_id,
        fence_token=context.attempt.fence_token,
        observation=ProgressObservation(
            stage_key=stage_key,
            overall=ProgressMeasurementUpdate(
                scope_id="subtitle-policy-audit:overall", mode=MeasurementMode.INDETERMINATE
            ),
            current=ProgressMeasurementUpdate(
                scope_id=f"subtitle-policy-audit:{stage_key}", mode=MeasurementMode.INDETERMINATE
            ),
            producer_ordinal=ordinal,
        ),
    )


register_execution_handler("subtitle_policy_audit", execute_subtitle_policy_audit)
