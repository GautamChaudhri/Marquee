"""Canonical publication decisions for Dolby Vision conversion candidates."""

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
from marquee.core.jobs.dovi_conversion_documents import (
    DoviDecisionResultV1,
    DoviDiscardRequestV1,
    DoviProbeV1,
    DoviPublishRequestV1,
    DoviRestoreRequestV1,
)
from marquee.core.jobs.media_mutation_support import (
    confined_boundary,
    current_signature,
    load_media_file,
)
from marquee.core.jobs.publication import execution_file_signature
from marquee.core.jobs.remux_coordinator import publish_media_candidate
from marquee.models import DoviState, JobArtifact, MediaFile, Movie


async def _probe(context: ExecutionContext, path):
    from marquee.core.jobs.handlers_dovi_conversion import _probe as tracked_probe  # noqa: PLC0415

    return await tracked_probe(context, path)


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
    request: DoviPublishRequestV1 | DoviRestoreRequestV1 | DoviDiscardRequestV1,
    backup_artifact_id: int | None = None,
    before_signature: str | None = None,
    actual_signature: str | None = None,
    actual_checksum: str | None = None,
    actual_probe: DoviProbeV1 | None = None,
    rescanned: bool = False,
    reconciled: bool = False,
    candidate_discarded: bool = False,
) -> dict[str, object]:
    return DoviDecisionResultV1(
        outcome=outcome,
        operation=operation,
        reason_code=reason,
        message=message,
        media_file_id=request.media_file_id,
        movie_id=request.movie_id,
        candidate_artifact_id=request.candidate_artifact_id,
        backup_artifact_id=backup_artifact_id,
        before_signature=before_signature,
        actual_signature=actual_signature,
        actual_checksum=actual_checksum,
        actual_probe=actual_probe,
        rescanned=rescanned,
        reconciled=reconciled,
        candidate_discarded=candidate_discarded,
    ).model_dump(mode="json")


def _candidate_matches(artifact: JobArtifact | None, request) -> bool:
    if (
        artifact is None
        or artifact.kind != "media_candidate"
        or artifact.status != "available"
        or artifact.checksum != request.candidate_checksum
    ):
        return False
    metadata = artifact.artifact_metadata or {}
    return (
        metadata.get("operation_family") == "dovi"
        and int(metadata.get("media_file_id") or 0) == request.media_file_id
        and int(metadata.get("movie_id") or 0) == request.movie_id
    )


def _probe_matches(actual: DoviProbeV1, expected: DoviProbeV1) -> bool:
    if actual.model_dump(exclude={"duration_seconds"}) != expected.model_dump(
        exclude={"duration_seconds"}
    ):
        return False
    if actual.duration_seconds and expected.duration_seconds:
        return abs(actual.duration_seconds - expected.duration_seconds) <= 2.0
    return actual.duration_seconds == expected.duration_seconds


async def _copy_to_stage(
    context: ExecutionContext, artifact: JobArtifact, *, boundary, staged
) -> None:
    await verify_physical_artifact(artifact)
    if artifact.storage_key is None:
        raise ArtifactError("physical artifact has no storage key")
    source = boundary.from_key("data", artifact.storage_key)
    await context.io.confined_copy(boundary, source, staged)


async def _persist_publish(
    context: ExecutionContext,
    request: DoviPublishRequestV1,
    *,
    backup: JobArtifact,
    actual_signature: str,
    actual_size: int,
    actual_probe: DoviProbeV1,
) -> None:
    now = datetime.now(UTC)
    async with context.session_factory() as session, session.begin():
        media_file = await session.get(MediaFile, request.media_file_id)
        movie = await session.get(Movie, request.movie_id)
        candidate = await session.scalar(
            select(JobArtifact).where(JobArtifact.id == request.candidate_artifact_id).with_for_update()
        )
        state = await session.scalar(
            select(DoviState).where(DoviState.movie_id == request.movie_id).with_for_update()
        )
        if media_file is None or movie is None or candidate is None or state is None:
            raise ArtifactError("Dolby Vision publication projection target disappeared")
        media_file.size_bytes = actual_size
        movie.has_dv = True
        movie.has_hdr = True
        candidate.artifact_metadata = {
            **(candidate.artifact_metadata or {}),
            "published": True,
            "published_by_job_id": context.delivery.canonical_job_id,
            "published_signature": actual_signature,
            "published_checksum": candidate.checksum,
            "backup_artifact_id": backup.id,
            "backup_checksum": backup.checksum,
        }
        state.status = "analyzed"
        state.dovi_profile = actual_probe.dovi_profile
        state.dovi_level = actual_probe.dovi_level
        state.el_present = actual_probe.enhancement_layer_present
        state.el_type = None
        state.bl_signal_compatibility_id = actual_probe.bl_signal_compatibility_id
        state.source_codec = actual_probe.codec
        state.source_signature = actual_signature
        state.source_fence_token = context.attempt.fence_token
        state.analysis_supported = True
        state.error_reason = None
        state.last_analyzed_at = now


