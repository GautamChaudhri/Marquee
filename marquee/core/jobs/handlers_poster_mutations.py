"""Canonical fenced poster deploy, restore, and reset leaves."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from PIL import Image, UnidentifiedImageError
from sqlalchemy import select

from marquee.config import settings
from marquee.core.filesystem import ClassifiedPath, FilesystemBoundary, RootSpec
from marquee.core.jobs.artifact_service import verify_physical_artifact
from marquee.core.jobs.delivery import ExecutionContext, register_execution_handler
from marquee.core.jobs.mutation_documents import (
    MutationAtomicityV1,
    MutationBackupV1,
    MutationEvidenceV1,
    MutationInvariantV1,
    MutationPublishV1,
    MutationSnapshotV1,
    MutationTargetOutcomeV1,
    MutationTargetStatus,
    MutationTargetV1,
    MutationValidationV1,
    PosterBackupRequestV1,
    PosterCandidateSelectionV1,
    PosterDeployRequestV1,
    PosterMutationResultV1,
    PosterResetRequestV1,
    PosterRestoreRequestV1,
    require_publication_preconditions,
)
from marquee.core.jobs.mutation_evidence import apply_mutation_evidence
from marquee.core.jobs.publication import (
    FileSignature,
    PublicationCoordinator,
    PublicationError,
    file_signature,
)
from marquee.core.path_utils import safe_translate_and_validate
from marquee.core.poster_files import tmdb_original_url
from marquee.core.poster_subjects import PosterSubject
from marquee.core.taste_preferences import (
    activate_exemplar,
    pin_candidate_artifact,
    record_deployment_effect,
    schedule_profile_builds,
)
from marquee.models import (
    ArtworkEvent,
    JobArtifact,
    MediaOperationDetail,
    Movie,
    PipelineRun,
    Season,
    Series,
    TasteExemplar,
)

SubjectKind = Literal["movie", "series", "season"]


class PosterMutationError(RuntimeError):
    pass


def _model(kind: SubjectKind):
    return {"movie": Movie, "series": Series, "season": Season}[kind]


async def _load_subject(session, kind: SubjectKind, subject_id: int) -> PosterSubject:
    entity = await session.get(_model(kind), subject_id)
    if entity is None or not entity.is_present:
        raise PosterMutationError("poster subject is no longer available")
    if kind == "movie":
        return PosterSubject.from_movie(entity)
    if kind == "series":
        return PosterSubject.from_series(entity)
    series = await session.get(Series, entity.series_id)
    if series is None or not series.is_present:
        raise PosterMutationError("poster subject series is no longer available")
    return PosterSubject.from_season(entity, series)


def _target(subject: PosterSubject, operation: str) -> MutationTargetV1:
    return MutationTargetV1(
        key=f"{subject.media_type}:{subject.id}:poster",
        kind=f"{subject.media_type}_artwork",
        label=subject.title,
        operation=operation,
        selector_facts={
            "subject_id": subject.id,
            "media_type": subject.media_type,
            "folder_identity": f"{subject.path_source}:{subject.id}",
            "filename": subject.render_filename(),
        },
    )


def _signature_text(signature: FileSignature | None) -> str | None:
    if signature is None:
        return None
    return (
        f"device:{signature.device}:inode:{signature.inode}:"
        f"size:{signature.size}:modified:{signature.modified_ns}"
    )


def _snapshot(
    subject: PosterSubject,
    signature: FileSignature | None,
    *,
    exists: bool,
) -> MutationSnapshotV1:
    return MutationSnapshotV1(
        identity=f"poster:{subject.media_type}:{subject.id}",
        signature=_signature_text(signature),
        checksum=signature.sha256 if signature else None,
        size_bytes=signature.size if signature else None,
        facts={"exists": exists, "filename": subject.render_filename()},
    )


def _validation(
    signature: FileSignature | None,
    *,
    verdict: Literal["passed", "failed", "not_run"],
    width: int | None = None,
    height: int | None = None,
    warning: str | None = None,
) -> MutationValidationV1:
    checks = ()
    if signature is not None and width is not None and height is not None:
        checks = (
            MutationInvariantV1(
                code="image_decode", passed=True, message="Candidate decoded as an image."
            ),
            MutationInvariantV1(
                code="nonempty_dimensions",
                passed=width > 0 and height > 0,
                message="Candidate image dimensions are non-empty.",
            ),
        )
    return MutationValidationV1(
        source_probe=(
            {"checksum": signature.sha256, "size_bytes": signature.size}
            if signature is not None
            else {}
        ),
        output_probe={"width": width, "height": height}
        if width is not None and height is not None
        else {},
        invariant_checks=checks,
        warnings=(warning,) if warning else (),
        verdict=verdict,
    )


def _atomic(
    context: ExecutionContext, *, published: bool = False, uncertain: bool = False
) -> MutationAtomicityV1:
    return MutationAtomicityV1(
        group_id=(
            f"poster:{context.delivery.canonical_job_id}:"
            f"{context.attempt.attempt_id}:{context.attempt.fence_token}"
        ),
        boundary="single_target",
        published=published,
        rollback_available=True,
        uncertain_state=uncertain,
    )


def _boundary(subject: PosterSubject) -> tuple[FilesystemBoundary, ClassifiedPath]:
    if not subject.folder_raw:
        raise PosterMutationError("poster destination folder is unavailable")
    folder = safe_translate_and_validate(subject.folder_raw, source=subject.path_source)
    if not folder.is_dir():
        raise PosterMutationError("poster destination folder is unavailable")
    boundary = FilesystemBoundary(
        {
            "data": RootSpec(
                name="data",
                path=Path(settings.DATA_DIR),
                purpose="poster candidates, caches, and backups",
                access="read_write",
                allow_symlinks=False,
            ),
            "subject": RootSpec(
                name="subject",
                path=folder,
                purpose="deployed subject artwork",
                access="read_write",
                allow_symlinks=False,
                same_filesystem=True,
            ),
        }
    )
    return boundary, boundary.from_key("subject", subject.render_filename())


def _validate_stored_destination(subject: PosterSubject) -> None:
    """Reject stale or poisoned projection paths before touching canonical bytes."""
    stored = subject.entity.poster_path
    if not stored:
        return
    if not subject.folder_raw:
        raise PosterMutationError("poster destination folder is unavailable")
    folder = safe_translate_and_validate(subject.folder_raw, source=subject.path_source)
    expected = (folder / subject.render_filename()).resolve(strict=False)
    if Path(stored).resolve(strict=False) != expected:
        raise PosterMutationError("stored poster path does not match the canonical destination")


def _optional_signature(
    boundary: FilesystemBoundary, classified: ClassifiedPath
) -> FileSignature | None:
    try:
        return file_signature(boundary, classified)
    except (OSError, ValueError):
        return None


def _decode_image(boundary: FilesystemBoundary, source: ClassifiedPath) -> tuple[int, int]:
    fd = boundary.open_read(source)
    try:
        with os.fdopen(fd, "rb", closefd=False) as stream, Image.open(stream) as image:
            image.verify()
        os.lseek(fd, 0, os.SEEK_SET)
        with os.fdopen(fd, "rb", closefd=False) as stream, Image.open(stream) as image:
            width, height = image.size
            if width < 1 or height < 1:
                raise PosterMutationError("candidate image has empty dimensions")
            return width, height
    except (UnidentifiedImageError, OSError) as exc:
        raise PosterMutationError("candidate image validation failed") from exc
    finally:
        os.close(fd)


async def _resolve_deploy_candidate(
    context: ExecutionContext,
    subject: PosterSubject,
    boundary: FilesystemBoundary,
    selection: PosterCandidateSelectionV1,
) -> ClassifiedPath:
    async with context.session_factory() as session:
        if selection.source == "pipeline_run":
            run = await session.get(PipelineRun, selection.run_id)
            if run is None:
                raise PosterMutationError("candidate pipeline run is unavailable")
            expected_id = {
                "movie": run.movie_id,
                "series": run.series_id,
                "season": run.season_id,
            }[subject.media_type]
            if expected_id != subject.id:
                raise PosterMutationError("candidate selection belongs to another subject")
            if selection.artifact_id is not None:
                artifact = await session.get(JobArtifact, selection.artifact_id)
                metadata = (
                    artifact.artifact_metadata
                    if artifact is not None and isinstance(artifact.artifact_metadata, dict)
                    else {}
                )
                if (
                    artifact is None
                    or artifact.status != "available"
                    or artifact.kind != "evidence_image"
                    or artifact.job_id != run.job_id
                    or artifact.storage_key != selection.storage_key
                    or artifact.checksum != selection.expected_checksum
                    or metadata.get("candidate_reference") != selection.candidate_reference
                ):
                    raise PosterMutationError("candidate artifact identity is inconsistent")
                _artifact_boundary, stored = await verify_physical_artifact(artifact)
                stored_path = stored.root.resolved() / stored.key.value
                workspace = (
                    context.workspace.directory.root.resolved()
                    / context.workspace.directory.key.value
                    / "deploy-candidate.jpg"
                )
                copied = await context.io.copy(stored_path, workspace)
                if copied.sha256 != selection.expected_checksum:
                    raise PosterMutationError("candidate artifact changed while staging")
                return boundary.classify(workspace, roots=("data",), require_file=True)
            raise PosterMutationError("pipeline candidate is missing artifact identity")

        if (
            selection.storage_key
            != f"subject-artwork/{selection.source_kind}/{selection.source_id}"
        ):
            raise PosterMutationError("subject-artwork storage identity is inconsistent")
        source_subject = await _load_subject(session, selection.source_kind, selection.source_id)
        choices = [source_subject.backup_file()]
        cache = source_subject.cache_paths()
        if cache is not None:
            choices.append(cache[0])
        if source_subject.entity.poster_path:
            choices.append(Path(source_subject.entity.poster_path))
        for choice in choices:
            for root in ("data", "subject"):
                try:
                    return boundary.classify(choice, roots=(root,), require_file=True)
                except ValueError:
                    continue
        raise PosterMutationError("source subject artwork bytes are unavailable")


def _restore_candidate(
    subject: PosterSubject,
    boundary: FilesystemBoundary,
    request: PosterRestoreRequestV1,
) -> tuple[ClassifiedPath, str]:
    for source in request.allowed_sources:
        paths = [subject.backup_file()] if source == "backup" else []
        cache = subject.cache_paths()
        if source == "cache" and cache is not None:
            paths.append(cache[0])
        for path in paths:
            try:
                candidate = boundary.classify(path, roots=("data",), require_file=True)
                return candidate, source
            except ValueError:
                continue
    raise PosterMutationError("no recorded poster restore source is available")


async def _publish_copy(
    context: ExecutionContext,
    boundary: FilesystemBoundary,
    source: ClassifiedPath,
    destination: ClassifiedPath,
    *,
    expected: FileSignature | None,
) -> FileSignature:
    parent_key = "/".join(destination.key.parts[:-1])
    if parent_key:
        directory = boundary.from_key(destination.root.name, parent_key)
        with contextlib.suppress(FileExistsError):
            boundary.create_directory(directory, parents=True)
        staged, fd = boundary.temporary_file(directory, prefix=".marquee-poster-")
    else:
        staged, fd = boundary.temporary_root_file(destination.root.name, prefix=".marquee-poster-")
    try:
        await context.io.confined_copy_to_fd(boundary, source, fd)
        return await PublicationCoordinator(
            boundary,
            maximum_bytes=32 * 1024 * 1024,
            execution_io=context.io,
        ).publish(
            staged=staged,
            destination=destination,
            expected_destination=expected,
            fence=context.writer,
        )
    except BaseException:
        with contextlib.suppress(Exception):
            boundary.delete_file(staged, missing_ok=True)
        raise


async def _create_backup(
    context: ExecutionContext,
    subject: PosterSubject,
    boundary: FilesystemBoundary,
    destination: ClassifiedPath,
    before: FileSignature | None,
) -> MutationBackupV1 | None:
    if before is None:
        return None
    key = f"jmc5/poster-backups/{subject.media_type}/{subject.id}/{before.sha256}.jpg"
    backup = boundary.from_key("data", key)
    existing = _optional_signature(boundary, backup)
    if existing is None:
        copied = await _publish_copy(context, boundary, destination, backup, expected=None)
    elif existing.sha256 == before.sha256:
        copied = existing
    else:
        raise PosterMutationError("recorded backup key conflicts with existing bytes")
    return MutationBackupV1(
        artifact_key=key,
        checksum=copied.sha256,
        size_bytes=copied.size,
        source_signature=_signature_text(before),
        retention="recoverable_artwork",
        restore_eligible=True,
    )


async def _owns(context: ExecutionContext) -> bool:
    async with context.session_factory() as session:
        return await context.writer.owns_current_attempt(session)


async def _persist_before(
    context: ExecutionContext,
    subject: PosterSubject,
    target: MutationTargetV1,
    before: MutationSnapshotV1,
    expected: MutationSnapshotV1 | None,
    source_signature: FileSignature | None,
) -> None:
    evidence = MutationEvidenceV1(
        requested_target=target,
        expected_target=expected,
        actual_target=before,
        validation=_validation(source_signature, verdict="not_run"),
        atomicity=_atomic(context),
    )
    async with context.session_factory() as session, session.begin():
        if not await context.writer.owns_current_attempt(session):
            raise PosterMutationError("stale fence rejected mutation planning")
        detail = await session.get(MediaOperationDetail, context.delivery.canonical_job_id)
        if detail is None:
            detail = MediaOperationDetail(
                job_id=context.delivery.canonical_job_id,
                operation_kind=context.definition.job_type,
                media_snapshot={
                    "kind": subject.media_type,
                    "subject_id": subject.id,
                    "title": subject.title,
                },
                target_snapshot=before.model_dump(mode="json", exclude_none=True),
                input_signature=(source_signature.sha256 if source_signature else "none"),
            )
            session.add(detail)
        apply_mutation_evidence(detail, evidence)


async def _persist_final(
    context: ExecutionContext,
    request: (
        PosterDeployRequestV1
        | PosterRestoreRequestV1
        | PosterResetRequestV1
        | PosterBackupRequestV1
    ),
    *,
    evidence: MutationEvidenceV1,
    checksum: str | None,
    backup_path: str | None,
    source: str | None,
    source_reference: str | None,
    update_projection: bool = True,
) -> None:
    async with context.session_factory() as session, session.begin():
        if not await context.writer.owns_current_attempt(session):
            raise PosterMutationError("stale fence rejected artwork state commit")
        entity = await session.scalar(
            select(_model(request.target_kind))
            .where(_model(request.target_kind).id == request.target_id)
            .with_for_update()
        )
        if entity is None:
            raise PosterMutationError("poster subject retired before state commit")
        subject = await _load_subject(session, request.target_kind, request.target_id)
        if not update_projection:
            pass
        elif isinstance(request, PosterResetRequestV1):
            entity.poster_path = None
            entity.poster_source = None
            entity.poster_source_url = None
            entity.poster_ai_selected = False
            if hasattr(entity, "poster_embedding"):
                entity.poster_embedding = None
            entity.poster_sha256 = None
            entity.poster_phash = None
            entity.poster_user_approved = False
            entity.poster_deployed_filename = None
            entity.poster_deployed_at = None
        else:
            if not subject.folder_raw:
                raise PosterMutationError("poster destination folder is unavailable")
            folder = safe_translate_and_validate(subject.folder_raw, source=subject.path_source)
            entity.poster_path = str(folder / subject.render_filename())
            entity.poster_sha256 = checksum
            entity.poster_deployed_filename = subject.render_filename()
            entity.poster_deployed_at = datetime.now(UTC)
            if isinstance(request, PosterDeployRequestV1):
                entity.poster_ai_selected = request.ai_selected
                entity.poster_user_approved = request.user_approved
                entity.poster_source = source
                entity.poster_source_url = source_reference
        if backup_path is not None:
            entity.poster_local_backup_path = backup_path
        detail = await session.get(MediaOperationDetail, context.delivery.canonical_job_id)
        if detail is None:
            raise PosterMutationError("mutation evidence disappeared before state commit")
        apply_mutation_evidence(detail, evidence)
        session.add(
            ArtworkEvent(
                **subject.event_fk_kwargs(),
                action=context.definition.job_type.removeprefix("poster_"),
                source="canonical_job",
                detail=json.dumps(
                    {
                        "job_id": context.delivery.canonical_job_id,
                        "checksum": checksum,
                        "backup_key": evidence.backup.artifact_key if evidence.backup else None,
                        "published": evidence.atomicity.published,
                    },
                    sort_keys=True,
                ),
            )
        )


def _result(
    *,
    outcome: str,
    reason: str,
    message: str,
    target: MutationTargetV1,
    status: MutationTargetStatus,
    before: MutationSnapshotV1,
    expected: MutationSnapshotV1 | None,
    actual: MutationSnapshotV1,
    validation: MutationValidationV1,
    atomicity: MutationAtomicityV1,
    backup: MutationBackupV1 | None,
    publish: MutationPublishV1 | None,
    changed: bool,
) -> dict[str, object]:
    return PosterMutationResultV1(
        outcome=outcome,
        reason_code=reason,
        message=message,
        requested_targets=(target,),
        target_outcomes=(
            MutationTargetOutcomeV1(
                target=target,
                status=status,
                stage="finalizing",
                reason_code=reason,
                message=message,
                before=before,
                expected=expected,
                actual=actual,
                bytes_changed=changed,
                product_state_changed=changed,
            ),
        ),
        validation=validation,
        atomicity=atomicity,
        backup=backup,
        publish=publish,
    ).model_dump(mode="json", exclude_none=True)


async def _execute_copy(
    context: ExecutionContext,
    request: PosterDeployRequestV1 | PosterRestoreRequestV1,
    candidate: ClassifiedPath,
    candidate_source: str,
) -> dict[str, object]:
    async with context.session_factory() as session:
        subject = await _load_subject(session, request.target_kind, request.target_id)
    target = _target(subject, context.definition.job_type)
    _validate_stored_destination(subject)
    boundary, destination = _boundary(subject)
    source_signature = await asyncio.to_thread(file_signature, boundary, candidate)
    expected_checksum = (
        request.candidate.expected_checksum
        if isinstance(request, PosterDeployRequestV1)
        else subject.entity.poster_sha256
    )
    if expected_checksum and source_signature.sha256 != expected_checksum:
        raise PosterMutationError("candidate checksum changed after enqueue")
    width, height = await asyncio.to_thread(_decode_image, boundary, candidate)
    before_signature = await asyncio.to_thread(_optional_signature, boundary, destination)
    before = _snapshot(subject, before_signature, exists=before_signature is not None)
    expected = _snapshot(subject, source_signature, exists=True)
    await _persist_before(context, subject, target, before, expected, source_signature)
    validation = _validation(source_signature, verdict="passed", width=width, height=height)
    if before_signature is not None and before_signature.sha256 == source_signature.sha256:
        atomicity = _atomic(context)
        actual = _snapshot(subject, before_signature, exists=True)
        evidence = MutationEvidenceV1(
            requested_target=target,
            expected_target=expected,
            actual_target=actual,
            validation=validation,
            atomicity=atomicity,
        )
        await _persist_final(
            context,
            request,
            evidence=evidence,
            checksum=source_signature.sha256,
            backup_path=None,
            source=candidate_source,
            source_reference=None,
            update_projection=False,
        )
        return _result(
            outcome="no_change",
            reason="already_identical",
            message="The deployed poster already matches the selected bytes.",
            target=target,
            status=MutationTargetStatus.SKIPPED,
            before=before,
            expected=expected,
            actual=actual,
            validation=validation,
            atomicity=atomicity,
            backup=None,
            publish=None,
            changed=False,
        )

    backup = await _create_backup(context, subject, boundary, destination, before_signature)
    source_now = await asyncio.to_thread(file_signature, boundary, candidate)
    destination_now = await asyncio.to_thread(_optional_signature, boundary, destination)
    require_publication_preconditions(
        fence_current=await _owns(context),
        cancellation_requested=context.cancellation.cancel_called,
        source_current=source_now == source_signature,
        destination_confined=destination_now == before_signature,
    )
    published = await _publish_copy(
        context,
        boundary,
        candidate,
        destination,
        expected=before_signature,
    )
    actual = _snapshot(subject, published, exists=True)
    if published.sha256 != source_signature.sha256:
        raise PublicationError("poster post-publication checksum validation failed")
    atomicity = _atomic(context, published=True)
    publish = MutationPublishV1(
        candidate_checksum=published.sha256,
        candidate_signature=_signature_text(source_signature),
        destination_identity=f"poster:{subject.media_type}:{subject.id}",
        fence_token=context.attempt.fence_token,
        fsync_result="succeeded",
        replace_result="succeeded",
        post_publish_rescan={"checksum": published.sha256, "size_bytes": published.size},
        reconciliation_state="not_required",
    )
    evidence = MutationEvidenceV1(
        requested_target=target,
        expected_target=expected,
        actual_target=actual,
        validation=validation,
        atomicity=atomicity,
        backup=backup,
        publish=publish,
    )
    backup_path = None
    if backup is not None:
        backup_path = str(Path(settings.DATA_DIR) / backup.artifact_key)
    source_reference = None
    if (
        isinstance(request, PosterDeployRequestV1)
        and request.candidate.source == "pipeline_run"
        and request.candidate.candidate_reference
    ):
        source_reference = tmdb_original_url(request.candidate.candidate_reference)
    await _persist_final(
        context,
        request,
        evidence=evidence,
        checksum=published.sha256,
        backup_path=backup_path,
        source=candidate_source,
        source_reference=source_reference,
    )
    return _result(
        outcome="succeeded",
        reason="poster_published",
        message="The poster was validated and atomically published.",
        target=target,
        status=MutationTargetStatus.SUCCEEDED,
        before=before,
        expected=expected,
        actual=actual,
        validation=validation,
        atomicity=atomicity,
        backup=backup,
        publish=publish,
        changed=True,
    )


async def execute_poster_deploy(context: ExecutionContext) -> dict[str, object]:
    request = PosterDeployRequestV1.model_validate(context.request)
    async with context.session_factory() as session:
        subject = await _load_subject(session, request.target_kind, request.target_id)
    boundary, _destination = _boundary(subject)
    candidate = await _resolve_deploy_candidate(context, subject, boundary, request.candidate)
    result = await _execute_copy(context, request, candidate, request.candidate.source)
    async with context.session_factory() as session:
        exemplar, validation = await record_deployment_effect(
            session,
            deployment_job_id=context.delivery.canonical_job_id,
            result=result,
        )
        exemplar_id = exemplar.id if exemplar is not None else None
        source_artifact_id = exemplar.candidate_artifact_id if exemplar is not None else None
        await session.commit()
    if exemplar_id is None or source_artifact_id is None or validation is None:
        return result
    async with context.session_factory() as session:
        source_artifact = await session.get(JobArtifact, source_artifact_id)
        if source_artifact is None:
            raise PosterMutationError("pending taste exemplar lost its candidate artifact")
        pinned = await pin_candidate_artifact(
            source_artifact,
            deployment_job_id=context.delivery.canonical_job_id,
            deployment_attempt_id=context.attempt.attempt_id,
            deployment_fence_token=context.attempt.fence_token,
            session=session,
        )
        await session.commit()
    async with context.session_factory() as session:
        current = await session.get(TasteExemplar, exemplar_id)
        was_active = current is not None and current.status == "active"
        activated = await activate_exemplar(
            session,
            exemplar_id=exemplar_id,
            retained_artifact_id=pinned.id,
            deployment_result={
                **validation,
                "job_id": context.delivery.canonical_job_id,
                "attempt_id": context.attempt.attempt_id,
                "fence_token": context.attempt.fence_token,
            },
        )
        if activated.status == "active" and not was_active:
            namespaces = (
                ("movies", "tv") if activated.namespace == "global" else (activated.namespace,)
            )
            await schedule_profile_builds(
                session,
                namespaces=namespaces,
                initiator_identifier="poster-deploy-post-effect",
            )
        await session.commit()
    return result


async def execute_poster_restore(context: ExecutionContext) -> dict[str, object]:
    request = PosterRestoreRequestV1.model_validate(context.request)
    async with context.session_factory() as session:
        subject = await _load_subject(session, request.target_kind, request.target_id)
    boundary, _destination = _boundary(subject)
    candidate, source = _restore_candidate(subject, boundary, request)
    return await _execute_copy(context, request, candidate, source)


async def execute_poster_reset(context: ExecutionContext) -> dict[str, object]:
    request = PosterResetRequestV1.model_validate(context.request)
    async with context.session_factory() as session:
        subject = await _load_subject(session, request.target_kind, request.target_id)
    target = _target(subject, context.definition.job_type)
    _validate_stored_destination(subject)
    boundary, destination = _boundary(subject)
    before_signature = await asyncio.to_thread(_optional_signature, boundary, destination)
    before = _snapshot(subject, before_signature, exists=before_signature is not None)
    expected = _snapshot(subject, None, exists=False)
    await _persist_before(context, subject, target, before, expected, before_signature)
    if before_signature is None and not subject.entity.poster_path:
        validation = _validation(None, verdict="passed", warning="poster already absent")
        atomicity = _atomic(context)
        evidence = MutationEvidenceV1(
            requested_target=target,
            expected_target=expected,
            actual_target=expected,
            validation=validation,
            atomicity=atomicity,
        )
        await _persist_final(
            context,
            request,
            evidence=evidence,
            checksum=None,
            backup_path=None,
            source=None,
            source_reference=None,
            update_projection=False,
        )
        return _result(
            outcome="no_change",
            reason="already_absent",
            message="The subject has no deployed poster.",
            target=target,
            status=MutationTargetStatus.SKIPPED,
            before=before,
            expected=expected,
            actual=expected,
            validation=validation,
            atomicity=atomicity,
            backup=None,
            publish=None,
            changed=False,
        )
    if before_signature is None:
        raise PosterMutationError("poster database state does not match the destination")
    backup = await _create_backup(context, subject, boundary, destination, before_signature)
    require_publication_preconditions(
        fence_current=await _owns(context),
        cancellation_requested=context.cancellation.cancel_called,
        source_current=True,
        destination_confined=(
            await asyncio.to_thread(_optional_signature, boundary, destination) == before_signature
        ),
    )
    await PublicationCoordinator(boundary, execution_io=context.io).delete(
        destination=destination,
        expected_destination=before_signature,
        fence=context.writer,
    )
    if await asyncio.to_thread(_optional_signature, boundary, destination) is not None:
        raise PublicationError("poster remained after atomic deletion")
    validation = _validation(None, verdict="passed")
    atomicity = _atomic(context, published=True)
    publish = MutationPublishV1(
        candidate_checksum=before_signature.sha256,
        candidate_signature=_signature_text(before_signature),
        destination_identity=f"poster:{subject.media_type}:{subject.id}",
        fence_token=context.attempt.fence_token,
        fsync_result="succeeded",
        replace_result="succeeded",
        post_publish_rescan={"exists": False},
        reconciliation_state="not_required",
    )
    evidence = MutationEvidenceV1(
        requested_target=target,
        expected_target=expected,
        actual_target=expected,
        validation=validation,
        atomicity=atomicity,
        backup=backup,
        publish=publish,
    )
    await _persist_final(
        context,
        request,
        evidence=evidence,
        checksum=None,
        backup_path=(str(Path(settings.DATA_DIR) / backup.artifact_key) if backup else None),
        source=None,
        source_reference=None,
    )
    return _result(
        outcome="succeeded",
        reason="poster_reset",
        message="The deployed poster was backed up and atomically removed.",
        target=target,
        status=MutationTargetStatus.SUCCEEDED,
        before=before,
        expected=expected,
        actual=expected,
        validation=validation,
        atomicity=atomicity,
        backup=backup,
        publish=publish,
        changed=True,
    )


async def execute_poster_backup_subject(context: ExecutionContext) -> dict[str, object]:
    request = PosterBackupRequestV1.model_validate(context.request)
    async with context.session_factory() as session:
        subject = await _load_subject(session, request.target_kind, request.target_id)
    target = _target(subject, context.definition.job_type)
    _validate_stored_destination(subject)
    boundary, destination = _boundary(subject)
    before_signature = await asyncio.to_thread(_optional_signature, boundary, destination)
    before = _snapshot(subject, before_signature, exists=before_signature is not None)
    await _persist_before(context, subject, target, before, before, before_signature)
    if before_signature is None:
        validation = _validation(None, verdict="passed", warning="deployed poster is absent")
        atomicity = _atomic(context)
        evidence = MutationEvidenceV1(
            requested_target=target,
            expected_target=before,
            actual_target=before,
            validation=validation,
            atomicity=atomicity,
        )
        await _persist_final(
            context,
            request,
            evidence=evidence,
            checksum=None,
            backup_path=None,
            source=None,
            source_reference=None,
            update_projection=False,
        )
        return _result(
            outcome="no_change",
            reason="deployed_poster_absent",
            message="There is no deployed poster to back up.",
            target=target,
            status=MutationTargetStatus.SKIPPED,
            before=before,
            expected=before,
            actual=before,
            validation=validation,
            atomicity=atomicity,
            backup=None,
            publish=None,
            changed=False,
        )

    backup_key = (
        f"jmc5/poster-backups/{subject.media_type}/{subject.id}/{before_signature.sha256}.jpg"
    )
    backup_destination = boundary.from_key("data", backup_key)
    existed = await asyncio.to_thread(_optional_signature, boundary, backup_destination)
    backup = await _create_backup(context, subject, boundary, destination, before_signature)
    if backup is None:
        raise PosterMutationError("poster backup evidence was not created")
    changed = existed is None
    validation = _validation(before_signature, verdict="passed")
    atomicity = _atomic(context, published=changed)
    publish = (
        MutationPublishV1(
            candidate_checksum=backup.checksum,
            candidate_signature=_signature_text(before_signature),
            destination_identity=f"poster-backup:{subject.media_type}:{subject.id}",
            fence_token=context.attempt.fence_token,
            fsync_result="succeeded",
            replace_result="succeeded",
            post_publish_rescan={"checksum": backup.checksum, "size_bytes": backup.size_bytes},
            reconciliation_state="not_required",
        )
        if changed
        else None
    )
    evidence = MutationEvidenceV1(
        requested_target=target,
        expected_target=before,
        actual_target=before,
        validation=validation,
        atomicity=atomicity,
        backup=backup,
        publish=publish,
    )
    await _persist_final(
        context,
        request,
        evidence=evidence,
        checksum=before_signature.sha256,
        backup_path=str(Path(settings.DATA_DIR) / backup.artifact_key),
        source=None,
        source_reference=None,
        update_projection=False,
    )
    return _result(
        outcome="succeeded" if changed else "no_change",
        reason="poster_backed_up" if changed else "backup_already_identical",
        message=(
            "The deployed poster was copied into recoverable storage."
            if changed
            else "An identical recoverable backup already exists."
        ),
        target=target,
        status=(MutationTargetStatus.SUCCEEDED if changed else MutationTargetStatus.SKIPPED),
        before=before,
        expected=before,
        actual=before,
        validation=validation,
        atomicity=atomicity,
        backup=backup,
        publish=publish,
        changed=changed,
    )


register_execution_handler("poster_deploy", execute_poster_deploy)
register_execution_handler("poster_restore", execute_poster_restore)
register_execution_handler("poster_reset", execute_poster_reset)
register_execution_handler("poster_backup_subject", execute_poster_backup_subject)
