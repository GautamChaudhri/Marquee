from __future__ import annotations

from collections.abc import Sequence

from marquee.core.jobs.audio_subtitle_documents import (
    AudioReorderRequestV1,
    MediaTrackMutationResultV1,
    SubtitleMetadataRequestV1,
    TrackRemoveRequestV1,
)
from marquee.core.jobs.delivery import ExecutionContext, register_execution_handler
from marquee.core.jobs.media_backups import create_media_backup
from marquee.core.jobs.media_mutation_support import TrackMutationError
from marquee.core.jobs.media_mutation_support import confined_boundary as _boundary
from marquee.core.jobs.media_mutation_support import container_supported as _supported
from marquee.core.jobs.media_mutation_support import current_signature as _signature
from marquee.core.jobs.media_mutation_support import load_media_file as _load
from marquee.core.jobs.media_mutation_support import physical as _physical
from marquee.core.jobs.media_mutation_support import probe_inventory as _inventory
from marquee.core.jobs.mkvmerge_plan import (
    MkvmergePlanError,
    build_metadata_args,
    build_remove_args,
    build_reorder_args,
)
from marquee.core.jobs.mutation_documents import (
    MutationJobOutcome,
    MutationTargetOutcomeV1,
    MutationTargetStatus,
    MutationTargetV1,
    MutationValidationV1,
)
from marquee.core.jobs.publication import file_signature
from marquee.core.jobs.remux_coordinator import (
    RemuxCancelledError,
    RemuxError,
    atomicity,
    failed_result,
    no_change_result,
    publish_media_candidate,
    run_mkvmerge_plan,
    run_mkvpropedit_plan,
    target_for,
)
from marquee.core.jobs.track_selectors import (
    TrackEntryV1,
    TrackInventoryV1,
    TrackSelectorError,
    TrackSelectorV1,
    resolve_all,
)
from marquee.core.media_files import MediaFileNotFoundError, MediaFileUnavailableError

_METADATA_PROPERTIES = {
    "language_tag": "language",
    "title": "name",
    "is_default": "flag-default",
    "is_forced": "flag-forced",
    "is_hearing_impaired": "flag-hearing-impaired",
}




def _resolve(
    selectors: Sequence[TrackSelectorV1], inventory: TrackInventoryV1
) -> tuple[TrackEntryV1, ...]:
    return resolve_all(list(selectors), inventory)


def _succeeded(
    targets: Sequence[MutationTargetV1], *, stage: str, message: str
) -> tuple[MutationTargetOutcomeV1, ...]:
    return tuple(
        MutationTargetOutcomeV1(
            target=target,
            status=MutationTargetStatus.SUCCEEDED,
            stage=stage,
            reason_code="applied",
            message=message,
            bytes_changed=True,
            product_state_changed=True,
        )
        for target in targets
    )