async def _persist_restore(
    context: ExecutionContext,
    request: DoviRestoreRequestV1,
    *,
    actual_signature: str,
    actual_size: int,
    actual_probe: DoviProbeV1,
) -> None:
    async with context.session_factory() as session, session.begin():
        media_file = await session.get(MediaFile, request.media_file_id)
        candidate = await session.scalar(
            select(JobArtifact).where(JobArtifact.id == request.candidate_artifact_id).with_for_update()
        )
        state = await session.scalar(
            select(DoviState).where(DoviState.movie_id == request.movie_id).with_for_update()
        )
        if media_file is None or candidate is None or state is None:
            raise ArtifactError("Dolby Vision restore projection target disappeared")
        media_file.size_bytes = actual_size
        candidate.artifact_metadata = {
            **(candidate.artifact_metadata or {}),
            "restored": True,
            "restored_by_job_id": context.delivery.canonical_job_id,
        }
        state.status = "analyzed"
        state.dovi_profile = actual_probe.dovi_profile
        state.dovi_level = actual_probe.dovi_level
        state.el_present = actual_probe.enhancement_layer_present
        state.bl_signal_compatibility_id = actual_probe.bl_signal_compatibility_id
        state.source_codec = actual_probe.codec
        state.source_signature = actual_signature
        state.source_fence_token = context.attempt.fence_token
        state.error_reason = None
        state.last_analyzed_at = datetime.now(UTC)


