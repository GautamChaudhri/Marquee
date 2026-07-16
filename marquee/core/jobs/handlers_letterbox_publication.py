"""Canonical publish, restore, and discard commands for letterbox candidates."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select

from marquee.core.jobs.artifact_service import (
    ArtifactError,
    physical_artifact_file,
    register_physical_artifact,
    verify_physical_artifact,
)
from marquee.core.jobs.delivery import ExecutionContext, register_execution_handler
from marquee.core.jobs.handlers_letterbox_reencode import _probe
from marquee.core.jobs.letterbox_reencode_documents import (
    LetterboxReencodeDecisionResultV1,
    LetterboxReencodeDiscardRequestV1,
    LetterboxReencodePublishRequestV1,
    LetterboxReencodeRestoreRequestV1,
    ReencodeProbeV1,
)
from marquee.core.jobs.media_mutation_support import (
    confined_boundary,
    current_signature,
    load_media_file,
)
from marquee.core.jobs.publication import file_signature
from marquee.core.jobs.remux_coordinator import publish_media_candidate
from marquee.models import EpisodeMediaFile, JobArtifact, LetterboxEvent, LetterboxState, MediaFile


async def _artifact(context: ExecutionContext, artifact_id: int) -> JobArtifact | None:
    async with context.session_factory() as session:
        return await session.get(JobArtifact, artifact_id)


async def _backup_for_job(context: ExecutionContext) -> JobArtifact | None:
    async with context.session_factory() as session:
        return await session.scalar(
            select(JobArtifact)
            .where(
                JobArtifact.job_id == context.delivery.canonical_job_id,
                JobArtifact.kind == "media_backup",
                JobArtifact.status == "available",
            )
            .order_by(JobArtifact.id.desc())
            .limit(1)
        )


def _result(
    *,
    operation: str,
    outcome: str,
    reason: str,
    message: str,
    media_file_id: int,
    candidate_artifact_id: int,
    backup_artifact_id: int | None = None,
    before_signature: str | None = None,
    actual_signature: str | None = None,
    actual_checksum: str | None = None,
    rescanned: bool = False,
    reconciled: bool = False,
    candidate_discarded: bool = False,
) -> dict[str, object]:
    return LetterboxReencodeDecisionResultV1(
        outcome=outcome,
        operation=operation,
        reason_code=reason,
        message=message,
        media_file_id=media_file_id,
        candidate_artifact_id=candidate_artifact_id,
        backup_artifact_id=backup_artifact_id,
        before_signature=before_signature,
        actual_signature=actual_signature,
        actual_checksum=actual_checksum,
        rescanned=rescanned,
        reconciled=reconciled,
        candidate_discarded=candidate_discarded,
    ).model_dump(mode="json")


def _probe_matches(actual: ReencodeProbeV1, expected: ReencodeProbeV1) -> bool:
    if (actual.codec, actual.width, actual.height) != (
        expected.codec,
        expected.width,
        expected.height,
    ):
        return False
    if (
        actual.video_streams,
        actual.audio_streams,
        actual.subtitle_streams,
        actual.attachment_streams,
    ) != (
        expected.video_streams,
        expected.audio_streams,
        expected.subtitle_streams,
        expected.attachment_streams,
    ):
        return False
    if actual.has_hdr != expected.has_hdr or actual.has_dolby_vision != expected.has_dolby_vision:
        return False
    if actual.duration_seconds and expected.duration_seconds:
        return abs(actual.duration_seconds - expected.duration_seconds) <= 2.0
    return actual.duration_seconds == expected.duration_seconds


def _candidate_matches_request(
    artifact: JobArtifact | None,
    request: LetterboxReencodePublishRequestV1 | LetterboxReencodeDiscardRequestV1,
) -> bool:
    if (
        artifact is None
        or artifact.kind != "media_candidate"
        or artifact.status != "available"
        or artifact.checksum != request.candidate_checksum
    ):
        return False
    metadata = artifact.artifact_metadata or {}
    return int(metadata.get("media_file_id") or 0) == request.media_file_id


async def _copy_artifact_to_stage(
    artifact: JobArtifact,
    *,
    boundary,
    staged,
) -> None:
    await verify_physical_artifact(artifact)
    if artifact.storage_key is None:
        raise ArtifactError("physical artifact has no storage key")
    source = boundary.from_key("data", artifact.storage_key)
    boundary.copy_file(source, staged)
    boundary.fsync_parent(staged)


async def _persist_publish(
    context: ExecutionContext,
    request: LetterboxReencodePublishRequestV1,
    *,
    backup: JobArtifact,
    actual_signature: str,
    actual_size: int,
) -> None:
    now = datetime.now(UTC)
    async with context.session_factory() as session, session.begin():
        media_file = await session.get(MediaFile, request.media_file_id)
        candidate = await session.scalar(
            select(JobArtifact)
            .where(JobArtifact.id == request.candidate_artifact_id)
            .with_for_update()
        )
        if media_file is None or candidate is None:
            raise ArtifactError("publication projection target disappeared")
        media_file.size_bytes = actual_size
        candidate.artifact_metadata = {
            **(candidate.artifact_metadata or {}),
            "published": True,
            "published_by_job_id": context.delivery.canonical_job_id,
            "published_signature": actual_signature,
            "backup_artifact_id": backup.id,
            "backup_checksum": backup.checksum,
        }
        if media_file.movie_id is not None:
            states = list(
                (
                    await session.scalars(
                        select(LetterboxState).where(
                            LetterboxState.media_type == "movie",
                            LetterboxState.movie_id == media_file.movie_id,
                        )
                    )
                ).all()
            )
        else:
            states = list(
                (
                    await session.scalars(
                        select(LetterboxState)
                        .join(
                            EpisodeMediaFile,
                            EpisodeMediaFile.episode_id == LetterboxState.episode_id,
                        )
                        .where(
                            LetterboxState.media_type == "episode",
                            EpisodeMediaFile.media_file_id == request.media_file_id,
                        )
                    )
                ).all()
            )
        for state in states:
            state.status = "reencoded"
            state.applied_crop_top = request.crop_top
            state.applied_crop_bottom = request.crop_bottom
            state.last_applied_at = now
            state.error = None
            state.resolved_by = "reencode"
            state.resolved_at = now
            state.original_crop_top = request.crop_top
            state.original_crop_bottom = request.crop_bottom
            state.original_aspect_label = state.aspect_label
            session.add(
                LetterboxEvent(
                    media_type=state.media_type,
                    movie_id=state.movie_id,
                    episode_id=state.episode_id,
                    action="reencode_publish",
                    source="job",
                    detail=f'{{"job_id":"{context.delivery.canonical_job_id}"}}',
                )
            )


async def _persist_restore(
    context: ExecutionContext,
    request: LetterboxReencodeRestoreRequestV1,
    *,
    actual_size: int,
) -> None:
    async with context.session_factory() as session, session.begin():
        media_file = await session.get(MediaFile, request.media_file_id)
        candidate = await session.scalar(
            select(JobArtifact)
            .where(JobArtifact.id == request.candidate_artifact_id)
            .with_for_update()
        )
        if media_file is None or candidate is None:
            raise ArtifactError("restore projection target disappeared")
        media_file.size_bytes = actual_size
        candidate.artifact_metadata = {
            **(candidate.artifact_metadata or {}),
            "restored": True,
            "restored_by_job_id": context.delivery.canonical_job_id,
        }
        if media_file.movie_id is not None:
            states = list(
                (
                    await session.scalars(
                        select(LetterboxState).where(
                            LetterboxState.media_type == "movie",
                            LetterboxState.movie_id == media_file.movie_id,
                        )
                    )
                ).all()
            )
        else:
            states = list(
                (
                    await session.scalars(
                        select(LetterboxState)
                        .join(
                            EpisodeMediaFile,
                            EpisodeMediaFile.episode_id == LetterboxState.episode_id,
                        )
                        .where(
                            LetterboxState.media_type == "episode",
                            EpisodeMediaFile.media_file_id == request.media_file_id,
                        )
                    )
                ).all()
            )
        for state in states:
            state.status = "candidate"
            state.applied_crop_top = None
            state.applied_crop_bottom = None
            state.resolved_by = None
            state.resolved_at = None
            state.original_crop_top = None
            state.original_crop_bottom = None
            state.original_aspect_label = None
            session.add(
                LetterboxEvent(
                    media_type=state.media_type,
                    movie_id=state.movie_id,
                    episode_id=state.episode_id,
                    action="reencode_restore",
                    source="job",
                    detail=f'{{"job_id":"{context.delivery.canonical_job_id}"}}',
                )
            )


async def execute_letterbox_reencode_publish(context: ExecutionContext) -> dict[str, object]:
    request = LetterboxReencodePublishRequestV1.model_validate(context.request)
    candidate = await _artifact(context, request.candidate_artifact_id)
    if not _candidate_matches_request(candidate, request) or candidate is None:
        return _result(
            operation="publish",
            outcome="failed",
            reason="candidate_stale",
            message="the candidate is missing, corrupt, expired, or belongs to another file",
            media_file_id=request.media_file_id,
            candidate_artifact_id=request.candidate_artifact_id,
        )
    try:
        await verify_physical_artifact(candidate)
        resolved = await load_media_file(context, request.media_file_id)
    except Exception as exc:  # noqa: BLE001
        return _result(
            operation="publish",
            outcome="failed",
            reason="preflight_failed",
            message=str(exc)[:500],
            media_file_id=request.media_file_id,
            candidate_artifact_id=request.candidate_artifact_id,
        )
    if resolved.path.suffix.lower() != ".mkv":
        return _result(
            operation="publish",
            outcome="failed",
            reason="unsupported_container",
            message="only same-container Matroska publication is certified",
            media_file_id=request.media_file_id,
            candidate_artifact_id=request.candidate_artifact_id,
        )
    boundary, destination = confined_boundary(resolved.path)
    before_file = file_signature(boundary, destination)
    backup = await _backup_for_job(context)
    already_published = before_file.sha256 == request.candidate_checksum
    if resolved.signature != request.expected_source_signature and not already_published:
        return _result(
            operation="publish",
            outcome="failed",
            reason="source_changed",
            message="the source changed after the publish plan was confirmed",
            media_file_id=request.media_file_id,
            candidate_artifact_id=request.candidate_artifact_id,
            before_signature=resolved.signature,
        )
    if already_published:
        if backup is None:
            return _result(
                operation="publish",
                outcome="unsafe",
                reason="published_without_backup_evidence",
                message="candidate bytes are live but restorable backup evidence is missing",
                media_file_id=request.media_file_id,
                candidate_artifact_id=request.candidate_artifact_id,
                before_signature=resolved.signature,
                actual_checksum=before_file.sha256,
            )
        actual_probe = await _probe(context, resolved.path)
        if not _probe_matches(actual_probe, request.candidate_probe):
            return _result(
                operation="publish",
                outcome="unsafe",
                reason="reconciliation_probe_failed",
                message="published bytes could not be reconciled to the candidate probe",
                media_file_id=request.media_file_id,
                candidate_artifact_id=request.candidate_artifact_id,
                backup_artifact_id=backup.id,
                actual_checksum=before_file.sha256,
            )
        actual_signature = current_signature(resolved.path)
        await _persist_publish(
            context,
            request,
            backup=backup,
            actual_signature=actual_signature,
            actual_size=before_file.size,
        )
        return _result(
            operation="publish",
            outcome="succeeded",
            reason="publish_reconciled",
            message="a prior atomic publication was reconciled and rescanned",
            media_file_id=request.media_file_id,
            candidate_artifact_id=request.candidate_artifact_id,
            backup_artifact_id=backup.id,
            before_signature=resolved.signature,
            actual_signature=actual_signature,
            actual_checksum=before_file.sha256,
            rescanned=True,
            reconciled=True,
        )

    staged = boundary.from_key(
        "media", f".marquee-publish-{context.attempt.attempt_id}-{destination.key.value}"
    )
    try:
        await _copy_artifact_to_stage(candidate, boundary=boundary, staged=staged)
        staged_signature = file_signature(boundary, staged)
        if staged_signature.sha256 != request.candidate_checksum:
            raise ArtifactError("destination-local candidate copy failed checksum validation")
        staged_probe = await _probe(context, resolved.path.parent / staged.key.value)
        if not _probe_matches(staged_probe, request.candidate_probe):
            raise ArtifactError("destination-local candidate failed its sealed media probe")
        if backup is None:
            backup = await register_physical_artifact(
                job_id=context.delivery.canonical_job_id,
                attempt_id=context.attempt.attempt_id,
                fence_token=context.attempt.fence_token,
                source=destination,
                kind="media_backup",
                name="letterbox-original.mkv",
                content_type="video/x-matroska",
                retention_class="extended",
                metadata={
                    "media_file_id": request.media_file_id,
                    "candidate_artifact_id": request.candidate_artifact_id,
                    "source_signature": request.expected_source_signature,
                    "source_probe": request.source_probe.model_dump(mode="json"),
                },
            )
        await publish_media_candidate(
            context,
            boundary=boundary,
            candidate=staged,
            destination=destination,
            expected_destination=before_file,
        )
    except Exception as exc:  # noqa: BLE001
        boundary.delete_file(staged, missing_ok=True)
        return _result(
            operation="publish",
            outcome="failed",
            reason="publish_failed",
            message=str(exc)[:500],
            media_file_id=request.media_file_id,
            candidate_artifact_id=request.candidate_artifact_id,
            backup_artifact_id=backup.id if backup else None,
            before_signature=resolved.signature,
        )
    actual_file = file_signature(boundary, destination)
    actual_probe = await _probe(context, resolved.path)
    if actual_file.sha256 != request.candidate_checksum or not _probe_matches(
        actual_probe, request.candidate_probe
    ):
        return _result(
            operation="publish",
            outcome="unsafe",
            reason="post_publish_validation_failed",
            message="the published destination failed checksum or media rescan validation",
            media_file_id=request.media_file_id,
            candidate_artifact_id=request.candidate_artifact_id,
            backup_artifact_id=backup.id,
            before_signature=resolved.signature,
            actual_checksum=actual_file.sha256,
        )
    actual_signature = current_signature(resolved.path)
    await _persist_publish(
        context,
        request,
        backup=backup,
        actual_signature=actual_signature,
        actual_size=actual_file.size,
    )
    return _result(
        operation="publish",
        outcome="succeeded",
        reason="published",
        message="candidate was atomically published, rescanned, and linked to a canonical backup",
        media_file_id=request.media_file_id,
        candidate_artifact_id=request.candidate_artifact_id,
        backup_artifact_id=backup.id,
        before_signature=resolved.signature,
        actual_signature=actual_signature,
        actual_checksum=actual_file.sha256,
        rescanned=True,
    )


async def execute_letterbox_reencode_restore(context: ExecutionContext) -> dict[str, object]:
    request = LetterboxReencodeRestoreRequestV1.model_validate(context.request)
    backup = await _artifact(context, request.backup_artifact_id)
    candidate = await _artifact(context, request.candidate_artifact_id)
    if (
        backup is None
        or backup.kind != "media_backup"
        or backup.status != "available"
        or backup.checksum != request.backup_checksum
        or backup.size_bytes != request.backup_size_bytes
        or candidate is None
        or candidate.kind != "media_candidate"
    ):
        return _result(
            operation="restore",
            outcome="failed",
            reason="restore_evidence_stale",
            message="the candidate or backup evidence is unavailable or changed",
            media_file_id=request.media_file_id,
            candidate_artifact_id=request.candidate_artifact_id,
            backup_artifact_id=request.backup_artifact_id,
        )
    try:
        await verify_physical_artifact(backup)
        resolved = await load_media_file(context, request.media_file_id)
    except Exception as exc:  # noqa: BLE001
        return _result(
            operation="restore",
            outcome="failed",
            reason="restore_preflight_failed",
            message=str(exc)[:500],
            media_file_id=request.media_file_id,
            candidate_artifact_id=request.candidate_artifact_id,
            backup_artifact_id=request.backup_artifact_id,
        )
    boundary, destination = confined_boundary(resolved.path)
    before_file = file_signature(boundary, destination)
    already_restored = before_file.sha256 == request.backup_checksum
    if resolved.signature != request.expected_destination_signature and not already_restored:
        return _result(
            operation="restore",
            outcome="failed",
            reason="destination_changed",
            message="the destination changed after the restore plan was confirmed",
            media_file_id=request.media_file_id,
            candidate_artifact_id=request.candidate_artifact_id,
            backup_artifact_id=request.backup_artifact_id,
            before_signature=resolved.signature,
        )
    if not already_restored and before_file.sha256 != request.published_checksum:
        return _result(
            operation="restore",
            outcome="failed",
            reason="destination_not_published_candidate",
            message="the destination no longer matches the published candidate",
            media_file_id=request.media_file_id,
            candidate_artifact_id=request.candidate_artifact_id,
            backup_artifact_id=request.backup_artifact_id,
            actual_checksum=before_file.sha256,
        )
    if not already_restored:
        staged = boundary.from_key(
            "media", f".marquee-restore-{context.attempt.attempt_id}-{destination.key.value}"
        )
        try:
            await _copy_artifact_to_stage(backup, boundary=boundary, staged=staged)
            if file_signature(boundary, staged).sha256 != request.backup_checksum:
                raise ArtifactError("destination-local restore copy failed checksum validation")
            await publish_media_candidate(
                context,
                boundary=boundary,
                candidate=staged,
                destination=destination,
                expected_destination=before_file,
            )
        except Exception as exc:  # noqa: BLE001
            boundary.delete_file(staged, missing_ok=True)
            return _result(
                operation="restore",
                outcome="failed",
                reason="restore_failed",
                message=str(exc)[:500],
                media_file_id=request.media_file_id,
                candidate_artifact_id=request.candidate_artifact_id,
                backup_artifact_id=request.backup_artifact_id,
                before_signature=resolved.signature,
            )
    actual_file = file_signature(boundary, destination)
    if actual_file.sha256 != request.backup_checksum:
        return _result(
            operation="restore",
            outcome="unsafe",
            reason="post_restore_validation_failed",
            message="the restored destination failed checksum validation",
            media_file_id=request.media_file_id,
            candidate_artifact_id=request.candidate_artifact_id,
            backup_artifact_id=request.backup_artifact_id,
            actual_checksum=actual_file.sha256,
        )
    await _probe(context, resolved.path)
    actual_signature = current_signature(resolved.path)
    await _persist_restore(context, request, actual_size=actual_file.size)
    return _result(
        operation="restore",
        outcome="no_change" if already_restored else "succeeded",
        reason="restore_reconciled" if already_restored else "restored",
        message="the canonical original was restored and the actual destination was rescanned",
        media_file_id=request.media_file_id,
        candidate_artifact_id=request.candidate_artifact_id,
        backup_artifact_id=request.backup_artifact_id,
        before_signature=resolved.signature,
        actual_signature=actual_signature,
        actual_checksum=actual_file.sha256,
        rescanned=True,
        reconciled=already_restored,
    )


async def execute_letterbox_reencode_discard(context: ExecutionContext) -> dict[str, object]:
    request = LetterboxReencodeDiscardRequestV1.model_validate(context.request)
    artifact = await _artifact(context, request.candidate_artifact_id)
    if artifact is None or artifact.kind != "media_candidate":
        return _result(
            operation="discard",
            outcome="failed",
            reason="candidate_missing",
            message="the candidate artifact does not exist",
            media_file_id=request.media_file_id,
            candidate_artifact_id=request.candidate_artifact_id,
        )
    if artifact.status == "expired":
        return _result(
            operation="discard",
            outcome="no_change",
            reason="already_discarded",
            message="the candidate was already discarded",
            media_file_id=request.media_file_id,
            candidate_artifact_id=request.candidate_artifact_id,
            candidate_discarded=True,
            reconciled=True,
        )
    metadata = artifact.artifact_metadata or {}
    if artifact.checksum != request.candidate_checksum or int(metadata.get("media_file_id") or 0) != request.media_file_id:
        return _result(
            operation="discard",
            outcome="failed",
            reason="candidate_stale",
            message="the candidate checksum or media ownership changed",
            media_file_id=request.media_file_id,
            candidate_artifact_id=request.candidate_artifact_id,
        )
    if metadata.get("published") and not metadata.get("restored"):
        return _result(
            operation="discard",
            outcome="failed",
            reason="candidate_in_use",
            message="a published candidate cannot be discarded until its original is restored",
            media_file_id=request.media_file_id,
            candidate_artifact_id=request.candidate_artifact_id,
        )
    boundary, stored = physical_artifact_file(artifact)
    boundary.delete_file(stored, missing_ok=True)
    async with context.session_factory() as session, session.begin():
        row = await session.scalar(
            select(JobArtifact)
            .where(JobArtifact.id == request.candidate_artifact_id)
            .with_for_update()
        )
        if row is None or row.kind != "media_candidate":
            raise ArtifactError("candidate ownership changed during discard")
        row.status = "expired"
        row.expires_at = datetime.now(UTC)
        row.artifact_metadata = {
            **(row.artifact_metadata or {}),
            "discarded_by_job_id": context.delivery.canonical_job_id,
        }
    return _result(
        operation="discard",
        outcome="succeeded",
        reason="discarded",
        message="the unreferenced owned candidate was deleted from confined storage",
        media_file_id=request.media_file_id,
        candidate_artifact_id=request.candidate_artifact_id,
        candidate_discarded=True,
    )


register_execution_handler("letterbox_reencode_publish", execute_letterbox_reencode_publish)
register_execution_handler("letterbox_reencode_restore", execute_letterbox_reencode_restore)
register_execution_handler("letterbox_reencode_discard", execute_letterbox_reencode_discard)