async def _run_remux(
    context: ExecutionContext,
    *,
    media_file_id: int,
    selectors: Sequence[TrackSelectorV1],
    operation: str,
    build,
) -> dict[str, object]:
    """Shared removal/reorder path: plan → stage → validate → backup → publish → rescan."""
    group_id = f"{operation}:{media_file_id}"
    try:
        resolved_file = await _load(context, media_file_id)
    except (MediaFileNotFoundError, MediaFileUnavailableError) as exc:
        raise TrackMutationError("the media file is unavailable") from exc

    source_path = resolved_file.path
    before = _inventory(source_path, resolved_file.signature)
    boundary, source = _boundary(source_path)

    try:
        entries = _resolve(selectors, before)
    except TrackSelectorError as exc:
        targets = [
            MutationTargetV1(
                key=selector.track_key,
                kind="track",
                label=f"{selector.facts.kind.title()} {selector.facts.language_tag}",
                operation=operation,
                selector_facts=selector.facts.model_dump(mode="json"),
            )
            for selector in selectors
        ]
        return failed_result(
            targets=targets,
            before=before,
            stage="resolve",
            reason_code=exc.reason,
            message="the requested tracks no longer match the source",
            group_id=group_id,
        ).model_dump(mode="json")

    targets = [target_for(entry, operation) for entry in entries]

    if not _supported(before):
        return failed_result(
            targets=targets,
            before=before,
            stage="preflight",
            reason_code="unsupported_container",
            message="this container cannot preserve the requested track model",
            group_id=group_id,
        ).model_dump(mode="json")

    candidate = boundary.from_key("media", f".marquee-{context.attempt.attempt_id}-{source.key.value}")
    try:
        args = build(before, entries, source_path, candidate)
    except MkvmergePlanError as exc:
        return failed_result(
            targets=targets,
            before=before,
            stage="preflight",
            reason_code="plan_rejected",
            message=str(exc),
            group_id=group_id,
        ).model_dump(mode="json")

    try:
        await run_mkvmerge_plan(context, boundary=boundary, args=args, candidate=candidate)
    except RemuxCancelledError as exc:
        boundary.delete_file(candidate, missing_ok=True)
        return failed_result(
            targets=targets,
            before=before,
            stage=exc.stage,
            reason_code="cancelled",
            message="the remux was cancelled before publication",
            group_id=group_id,
        ).model_dump(mode="json")
    except RemuxError as exc:
        boundary.delete_file(candidate, missing_ok=True)
        return failed_result(
            targets=targets,
            before=before,
            stage=exc.stage,
            reason_code=exc.reason_code,
            message=str(exc),
            group_id=group_id,
        ).model_dump(mode="json")

    try:
        after_candidate = _inventory(_physical(candidate), "sha256:candidate")
        if not _supported(after_candidate):
            raise TrackMutationError("the candidate lost its container model")

        source_signature = file_signature(boundary, source)
        backup = create_media_backup(
            boundary,
            source=source,
            subject_key=f"media-file-{media_file_id}",
            signature=source_signature,
            suffix=source_path.suffix.lstrip("."),
        )
        await publish_media_candidate(
            context,
            boundary=boundary,
            candidate=candidate,
            destination=source,
            expected_destination=source_signature,
        )
    except Exception as exc:  # noqa: BLE001 - classified below
        boundary.delete_file(candidate, missing_ok=True)
        return failed_result(
            targets=targets,
            before=before,
            stage="publish",
            reason_code="publish_failed",
            message=str(exc)[:200],
            group_id=group_id,
        ).model_dump(mode="json")

    # B13: the post-operation probe is authoritative for what actually changed.
    actual = _inventory(source_path, _signature(source_path))
    result = MediaTrackMutationResultV1(
        outcome=MutationJobOutcome.SUCCEEDED,
        reason_code="applied",
        message=f"{operation} published and rescanned",
        requested_targets=tuple(targets),
        target_outcomes=_succeeded(targets, stage="rescan", message="verified by rescan"),
        validation=MutationValidationV1(verdict="passed"),
        atomicity=atomicity(group_id, published=True),
        backup=backup,
        before_inventory=before,
        actual_inventory=actual,
    )
    return result.model_dump(mode="json")


async def _execute_remove(context: ExecutionContext, operation: str) -> dict[str, object]:
    request = TrackRemoveRequestV1.model_validate(context.request)

    def build(inventory, entries, source_path, candidate):
        return build_remove_args(
            source=str(source_path),
            destination=str(_physical(candidate)),
            inventory=inventory,
            removed=entries,
        )

    return await _run_remux(
        context,
        media_file_id=request.media_file_id,
        selectors=request.selectors,
        operation=operation,
        build=build,
    )


async def execute_audio_remove(context: ExecutionContext) -> dict[str, object]:
    return await _execute_remove(context, "audio_remove")


async def execute_track_remove(context: ExecutionContext) -> dict[str, object]:
    return await _execute_remove(context, "track_remove")


async def execute_subtitle_remove(context: ExecutionContext) -> dict[str, object]:
    return await _execute_remove(context, "subtitle_remove")


async def execute_audio_reorder(context: ExecutionContext) -> dict[str, object]:
    request = AudioReorderRequestV1.model_validate(context.request)

    def build(inventory, entries, source_path, candidate):
        return build_reorder_args(
            source=str(source_path),
            destination=str(_physical(candidate)),
            inventory=inventory,
            ordered_audio=entries,
        )

    return await _run_remux(
        context,
        media_file_id=request.media_file_id,
        selectors=request.ordered_selectors,
        operation="audio_reorder",
        build=build,
    )