async def execute_dovi_publish(context: ExecutionContext) -> dict[str, object]:
    request = DoviPublishRequestV1.model_validate(context.request)
    candidate = await _artifact(context, request.candidate_artifact_id)
    if not _candidate_matches(candidate, request) or candidate is None:
        return _result(operation="publish", outcome="failed", reason="candidate_stale", message="the Dolby Vision candidate is unavailable or stale", request=request)
    try:
        await verify_physical_artifact(candidate)
        resolved = await load_media_file(context, request.media_file_id)
    except Exception as exc:  # noqa: BLE001
        return _result(operation="publish", outcome="failed", reason="preflight_failed", message=str(exc)[:500], request=request)
    if resolved.path.suffix.lower() != ".mkv":
        return _result(operation="publish", outcome="failed", reason="unsupported_container", message="only Matroska publication is certified", request=request)
    boundary, destination = confined_boundary(resolved.path)
    before_file = await execution_file_signature(context.io, boundary, destination)
    backup = await _backup_for_job(context)
    already_published = before_file.sha256 == request.candidate_checksum
    if resolved.signature != request.expected_source_signature and not already_published:
        return _result(operation="publish", outcome="failed", reason="source_changed", message="the source changed after confirmation", request=request, before_signature=resolved.signature)
    if already_published:
        if backup is None:
            return _result(operation="publish", outcome="unsafe", reason="published_without_backup_evidence", message="candidate bytes are live without canonical backup evidence", request=request, actual_checksum=before_file.sha256)
        actual_probe = await _probe(context, resolved.path)
        if not _probe_matches(actual_probe, request.candidate_probe):
            return _result(operation="publish", outcome="unsafe", reason="reconciliation_probe_failed", message="published bytes failed the sealed Dolby Vision probe", request=request, backup_artifact_id=backup.id, actual_checksum=before_file.sha256)
        actual_signature = current_signature(resolved.path)
        await _persist_publish(context, request, backup=backup, actual_signature=actual_signature, actual_size=before_file.size, actual_probe=actual_probe)
        return _result(operation="publish", outcome="succeeded", reason="publish_reconciled", message="a prior atomic publication was reconciled and rescanned", request=request, backup_artifact_id=backup.id, actual_signature=actual_signature, actual_checksum=before_file.sha256, actual_probe=actual_probe, rescanned=True, reconciled=True)
    staged = boundary.from_key("media", f".marquee-dovi-publish-{context.attempt.attempt_id}-{destination.key.value}")
    try:
        await _copy_to_stage(context, candidate, boundary=boundary, staged=staged)
        if (
            await execution_file_signature(context.io, boundary, staged)
        ).sha256 != request.candidate_checksum:
            raise ArtifactError("destination-local candidate checksum mismatch")
        staged_probe = await _probe(context, resolved.path.parent / staged.key.value)
        if not _probe_matches(staged_probe, request.candidate_probe):
            raise ArtifactError("destination-local candidate probe mismatch")
        if backup is None:
            backup = await register_physical_artifact(
                job_id=context.delivery.canonical_job_id,
                attempt_id=context.attempt.attempt_id,
                fence_token=context.attempt.fence_token,
                source=destination,
                kind="media_backup",
                name="dovi-original.mkv",
                content_type="video/x-matroska",
                retention_class="extended",
                metadata={"operation_family": "dovi", "media_file_id": request.media_file_id, "movie_id": request.movie_id, "candidate_artifact_id": request.candidate_artifact_id, "source_signature": request.expected_source_signature, "source_probe": request.source_probe.model_dump(mode="json")},
            )
        await publish_media_candidate(context, boundary=boundary, candidate=staged, destination=destination, expected_destination=before_file)
    except Exception as exc:  # noqa: BLE001
        boundary.delete_file(staged, missing_ok=True)
        return _result(operation="publish", outcome="failed", reason="publish_failed", message=str(exc)[:500], request=request, backup_artifact_id=backup.id if backup else None, before_signature=resolved.signature)
    actual_file = await execution_file_signature(context.io, boundary, destination)
    actual_probe = await _probe(context, resolved.path)
    if actual_file.sha256 != request.candidate_checksum or not _probe_matches(actual_probe, request.candidate_probe):
        return _result(operation="publish", outcome="unsafe", reason="post_publish_validation_failed", message="published destination failed checksum or Dolby Vision rescan", request=request, backup_artifact_id=backup.id, actual_checksum=actual_file.sha256, actual_probe=actual_probe)
    actual_signature = current_signature(resolved.path)
    await _persist_publish(context, request, backup=backup, actual_signature=actual_signature, actual_size=actual_file.size, actual_probe=actual_probe)
    return _result(operation="publish", outcome="succeeded", reason="published", message="candidate was atomically published, rescanned, and linked to a canonical backup", request=request, backup_artifact_id=backup.id, before_signature=resolved.signature, actual_signature=actual_signature, actual_checksum=actual_file.sha256, actual_probe=actual_probe, rescanned=True)


