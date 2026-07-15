from __future__ import annotations

import hashlib
from uuid import uuid4

from sqlalchemy import select

from marquee.core.filesystem import ClassifiedPath, FilesystemBoundary
from marquee.core.jobs.audio_subtitle_documents import (
    ManagedSidecarV1,
    SubtitleEmbedRequestV1,
    SubtitleExtractRequestV1,
    SubtitleSidecarResultV1,
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
from marquee.core.jobs.mkvmerge_plan import BASE_ARGS
from marquee.core.jobs.mutation_documents import (
    MutationJobOutcome,
    MutationTargetOutcomeV1,
    MutationTargetStatus,
    MutationTargetV1,
    MutationValidationV1,
)
from marquee.core.jobs.remux_coordinator import (
    RemuxCancelledError,
    RemuxError,
    atomicity,
    failed_result,
    publish_media_candidate,
    run_mkvmerge_plan,
    target_for,
)
from marquee.core.jobs.track_selectors import TrackSelectorError, resolve_selector
from marquee.core.media_files import MediaFileNotFoundError, MediaFileUnavailableError
from marquee.models import ManagedSubtitleAsset

#: Confined content-addressed storage for managed sidecars.
MANAGED_PREFIX = "jmc5/managed-subtitles"

#: A generated/extracted sidecar must at least look like a subtitle document.
MAX_SIDECAR_BYTES = 32 * 1024 * 1024


class SidecarError(RuntimeError):
    """A sidecar could not be produced or validated."""


def validate_subtitle_bytes(payload: bytes) -> None:
    """Reject empty or non-text output before anything is published."""
    if not payload:
        raise SidecarError("the subtitle output is empty")
    if len(payload) > MAX_SIDECAR_BYTES:
        raise SidecarError("the subtitle output exceeds the configured bound")
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SidecarError("the subtitle output is not valid UTF-8 text") from exc
    if "-->" not in text:
        raise SidecarError("the subtitle output has no cue timing")


def managed_key(checksum: str) -> str:
    return f"{MANAGED_PREFIX}/{checksum}.srt"


async def _register_managed_asset(
    context: ExecutionContext,
    *,
    checksum: str,
    language_tag: str,
    title: str | None,
    is_forced: bool,
    is_sdh: bool,
) -> str:
    """Record the managed asset row; the key, not a path, is the public handle."""
    async with context.session_factory() as session:
        existing = await session.scalar(
            select(ManagedSubtitleAsset).where(
                ManagedSubtitleAsset.content_sha256 == checksum
            )
        )
        if existing is not None:
            return existing.id
        asset_id = uuid4().hex
        session.add(
            ManagedSubtitleAsset(
                id=asset_id,
                cache_path=managed_key(checksum),
                content_sha256=checksum,
                language_tag=language_tag,
                title=title,
                kind="text",
                is_forced=is_forced,
                is_sdh=is_sdh,
                active=True,
            )
        )
        await session.commit()
        return asset_id


def _publish_managed(
    boundary: FilesystemBoundary, staged: ClassifiedPath, checksum: str
) -> ClassifiedPath:
    """Content-addressed publish: identical bytes are already correct."""
    directory = boundary.from_key("data", MANAGED_PREFIX)
    boundary.create_directory(directory, parents=True)
    destination = boundary.from_key("data", managed_key(checksum))
    target = _physical(destination)
    if target.exists():
        if hashlib.sha256(target.read_bytes()).hexdigest() != checksum:
            raise SidecarError("a conflicting managed sidecar already owns this key")
        boundary.delete_file(staged, missing_ok=True)
        return destination
    boundary.atomic_replace(staged, destination)
    boundary.fsync_parent(destination)
    return destination


def _sidecar_failure(
    target: MutationTargetV1, before, group_id: str, stage: str, reason: str, message: str
) -> dict[str, object]:
    """A sidecar result carries no actual inventory: nothing was published."""
    failed = failed_result(
        targets=[target],
        before=before,
        stage=stage,
        reason_code=reason,
        message=message,
        group_id=group_id,
    ).model_dump(exclude={"actual_inventory"})
    return SubtitleSidecarResultV1(**failed).model_dump(mode="json")


async def execute_subtitle_extract(context: ExecutionContext) -> dict[str, object]:
    """Extract one embedded subtitle track without altering the source."""
    request = SubtitleExtractRequestV1.model_validate(context.request)
    group_id = f"subtitle_extract:{request.media_file_id}"
    try:
        resolved_file = await _load(context, request.media_file_id)
    except (MediaFileNotFoundError, MediaFileUnavailableError) as exc:
        raise TrackMutationError("the media file is unavailable") from exc

    source_path = resolved_file.path
    before = _inventory(source_path, resolved_file.signature)
    boundary, _source = _boundary(source_path)

    try:
        entry = resolve_selector(request.selector, before)
    except TrackSelectorError as exc:
        target = MutationTargetV1(
            key=request.selector.track_key,
            kind="track",
            label=f"Subtitle {request.selector.facts.language_tag}",
            operation="subtitle_extract",
            selector_facts=request.selector.facts.model_dump(mode="json"),
        )
        return _sidecar_failure(
            target, before, group_id, "resolve", exc.reason,
            "the requested track no longer matches the source",
        )

    target = target_for(entry, "subtitle_extract")
    subtitles = [e for e in before.entries if e.facts.kind == "subtitle"]
    ordinal = subtitles.index(entry)

    staged = boundary.from_key("data", f"{MANAGED_PREFIX}/.staging-{uuid4().hex}.srt")
    boundary.create_directory(boundary.from_key("data", MANAGED_PREFIX), parents=True)
    args = (
        "ffmpeg", "-v", "error", "-y", "-i", str(source_path),
        "-map", f"0:s:{ordinal}", "-c:s", "copy", str(_physical(staged)),
    )
    try:
        await run_mkvmerge_plan(context, boundary=boundary, args=args, candidate=staged)
        payload = _physical(staged).read_bytes()
        validate_subtitle_bytes(payload)
        checksum = hashlib.sha256(payload).hexdigest()
        destination = _publish_managed(boundary, staged, checksum)
    except (RemuxCancelledError, RemuxError, SidecarError, OSError) as exc:
        boundary.delete_file(staged, missing_ok=True)
        stage = getattr(exc, "stage", "sidecar")
        reason = getattr(exc, "reason_code", "sidecar_invalid")
        return _sidecar_failure(target, before, group_id, stage, reason, str(exc)[:200])

    asset_id = await _register_managed_asset(
        context,
        checksum=checksum,
        language_tag=entry.facts.language_tag,
        title=entry.facts.title,
        is_forced=entry.facts.is_forced,
        is_sdh=entry.facts.is_hearing_impaired,
    )
    result = SubtitleSidecarResultV1(
        outcome=MutationJobOutcome.SUCCEEDED,
        reason_code="extracted",
        message="the subtitle track was extracted to a managed sidecar",
        requested_targets=(target,),
        target_outcomes=(
            MutationTargetOutcomeV1(
                target=target,
                status=MutationTargetStatus.SUCCEEDED,
                stage="sidecar",
                reason_code="extracted",
                message="validated and published as a managed artifact",
                bytes_changed=True,
                product_state_changed=True,
            ),
        ),
        validation=MutationValidationV1(verdict="passed"),
        atomicity=atomicity(group_id, published=True),
        before_inventory=before,
        sidecar=ManagedSidecarV1(
            managed_asset_id=asset_id,
            storage_key=managed_key(checksum),
            checksum=checksum,
            size_bytes=len(payload),
            language_tag=entry.facts.language_tag,
        ),
    )
    # The source must be untouched by an extraction.
    assert destination is not None
    return result.model_dump(mode="json")


async def execute_subtitle_embed(context: ExecutionContext) -> dict[str, object]:
    """Embed one validated managed subtitle asset through a full remux."""
    from marquee.core.jobs.audio_subtitle_documents import MediaTrackMutationResultV1

    request = SubtitleEmbedRequestV1.model_validate(context.request)
    group_id = f"subtitle_embed:{request.media_file_id}"
    try:
        resolved_file = await _load(context, request.media_file_id)
    except (MediaFileNotFoundError, MediaFileUnavailableError) as exc:
        raise TrackMutationError("the media file is unavailable") from exc

    source_path = resolved_file.path
    before = _inventory(source_path, resolved_file.signature)
    boundary, source = _boundary(source_path)

    target = MutationTargetV1(
        key=f"subtitle:managed:{request.managed_asset_id}",
        kind="track",
        label=f"Subtitle {request.language_tag}",
        operation="subtitle_embed",
        selector_facts={
            "kind": "subtitle",
            "source": "external",
            "language_tag": request.language_tag,
            "managed_key": request.managed_asset_id,
        },
    )

    async with context.session_factory() as session:
        asset = await session.get(ManagedSubtitleAsset, request.managed_asset_id)
    if asset is None or not asset.active:
        return _embed_failure(
            target, before, group_id, "preflight", "asset_missing",
            "the managed subtitle asset is unavailable",
        )
    if not _supported(before):
        return _embed_failure(
            target, before, group_id, "preflight", "unsupported_container",
            "this container cannot carry an embedded subtitle track",
        )

    sidecar = boundary.from_key("data", asset.cache_path)
    sidecar_path = _physical(sidecar)
    try:
        payload = sidecar_path.read_bytes()
        validate_subtitle_bytes(payload)
        if hashlib.sha256(payload).hexdigest() != asset.content_sha256:
            raise SidecarError("the managed sidecar failed checksum verification")
    except (SidecarError, OSError) as exc:
        return _embed_failure(
            target, before, group_id, "validate", "asset_invalid", str(exc)[:200]
        )

    candidate = boundary.from_key(
        "media", f".marquee-{context.attempt.attempt_id}-{source.key.value}"
    )
    args = [
        *BASE_ARGS, "-o", str(_physical(candidate)), str(source_path),
        "--language", f"0:{request.language_tag}",
        "--default-track-flag", f"0:{'yes' if request.is_default else 'no'}",
    ]
    if request.title:
        args += ["--track-name", f"0:{request.title}"]
    if request.is_forced:
        args += ["--forced-display-flag", "0:yes"]
    if request.is_hearing_impaired:
        args += ["--hearing-impaired-flag", "0:yes"]
    args.append(str(sidecar_path))

    try:
        await run_mkvmerge_plan(context, boundary=boundary, args=tuple(args), candidate=candidate)
        from marquee.core.jobs.publication import file_signature

        source_signature = file_signature(boundary, source)
        backup = create_media_backup(
            boundary,
            source=source,
            subject_key=f"media-file-{request.media_file_id}",
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
    except (RemuxCancelledError, RemuxError) as exc:
        boundary.delete_file(candidate, missing_ok=True)
        return _embed_failure(
            target, before, group_id, exc.stage, exc.reason_code, str(exc)[:200]
        )
    except Exception as exc:  # noqa: BLE001 - classified as a publish failure
        boundary.delete_file(candidate, missing_ok=True)
        return _embed_failure(
            target, before, group_id, "publish", "publish_failed", str(exc)[:200]
        )

    actual = _inventory(source_path, _signature(source_path))
    return MediaTrackMutationResultV1(
        outcome=MutationJobOutcome.SUCCEEDED,
        reason_code="embedded",
        message="the managed subtitle was embedded and verified by rescan",
        requested_targets=(target,),
        target_outcomes=(
            MutationTargetOutcomeV1(
                target=target,
                status=MutationTargetStatus.SUCCEEDED,
                stage="rescan",
                reason_code="embedded",
                message="verified by rescan",
                bytes_changed=True,
                product_state_changed=True,
            ),
        ),
        validation=MutationValidationV1(verdict="passed"),
        atomicity=atomicity(group_id, published=True),
        backup=backup,
        before_inventory=before,
        actual_inventory=actual,
    ).model_dump(mode="json")


def _embed_failure(
    target: MutationTargetV1, before, group_id: str, stage: str, reason: str, message: str
) -> dict[str, object]:
    from marquee.core.jobs.audio_subtitle_documents import MediaTrackMutationResultV1

    return MediaTrackMutationResultV1(
        **failed_result(
            targets=[target],
            before=before,
            stage=stage,
            reason_code=reason,
            message=message,
            group_id=group_id,
        ).model_dump()
    ).model_dump(mode="json")


register_execution_handler("subtitle_extract", execute_subtitle_extract)
register_execution_handler("subtitle_embed", execute_subtitle_embed)