async def execute_subtitle_metadata(context: ExecutionContext) -> dict[str, object]:
    """Edit metadata on an owned candidate, then publish through the coordinator."""
    request = SubtitleMetadataRequestV1.model_validate(context.request)
    media_file_id = request.media_file_id
    group_id = f"subtitle_metadata:{media_file_id}"
    try:
        resolved_file = await _load(context, media_file_id)
    except (MediaFileNotFoundError, MediaFileUnavailableError) as exc:
        raise TrackMutationError("the media file is unavailable") from exc

    source_path = resolved_file.path
    before = _inventory(source_path, resolved_file.signature)
    boundary, source = _boundary(source_path)

    selectors = [edit.selector for edit in request.edits]
    try:
        entries = _resolve(selectors, before)
    except TrackSelectorError as exc:
        targets = [
            MutationTargetV1(
                key=selector.track_key,
                kind="track",
                label=f"Subtitle {selector.facts.language_tag}",
                operation="subtitle_metadata",
                selector_facts=selector.facts.model_dump(mode="json"),
            )
            for selector in selectors
        ]
        return failed_result(
            targets=targets,
            before=before,
            stage="resolve",
            reason_code=exc.reason,
            message="the requested tracks no longer match the source",
            group_id=group_id,
        ).model_dump(mode="json")

    targets = [target_for(entry, "subtitle_metadata") for entry in entries]
    if not _supported(before):
        return failed_result(
            targets=targets,
            before=before,
            stage="preflight",
            reason_code="unsupported_container",
            message="this container cannot carry the requested metadata",
            group_id=group_id,
        ).model_dump(mode="json")

    pending: list[tuple[TrackEntryV1, dict[str, object]]] = []
    for edit, entry in zip(request.edits, entries, strict=True):
        changes: dict[str, object] = {}
        for field, prop in _METADATA_PROPERTIES.items():
            desired = getattr(edit, field)
            if desired is None or desired == getattr(entry.facts, field):
                continue
            changes[prop] = desired
        if changes:
            pending.append((entry, changes))
    if not pending:
        return no_change_result(
            targets=targets,
            before=before,
            reason_code="already_satisfied",
            message="every requested value is already set",
            group_id=group_id,
        ).model_dump(mode="json")

    candidate = boundary.from_key(
        "media", f".marquee-{context.attempt.attempt_id}-{source.key.value}"
    )
    try:
        source_signature = file_signature(boundary, source)
        boundary.copy_file(source, candidate)
        args = build_metadata_args(
            source=str(_physical(candidate)), inventory=before, edits=pending
        )
        await run_mkvpropedit_plan(
            context, boundary=boundary, args=args, candidate=candidate
        )
        candidate_inventory = _inventory(_physical(candidate), "candidate:metadata")
        if not _supported(candidate_inventory):
            raise TrackMutationError("the metadata candidate lost its container model")
        backup = create_media_backup(
            boundary,
            source=source,
            subject_key=f"media-file-{media_file_id}",
            signature=source_signature,
            suffix=source_path.suffix.lstrip("."),
        )
        await publish_media_candidate(
            context,
            boundary=boundary,
            candidate=candidate,
            destination=source,
            expected_destination=source_signature,
        )
    except MkvmergePlanError as exc:
        boundary.delete_file(candidate, missing_ok=True)
        return failed_result(
            targets=targets,
            before=before,
            stage="preflight",
            reason_code="plan_rejected",
            message=str(exc),
            group_id=group_id,
        ).model_dump(mode="json")
    except RemuxCancelledError as exc:
        boundary.delete_file(candidate, missing_ok=True)
        return failed_result(
            targets=targets,
            before=before,
            stage=exc.stage,
            reason_code="cancelled",
            message="cancelled before the metadata edit completed",
            group_id=group_id,
        ).model_dump(mode="json")
    except RemuxError as exc:
        boundary.delete_file(candidate, missing_ok=True)
        return failed_result(
            targets=targets,
            before=before,
            stage=exc.stage,
            reason_code=exc.reason_code,
            message=str(exc),
            group_id=group_id,
        ).model_dump(mode="json")
    except Exception as exc:  # noqa: BLE001 - classified as a publish failure
        boundary.delete_file(candidate, missing_ok=True)
        return failed_result(
            targets=targets,
            before=before,
            stage="publish",
            reason_code="publish_failed",
            message=str(exc)[:200],
            group_id=group_id,
        ).model_dump(mode="json")

    actual = _inventory(source_path, _signature(source_path))
    changed_keys = {entry.track_key for entry, _ in pending}
    outcomes = tuple(
        MutationTargetOutcomeV1(
            target=target,
            status=MutationTargetStatus.SUCCEEDED
            if target.key in changed_keys
            else MutationTargetStatus.SKIPPED,
            stage="rescan",
            reason_code="applied" if target.key in changed_keys else "already_satisfied",
            message="verified by rescan",
            bytes_changed=target.key in changed_keys,
            product_state_changed=target.key in changed_keys,
        )
        for target in targets
    )
    return MediaTrackMutationResultV1(
        outcome=MutationJobOutcome.SUCCEEDED,
        reason_code="applied",
        message="subtitle metadata published and rescanned",
        requested_targets=tuple(targets),
        target_outcomes=outcomes,
        validation=MutationValidationV1(verdict="passed"),
        atomicity=atomicity(group_id, published=True),
        backup=backup,
        before_inventory=before,
        actual_inventory=actual,
    ).model_dump(mode="json")


register_execution_handler("audio_remove", execute_audio_remove)
register_execution_handler("track_remove", execute_track_remove)
register_execution_handler("subtitle_remove", execute_subtitle_remove)
register_execution_handler("audio_reorder", execute_audio_reorder)
register_execution_handler("subtitle_metadata", execute_subtitle_metadata)