async def execute_dovi_restore(context: ExecutionContext) -> dict[str, object]:
    request = DoviRestoreRequestV1.model_validate(context.request)
    backup = await _artifact(context, request.backup_artifact_id)
    candidate = await _artifact(context, request.candidate_artifact_id)
    if backup is None or backup.kind != "media_backup" or backup.status != "available" or backup.checksum != request.backup_checksum or backup.size_bytes != request.backup_size_bytes or candidate is None or candidate.kind != "media_candidate":
        return _result(operation="restore", outcome="failed", reason="restore_evidence_stale", message="candidate or backup evidence is unavailable or changed", request=request, backup_artifact_id=request.backup_artifact_id)
    try:
        await verify_physical_artifact(backup)
        resolved = await load_media_file(context, request.media_file_id)
    except Exception as exc:  # noqa: BLE001
        return _result(operation="restore", outcome="failed", reason="restore_preflight_failed", message=str(exc)[:500], request=request, backup_artifact_id=request.backup_artifact_id)
    boundary, destination = confined_boundary(resolved.path)
    before_file = await execution_file_signature(context.io, boundary, destination)
    already_restored = before_file.sha256 == request.backup_checksum
    if resolved.signature != request.expected_destination_signature and not already_restored:
        return _result(operation="restore", outcome="failed", reason="destination_changed", message="the destination changed after confirmation", request=request, backup_artifact_id=request.backup_artifact_id)
    if not already_restored and before_file.sha256 != request.published_checksum:
        return _result(operation="restore", outcome="failed", reason="destination_not_published_candidate", message="the destination no longer matches the published candidate", request=request, backup_artifact_id=request.backup_artifact_id, actual_checksum=before_file.sha256)
    if not already_restored:
        staged = boundary.from_key("media", f".marquee-dovi-restore-{context.attempt.attempt_id}-{destination.key.value}")
        try:
            await _copy_to_stage(context, backup, boundary=boundary, staged=staged)
            if (
                await execution_file_signature(context.io, boundary, staged)
            ).sha256 != request.backup_checksum:
                raise ArtifactError("destination-local restore checksum mismatch")
            await publish_media_candidate(context, boundary=boundary, candidate=staged, destination=destination, expected_destination=before_file)
        except Exception as exc:  # noqa: BLE001
            boundary.delete_file(staged, missing_ok=True)
            return _result(operation="restore", outcome="failed", reason="restore_failed", message=str(exc)[:500], request=request, backup_artifact_id=request.backup_artifact_id)
    actual_file = await execution_file_signature(context.io, boundary, destination)
    if actual_file.sha256 != request.backup_checksum:
        return _result(operation="restore", outcome="unsafe", reason="post_restore_validation_failed", message="restored destination failed checksum validation", request=request, backup_artifact_id=request.backup_artifact_id, actual_checksum=actual_file.sha256)
    actual_probe = await _probe(context, resolved.path)
    actual_signature = current_signature(resolved.path)
    await _persist_restore(context, request, actual_signature=actual_signature, actual_size=actual_file.size, actual_probe=actual_probe)
    return _result(operation="restore", outcome="no_change" if already_restored else "succeeded", reason="restore_reconciled" if already_restored else "restored", message="the canonical original was restored and rescanned", request=request, backup_artifact_id=request.backup_artifact_id, actual_signature=actual_signature, actual_checksum=actual_file.sha256, actual_probe=actual_probe, rescanned=True, reconciled=already_restored)


async def execute_dovi_discard(context: ExecutionContext) -> dict[str, object]:
    request = DoviDiscardRequestV1.model_validate(context.request)
    artifact = await _artifact(context, request.candidate_artifact_id)
    if artifact is None or artifact.kind != "media_candidate":
        return _result(operation="discard", outcome="failed", reason="candidate_missing", message="the candidate artifact does not exist", request=request)
    if artifact.status == "expired":
        return _result(operation="discard", outcome="no_change", reason="already_discarded", message="the candidate was already discarded", request=request, candidate_discarded=True, reconciled=True)
    if not _candidate_matches(artifact, request):
        return _result(operation="discard", outcome="failed", reason="candidate_stale", message="candidate checksum or ownership changed", request=request)
    metadata = artifact.artifact_metadata or {}
    if metadata.get("published") and not metadata.get("restored"):
        return _result(operation="discard", outcome="failed", reason="candidate_in_use", message="a published candidate cannot be discarded until restore", request=request)
    boundary, stored = physical_artifact_file(artifact)
    boundary.delete_file(stored, missing_ok=True)
    async with context.session_factory() as session, session.begin():
        row = await session.scalar(select(JobArtifact).where(JobArtifact.id == request.candidate_artifact_id).with_for_update())
        if row is None or row.kind != "media_candidate":
            raise ArtifactError("candidate ownership changed during discard")
        row.status = "expired"
        row.expires_at = datetime.now(UTC)
        row.artifact_metadata = {**(row.artifact_metadata or {}), "discarded_by_job_id": context.delivery.canonical_job_id}
    return _result(operation="discard", outcome="succeeded", reason="discarded", message="the unreferenced owned candidate was deleted", request=request, candidate_discarded=True)


register_execution_handler("dovi_publish", execute_dovi_publish)
register_execution_handler("dovi_restore", execute_dovi_restore)
register_execution_handler("dovi_discard", execute_dovi_discard)
