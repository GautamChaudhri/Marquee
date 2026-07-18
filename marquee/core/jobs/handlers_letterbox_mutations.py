"""Candidate-first, freshly probed letterbox metadata mutation handlers."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select

from marquee.core.jobs.delivery import ExecutionContext, register_execution_handler
from marquee.core.jobs.letterbox_mutation_documents import (
    LetterboxApplyRequestV1,
    LetterboxMutationResultV1,
    LetterboxProbeV1,
    LetterboxRemoveRequestV1,
)
from marquee.core.jobs.media_backups import create_execution_media_backup
from marquee.core.jobs.media_mutation_support import (
    confined_boundary,
    current_signature,
    load_media_file,
    physical,
)
from marquee.core.jobs.mutation_documents import (
    MutationJobOutcome,
    MutationSnapshotV1,
    MutationTargetOutcomeV1,
    MutationTargetStatus,
    MutationTargetV1,
    MutationValidationV1,
)
from marquee.core.jobs.process_launcher import ProcessLaunchError
from marquee.core.jobs.progress import MeasurementMode, ProgressMeasurementUpdate
from marquee.core.jobs.progress_service import ProgressObservation, progress_writer
from marquee.core.jobs.publication import execution_file_signature
from marquee.core.jobs.remux_coordinator import (
    RemuxCancelledError,
    atomicity,
    publish_media_candidate,
    run_mkvpropedit_plan,
)
from marquee.models.letterbox import LetterboxEvent, LetterboxState


class LetterboxMutationError(RuntimeError):
    pass


async def _progress(
    context: ExecutionContext, stage: str, ordinal: int, media_file_id: int
) -> None:
    await progress_writer.safe_write(
        job_id=context.delivery.canonical_job_id,
        attempt_id=context.attempt.attempt_id,
        fence_token=context.attempt.fence_token,
        observation=ProgressObservation(
            stage_key=stage,
            overall=ProgressMeasurementUpdate(
                scope_id=f"letterbox-mutation:{media_file_id}:overall",
                mode=MeasurementMode.INDETERMINATE,
            ),
            current=ProgressMeasurementUpdate(
                scope_id=f"letterbox-mutation:{media_file_id}:{stage}",
                mode=MeasurementMode.INDETERMINATE,
            ),
            producer_ordinal=ordinal,
        ),
    )


def _crop_facts(probe: LetterboxProbeV1) -> dict[str, object]:
    return {
        "crop_present": probe.crop_present,
        "crop_top": probe.crop_top,
        "crop_bottom": probe.crop_bottom,
        "crop_left": probe.crop_left,
        "crop_right": probe.crop_right,
        "width": probe.width,
        "height": probe.height,
    }


def _snapshot(probe: LetterboxProbeV1, *, media_file_id: int) -> MutationSnapshotV1:
    return MutationSnapshotV1(
        identity=f"media-file:{media_file_id}",
        signature=probe.source_signature,
        facts=_crop_facts(probe),
    )


def _target(
    *, media_file_id: int, operation: str, subject_kind: str, subject_ids: tuple[int, ...]
) -> MutationTargetV1:
    return MutationTargetV1(
        key=f"media-file:{media_file_id}",
        kind="media_file",
        label=f"{subject_kind.title()} media file {media_file_id}",
        operation=operation,
        selector_facts={"subject_kind": subject_kind, "subject_ids": list(subject_ids)},
    )


async def _probe(context: ExecutionContext, path: Path) -> LetterboxProbeV1:
    try:
        process = await context.process_launcher.launch("mkvmerge", ["-J", str(path)])
    except ProcessLaunchError as exc:
        raise LetterboxMutationError("mkvmerge is unavailable for authoritative crop probing") from exc
    summary = await process.wait()
    if summary.exit_code != 0 or summary.stdout.truncated:
        raise LetterboxMutationError("mkvmerge could not produce complete crop evidence")
    try:
        payload = json.loads(summary.stdout.captured.decode("utf-8", "replace"))
        tracks = payload["tracks"]
        video = next(track for track in tracks if track.get("type") == "video")
        properties = video["properties"]
        dimensions = properties.get("pixel_dimensions") or properties.get("display_dimensions")
        width_text, height_text = str(dimensions).lower().split("x", maxsplit=1)
        container = payload.get("container", {}).get("type")
        crop_keys = {key: value for key, value in properties.items() if "crop" in key.lower()}
    except (KeyError, StopIteration, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise LetterboxMutationError("mkvmerge returned incomplete crop evidence") from exc
    if not container or "matroska" not in str(container).lower():
        raise LetterboxMutationError("letterbox metadata mutation requires a Matroska container")
    crop_present = bool(crop_keys)
    if "cropping" in crop_keys:
        try:
            crop_left, crop_top, crop_right, crop_bottom = (
                int(value) for value in str(crop_keys["cropping"]).split(",")
            )
        except ValueError as exc:
            raise LetterboxMutationError("mkvmerge returned invalid crop coordinates") from exc
    else:
        crop_top = int(crop_keys.get("pixel_crop_top", 0) or 0)
        crop_bottom = int(crop_keys.get("pixel_crop_bottom", 0) or 0)
        crop_left = int(crop_keys.get("pixel_crop_left", 0) or 0)
        crop_right = int(crop_keys.get("pixel_crop_right", 0) or 0)
    return LetterboxProbeV1(
        source_signature=current_signature(path),
        container=str(container),
        video_track_id=int(video.get("id", 0)),
        width=int(width_text),
        height=int(height_text),
        crop_present=crop_present,
        crop_top=crop_top if crop_present else None,
        crop_bottom=crop_bottom if crop_present else None,
        crop_left=crop_left if crop_present else None,
        crop_right=crop_right if crop_present else None,
    )


def _failed(
    *,
    target: MutationTargetV1,
    group_id: str,
    reason_code: str,
    message: str,
    stage: str,
    before_probe: LetterboxProbeV1 | None = None,
    uncertain: bool = False,
    actual_probe: LetterboxProbeV1 | None = None,
) -> dict[str, object]:
    result = LetterboxMutationResultV1(
        outcome=(
            MutationJobOutcome.UNSAFE
            if uncertain
            else MutationJobOutcome.CANCELLED
            if reason_code == "cancelled"
            else MutationJobOutcome.FAILED
        ),
        reason_code=reason_code,
        message=message,
        requested_targets=(target,),
        target_outcomes=(
            MutationTargetOutcomeV1(
                target=target,
                status=MutationTargetStatus.NOT_APPLIED,
                stage=stage,
                reason_code=reason_code,
                message=message,
                before=_snapshot(before_probe, media_file_id=int(target.key.rsplit(":", 1)[1]))
                if before_probe
                else None,
                actual=_snapshot(actual_probe, media_file_id=int(target.key.rsplit(":", 1)[1]))
                if actual_probe
                else None,
                bytes_changed=False,
                product_state_changed=False,
            ),
        ),
        validation=MutationValidationV1(
            source_probe=_crop_facts(before_probe) if before_probe else {},
            output_probe=_crop_facts(actual_probe) if actual_probe else {},
            verdict="not_run" if uncertain else "failed",
        ),
        atomicity=atomicity(group_id, published=False, uncertain=uncertain),
        before_probe=before_probe,
        actual_probe=actual_probe,
    )
    return result.model_dump(mode="json")


async def _state_matches(context: ExecutionContext, request: Any) -> bool:
    criterion = (
        LetterboxState.movie_id.in_(request.subject_ids)
        if request.subject_kind == "movie"
        else LetterboxState.episode_id.in_(request.subject_ids)
    )
    async with context.session_factory() as session:
        states = (await session.scalars(select(LetterboxState).where(criterion))).all()
    if len(states) != len(request.subject_ids):
        return False
    before = request.before
    return all(
        state.media_type == request.subject_kind
        and state.status == before.status
        and state.confidence == before.confidence
        and state.variable_ar == before.variable_ar
        and state.source_width == before.source_width
        and state.source_height == before.source_height
        and state.applied_crop_top == before.current_crop_top
        and state.applied_crop_bottom == before.current_crop_bottom
        and state.recommended_crop_top == before.recommended_crop_top
        and state.recommended_crop_bottom == before.recommended_crop_bottom
        for state in states
    )


async def _persist_verified(context: ExecutionContext, request: Any, *, operation: str) -> None:
    criterion = (
        LetterboxState.movie_id.in_(request.subject_ids)
        if request.subject_kind == "movie"
        else LetterboxState.episode_id.in_(request.subject_ids)
    )
    async with context.session_factory() as session, session.begin():
        states = (
            await session.scalars(select(LetterboxState).where(criterion).with_for_update())
        ).all()
        if len(states) != len(request.subject_ids):
            raise LetterboxMutationError("letterbox state disappeared after publication")
        before = request.before
        if not all(
            state.media_type == request.subject_kind
            and state.status == before.status
            and state.confidence == before.confidence
            and state.variable_ar == before.variable_ar
            and state.source_width == before.source_width
            and state.source_height == before.source_height
            and state.applied_crop_top == before.current_crop_top
            and state.applied_crop_bottom == before.current_crop_bottom
            and state.recommended_crop_top == before.recommended_crop_top
            and state.recommended_crop_bottom == before.recommended_crop_bottom
            for state in states
        ):
            raise LetterboxMutationError("letterbox state changed during publication")
        for state in states:
            if operation == "apply":
                state.status = "tagged"
                state.applied_crop_top = request.crop_top
                state.applied_crop_bottom = request.crop_bottom
                state.last_applied_at = datetime.now(UTC)
            else:
                state.applied_crop_top = None
                state.applied_crop_bottom = None
                state.status = "candidate" if state.recommended_crop_top is not None else "skipped"
            state.error = None
            subject_id = state.movie_id if request.subject_kind == "movie" else state.episode_id
            session.add(
                LetterboxEvent(
                    movie_id=subject_id if request.subject_kind == "movie" else None,
                    episode_id=subject_id if request.subject_kind == "episode" else None,
                    media_type=request.subject_kind,
                    subject_snapshot={"kind": request.subject_kind, "id": subject_id},
                    action=operation,
                    source=request.source,
                    detail=json.dumps(
                        {
                            "media_file_id": request.media_file_id,
                            "crop_top": getattr(request, "crop_top", None),
                            "crop_bottom": getattr(request, "crop_bottom", None),
                            "verified": True,
                        },
                        sort_keys=True,
                    ),
                )
            )


async def _execute(context: ExecutionContext, *, operation: str) -> dict[str, object]:
    request = (
        LetterboxApplyRequestV1.model_validate(context.request)
        if operation == "apply"
        else LetterboxRemoveRequestV1.model_validate(context.request)
    )
    target = _target(
        media_file_id=request.media_file_id,
        operation=operation,
        subject_kind=request.subject_kind,
        subject_ids=request.subject_ids,
    )
    group_id = f"letterbox-{operation}:{request.media_file_id}"
    await _progress(context, "resolving", 1, request.media_file_id)
    try:
        resolved = await load_media_file(context, request.media_file_id)
    except Exception as exc:  # noqa: BLE001 - normalized into typed job evidence
        return _failed(
            target=target,
            group_id=group_id,
            reason_code="media_unavailable",
            message=str(exc)[:300] or "the media file is unavailable",
            stage="resolve",
        )
    if resolved.signature != request.before.source_signature or not await _state_matches(context, request):
        return _failed(
            target=target,
            group_id=group_id,
            reason_code="stale_plan",
            message="the sealed media or detection state changed before execution",
            stage="preflight",
        )
    await _progress(context, "probing", 2, request.media_file_id)
    try:
        before_probe = await _probe(context, resolved.path)
    except LetterboxMutationError as exc:
        return _failed(
            target=target,
            group_id=group_id,
            reason_code="probe_failed",
            message=str(exc),
            stage="preflight",
        )
    if (before_probe.width, before_probe.height) != (
        request.before.source_width,
        request.before.source_height,
    ):
        return _failed(
            target=target,
            group_id=group_id,
            reason_code="stale_dimensions",
            message="the encoded dimensions differ from the sealed plan",
            stage="preflight",
            before_probe=before_probe,
        )
    expected_crop = (
        (request.before.current_crop_top, request.before.current_crop_bottom)
        if request.before.current_crop_top is not None
        and request.before.current_crop_bottom is not None
        else None
    )
    actual_before_crop = (
        (before_probe.crop_top, before_probe.crop_bottom) if before_probe.crop_present else None
    )
    if actual_before_crop != expected_crop and request.source != "heal":
        return _failed(
            target=target,
            group_id=group_id,
            reason_code="stale_crop_metadata",
            message="the current crop tags differ from the sealed plan",
            stage="preflight",
            before_probe=before_probe,
        )
    desired = (request.crop_top, request.crop_bottom) if operation == "apply" else None
    if actual_before_crop == desired:
        outcome = MutationTargetOutcomeV1(
            target=target,
            status=MutationTargetStatus.SKIPPED,
            stage="preflight",
            reason_code="already_applied" if operation == "apply" else "already_absent",
            message="the authoritative source probe already matches the requested crop state",
            before=_snapshot(before_probe, media_file_id=request.media_file_id),
            actual=_snapshot(before_probe, media_file_id=request.media_file_id),
            bytes_changed=False,
            product_state_changed=False,
        )
        return LetterboxMutationResultV1(
            outcome=MutationJobOutcome.NO_CHANGE,
            reason_code=outcome.reason_code,
            message=outcome.message,
            requested_targets=(target,),
            target_outcomes=(outcome,),
            validation=MutationValidationV1(
                source_probe=_crop_facts(before_probe),
                output_probe=_crop_facts(before_probe),
                verdict="passed",
            ),
            atomicity=atomicity(group_id, published=False),
            before_probe=before_probe,
            actual_probe=before_probe,
        ).model_dump(mode="json")

    boundary, source = confined_boundary(resolved.path)
    candidate = boundary.from_key(
        "media", f".marquee-{context.attempt.attempt_id}-{source.key.value}"
    )
    try:
        await _progress(context, "staging", 3, request.media_file_id)
        await context.io.confined_copy(boundary, source, candidate)
        args = ["mkvpropedit", str(physical(candidate)), "--edit", "track:v1"]
        if operation == "apply":
            args.extend(
                [
                    "--set",
                    f"pixel-crop-top={request.crop_top}",
                    "--set",
                    f"pixel-crop-bottom={request.crop_bottom}",
                    "--set",
                    "pixel-crop-left=0",
                    "--set",
                    "pixel-crop-right=0",
                ]
            )
        else:
            for name in ("top", "bottom", "left", "right"):
                args.extend(["--delete", f"pixel-crop-{name}"])
        await _progress(context, "editing", 4, request.media_file_id)
        await run_mkvpropedit_plan(context, boundary=boundary, args=args, candidate=candidate)
        await _progress(context, "validating", 5, request.media_file_id)
        candidate_probe = await _probe(context, physical(candidate))
        if (candidate_probe.width, candidate_probe.height) != (
            before_probe.width,
            before_probe.height,
        ):
            raise LetterboxMutationError("candidate dimensions changed during metadata editing")
        observed = (
            (candidate_probe.crop_top, candidate_probe.crop_bottom)
            if candidate_probe.crop_present
            else None
        )
        if observed != desired or (
            candidate_probe.crop_present
            and (candidate_probe.crop_left != 0 or candidate_probe.crop_right != 0)
        ):
            raise LetterboxMutationError("candidate crop tags did not match the requested state")
        source_signature = await execution_file_signature(context.io, boundary, source)
        await _progress(context, "backing_up", 6, request.media_file_id)
        backup = await create_execution_media_backup(
            context.io,
            boundary,
            source=source,
            subject_key=f"media-file-{request.media_file_id}",
            signature=source_signature,
            suffix=resolved.path.suffix.lstrip("."),
        )
        await _progress(context, "publishing", 7, request.media_file_id)
        await publish_media_candidate(
            context,
            boundary=boundary,
            candidate=candidate,
            destination=source,
            expected_destination=source_signature,
        )
    except Exception as exc:  # noqa: BLE001 - normalized into typed mutation evidence
        boundary.delete_file(candidate, missing_ok=True)
        return _failed(
            target=target,
            group_id=group_id,
            reason_code="cancelled" if isinstance(exc, RemuxCancelledError) else "mutation_failed",
            message=str(exc)[:300],
            stage=getattr(exc, "stage", "validate"),
            before_probe=before_probe,
        )
    try:
        await _progress(context, "rescanning", 8, request.media_file_id)
        actual_probe = await _probe(context, resolved.path)
        actual_crop = (
            (actual_probe.crop_top, actual_probe.crop_bottom) if actual_probe.crop_present else None
        )
        if actual_crop != desired:
            raise LetterboxMutationError("published source did not retain the verified candidate crop")
        await _progress(context, "finalizing", 9, request.media_file_id)
        await _persist_verified(context, request, operation=operation)
    except Exception as exc:  # noqa: BLE001 - publication happened; quarantine uncertainty
        return _failed(
            target=target,
            group_id=group_id,
            reason_code="post_publish_uncertain",
            message=str(exc)[:300],
            stage="rescan",
            before_probe=before_probe,
            uncertain=True,
            actual_probe=locals().get("actual_probe"),
        )
    before_snapshot = _snapshot(before_probe, media_file_id=request.media_file_id)
    actual_snapshot = _snapshot(actual_probe, media_file_id=request.media_file_id)
    target_outcome = MutationTargetOutcomeV1(
        target=target,
        status=MutationTargetStatus.SUCCEEDED,
        stage="rescan",
        reason_code="verified",
        message="crop metadata was published and verified from the actual source",
        before=before_snapshot,
        expected=actual_snapshot,
        actual=actual_snapshot,
        bytes_changed=True,
        product_state_changed=True,
    )
    return LetterboxMutationResultV1(
        outcome=MutationJobOutcome.SUCCEEDED,
        reason_code="applied" if operation == "apply" else "removed",
        message=f"letterbox crop metadata {operation} completed and was freshly verified",
        requested_targets=(target,),
        target_outcomes=(target_outcome,),
        validation=MutationValidationV1(
            source_probe=_crop_facts(before_probe),
            output_probe=_crop_facts(actual_probe),
            verdict="passed",
        ),
        atomicity=atomicity(group_id, published=True),
        backup=backup,
        before_probe=before_probe,
        actual_probe=actual_probe,
    ).model_dump(mode="json")


async def execute_letterbox_apply(context: ExecutionContext) -> dict[str, object]:
    return await _execute(context, operation="apply")


async def execute_letterbox_remove(context: ExecutionContext) -> dict[str, object]:
    return await _execute(context, operation="remove")


register_execution_handler("letterbox_apply", execute_letterbox_apply)
register_execution_handler("letterbox_remove", execute_letterbox_remove)
