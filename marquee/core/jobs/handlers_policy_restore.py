from __future__ import annotations

from marquee.core.jobs.audio_subtitle_documents import (
    MediaTrackMutationResultV1,
    SubtitlePolicyRequestV1,
    SubtitleRestoreRequestV1,
)
from marquee.core.jobs.delivery import ExecutionContext, register_execution_handler
from marquee.core.jobs.media_backups import BackupArtifactError, verify_media_backup
from marquee.core.jobs.media_mutation_support import TrackMutationError
from marquee.core.jobs.media_mutation_support import confined_boundary as _boundary
from marquee.core.jobs.media_mutation_support import current_signature as _signature
from marquee.core.jobs.media_mutation_support import load_media_file as _load
from marquee.core.jobs.media_mutation_support import physical as _physical
from marquee.core.jobs.media_mutation_support import probe_inventory as _inventory
from marquee.core.jobs.mkvmerge_plan import build_remove_args
from marquee.core.jobs.mutation_documents import (
    MutationBackupV1,
    MutationJobOutcome,
    MutationTargetOutcomeV1,
    MutationTargetStatus,
    MutationTargetV1,
    MutationValidationV1,
)
from marquee.core.jobs.publication import file_signature
from marquee.core.jobs.remux_coordinator import (
    atomicity,
    failed_result,
    no_change_result,
    publish_media_candidate,
)
from marquee.core.media_files import (
    MediaFileNotFoundError,
    MediaFileUnavailableError,
    compute_signature,
)


async def execute_subtitle_policy(context: ExecutionContext) -> dict[str, object]:
    """Execute one frozen per-file policy plan; never re-evaluate live policy."""
    # Keep delivery's registration import acyclic when an individual handler
    # module is imported directly by a focused test or worker bootstrap.
    from marquee.core.jobs.handlers_track_mutations import _run_remux

    request = SubtitlePolicyRequestV1.model_validate(context.request)
    group_id = f"subtitle_policy:{request.media_file_id}"

    if not request.remove_selectors:
        # A policy that decided nothing for this file is a reasoned no-change.
        try:
            resolved_file = await _load(context, request.media_file_id)
        except (MediaFileNotFoundError, MediaFileUnavailableError) as exc:
            raise TrackMutationError("the media file is unavailable") from exc
        before = _inventory(resolved_file.path, resolved_file.signature)
        target = MutationTargetV1(
            key=f"policy:{request.policy_id}:{request.media_file_id}",
            kind="track",
            label=f"Policy {request.policy_id}",
            operation="subtitle_policy",
            selector_facts={"policy_revision": request.policy_revision},
        )
        return MediaTrackMutationResultV1(
            **no_change_result(
                targets=[target],
                before=before,
                reason_code="policy_selected_nothing",
                message="the frozen policy plan selected no tracks for this file",
                group_id=group_id,
            ).model_dump()
        ).model_dump(mode="json")

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
        selectors=request.remove_selectors,
        operation="subtitle_policy",
        build=build,
    )


async def execute_subtitle_restore(context: ExecutionContext) -> dict[str, object]:
    """Restore a source file from its canonical checksummed backup artifact."""
    request = SubtitleRestoreRequestV1.model_validate(context.request)
    group_id = f"subtitle_restore:{request.media_file_id}"
    try:
        resolved_file = await _load(context, request.media_file_id)
    except (MediaFileNotFoundError, MediaFileUnavailableError) as exc:
        raise TrackMutationError("the media file is unavailable") from exc

    source_path = resolved_file.path
    before = _inventory(source_path, resolved_file.signature)
    boundary, destination = _boundary(source_path)
    target = MutationTargetV1(
        key=f"restore:{request.media_file_id}",
        kind="media_file",
        label=source_path.name,
        operation="subtitle_restore",
        selector_facts={"source_signature": request.source_signature},
    )

    def _fail(stage: str, reason: str, message: str) -> dict[str, object]:
        return failed_result(
            targets=[target],
            before=before,
            stage=stage,
            reason_code=reason,
            message=message,
            group_id=group_id,
        ).model_dump(mode="json")

    # The destination must still be what the plan was made against.
    if resolved_file.signature != request.expected_destination_signature:
        return _fail(
            "preflight",
            "destination_changed",
            "the destination changed since the restore was planned; make a fresh plan",
        )

    backup = MutationBackupV1(
        artifact_key=request.artifact_key,
        checksum=request.checksum,
        size_bytes=0,
        source_signature=request.source_signature,
        retention="recoverable_media",
        restore_eligible=True,
    )
    try:
        verified = verify_media_backup(boundary, backup.model_copy(update={"size_bytes": _size(boundary, request)}))
    except BackupArtifactError as exc:
        return _fail("validate", "backup_invalid", str(exc)[:200])

    # Restoring identical bytes changes nothing.  This compares the destination's
    # actual content digest with the backup's, because the media-file signature
    # and the backup's source signature use different schemes and never compare.
    if file_signature(boundary, destination).sha256 == request.checksum:
        return MediaTrackMutationResultV1(
            **no_change_result(
                targets=[target],
                before=before,
                reason_code="already_restored",
                message="the destination already matches the backup",
                group_id=group_id,
            ).model_dump()
        ).model_dump(mode="json")
    staged = boundary.from_key(
        "media", f".marquee-restore-{context.attempt.attempt_id}-{destination.key.value}"
    )
    try:
        expected_destination = file_signature(boundary, destination)
        boundary.copy_file(verified, staged)
        boundary.fsync_parent(staged)
        if file_signature(boundary, staged).sha256 != request.checksum:
            raise BackupArtifactError("the staged restore did not match the backup checksum")
        # The backup is never consumed: it is copied, then the copy is published.
        await publish_media_candidate(
            context,
            boundary=boundary,
            candidate=staged,
            destination=destination,
            expected_destination=expected_destination,
        )
    except Exception as exc:  # noqa: BLE001 - classified as a publish failure
        boundary.delete_file(staged, missing_ok=True)
        return _fail("publish", "restore_failed", str(exc)[:200])

    actual = _inventory(source_path, _signature(source_path))
    return MediaTrackMutationResultV1(
        outcome=MutationJobOutcome.SUCCEEDED,
        reason_code="restored",
        message="the file was restored from its canonical backup and rescanned",
        requested_targets=(target,),
        target_outcomes=(
            MutationTargetOutcomeV1(
                target=target,
                status=MutationTargetStatus.SUCCEEDED,
                stage="rescan",
                reason_code="restored",
                message="verified by rescan",
                bytes_changed=True,
                product_state_changed=True,
            ),
        ),
        validation=MutationValidationV1(verdict="passed"),
        atomicity=atomicity(group_id, published=True),
        backup=backup.model_copy(update={"size_bytes": _size(boundary, request)}),
        before_inventory=before,
        actual_inventory=actual,
    ).model_dump(mode="json")


def _size(boundary, request: SubtitleRestoreRequestV1) -> int:
    """Actual stored size of the recorded artifact, or 0 when it is absent."""
    try:
        return _physical(boundary.from_key("data", request.artifact_key)).stat().st_size
    except OSError:
        return 0


def restore_destination_signature(path) -> str:
    """Signature helper used by callers building a restore plan."""
    stat = path.stat()
    return compute_signature(path, size=stat.st_size, mtime_ns=stat.st_mtime_ns)


register_execution_handler("subtitle_policy", execute_subtitle_policy)
register_execution_handler("subtitle_restore", execute_subtitle_restore)
