"""Canonical host-side execution and atomic projection for grouped poster analysis."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import uuid
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from marquee.core.jobs.artifact_service import ArtifactError, register_physical_artifact
from marquee.core.jobs.documents import (
    PosterPipelineGroupRequestV1,
    PosterPipelineGroupResultV1,
)
from marquee.core.jobs.execution_progress import ExecutionProgress
from marquee.core.jobs.ml_publication import (
    MlPublicationError,
    acknowledge_consumption,
    resolve_loaded_ranking_residual,
    resolve_loaded_taste_profile,
)
from marquee.core.jobs.poster_group_retry import poster_subject_key
from marquee.core.jobs.poster_pipeline import (
    _MAX_COUNT,
    _STAGE_MAP,
    _map_outcome,
    _media_type,
    _pipeline_baseline_signature,
    _resolved_subject_params,
    _runner_runtime_options,
    _text_gate_params,
    _workspace_dir,
)
from marquee.core.jobs.runner_progress import RunnerProgressBridge
from marquee.models import Job, JobAttempt, PipelineRun

if TYPE_CHECKING:
    from marquee.core.jobs.delivery import ExecutionContext

_MAX_GROUP_RESULT_BYTES = 1024 * 1024


def _member_snapshots(context: ExecutionContext) -> dict[str, dict[str, Any]]:
    snapshot = context.subject if isinstance(context.subject, Mapping) else {}
    raw_members = snapshot.get("members")
    if not isinstance(raw_members, Sequence) or isinstance(raw_members, str | bytes):
        raise RuntimeError("poster group subject snapshot has no members")
    resolved: dict[str, dict[str, Any]] = {}
    for wrapper in raw_members:
        if not isinstance(wrapper, Mapping):
            raise RuntimeError("poster group subject snapshot member is invalid")
        key = wrapper.get("subject_key")
        subject = wrapper.get("subject")
        if not isinstance(key, str) or not isinstance(subject, Mapping) or key in resolved:
            raise RuntimeError("poster group subject snapshot identity is invalid")
        # Delivery freezes durable JSON into MappingProxyType/tuple containers.
        # Materialize each member before passing it to JSON/SQLAlchemy consumers.
        resolved[key] = dict(subject)
    return resolved


async def _stage_personalization(
    context: ExecutionContext,
    *,
    library: str,
    workspace_dir: Path,
) -> tuple[dict[str, Any], str | None, str | None]:
    """Stage one profile/residual pair for the whole homogeneous group."""
    baseline = _pipeline_baseline_signature(context)
    params: dict[str, Any] = {"baseline_signature": baseline}
    profile_resolution_error: str | None = None
    try:
        async with context.session_factory() as session:
            active_profile, profile_load_result = await resolve_loaded_taste_profile(
                session, library=library
            )
    except MlPublicationError as exc:
        active_profile = None
        profile_load_result = None
        profile_resolution_error = str(exc)

    if active_profile is not None:
        copied = await context.io.copy(active_profile.path, workspace_dir / "profile.npz")
        if copied.sha256 != active_profile.checksum:
            (workspace_dir / "profile.npz").unlink(missing_ok=True)
            raise RuntimeError("active taste-profile checksum changed while staging")
        params["taste_profile"] = {
            "generation": active_profile.generation,
            "version": active_profile.version,
            "checksum": active_profile.checksum,
        }
        async with context.session_factory() as session:
            attempt = await session.get(JobAttempt, context.attempt.attempt_id)
            if attempt is not None and attempt.runtime_instance_id is not None:
                await acknowledge_consumption(
                    session,
                    publication=active_profile,
                    consumer_role="poster_pipeline_group",
                    instance_id=attempt.runtime_instance_id,
                    load_result={
                        **(profile_load_result or {}),
                        "supplied_to": "contained_poster_group_runner",
                        "staged_checksum": copied.sha256,
                    },
                )
                await session.commit()

    personalization_mode = "personalized" if active_profile is not None else "collecting"
    params["personalization_mode"] = personalization_mode
    if profile_resolution_error is not None:
        params["personalization_fallback"] = profile_resolution_error

    residual_resolution_error: str | None = None
    active_residual = None
    if active_profile is not None:
        try:
            async with context.session_factory() as session:
                active_residual, _load_result = await resolve_loaded_ranking_residual(
                    session,
                    library=library,
                    profile_checksum=active_profile.checksum,
                    profile_generation=active_profile.generation,
                    baseline=baseline,
                )
        except MlPublicationError as exc:
            residual_resolution_error = str(exc)
    if active_residual is not None:
        copied = await context.io.copy(active_residual.path, workspace_dir / "residual.npz")
        if copied.sha256 != active_residual.checksum:
            (workspace_dir / "residual.npz").unlink(missing_ok=True)
            raise RuntimeError("active residual checksum changed while staging")
        params["ranking_residual"] = {
            "artifact_id": active_residual.artifact_id,
            "generation": active_residual.generation,
            "version": active_residual.version,
            "checksum": active_residual.checksum,
        }
    if residual_resolution_error is not None:
        params["residual_fallback"] = residual_resolution_error
    if personalization_mode == "collecting":
        (workspace_dir / "residual.npz").unlink(missing_ok=True)
        params.pop("ranking_residual", None)
    return params, profile_resolution_error, residual_resolution_error


async def _write_group_file(
    context: ExecutionContext,
    request: PosterPipelineGroupRequestV1,
    *,
    workspace_dir: Path,
    snapshots: dict[str, dict[str, Any]],
) -> tuple[dict[str, Any], tuple[str, ...]]:
    """Seal one authenticated group document for the contained runner.

    Season identity is resolved the same way the single path resolves it: a
    sealed snapshot that predates ``series_title``/``season_number`` is filled
    from the live rows rather than rejected. Being strict here would be
    chunk-wide — one stale field would fail every subject in the group before
    any of them reached the GPU.
    """
    members: list[dict[str, Any]] = []
    run_ids: list[str] = []
    async with context.session_factory() as session:
        for member in request.members:
            key = poster_subject_key(member)
            snapshot = snapshots.get(key)
            if snapshot is None:
                raise RuntimeError(f"poster group snapshot is missing {key}")
            run_id = uuid.uuid4().hex
            run_ids.append(run_id)
            members.append(
                {
                    "subject_key": key,
                    "run_id": run_id,
                    "request": member.model_dump(mode="json", exclude_none=True),
                    "subject": await _resolved_subject_params(session, member, snapshot),
                    "text_gate": await _text_gate_params(session, member, _media_type(member)),
                }
            )
    document = {
        "version": 1,
        "library": request.library,
        "chunk_index": request.chunk_index,
        "members": members,
    }
    payload = json.dumps(
        document, allow_nan=False, separators=(",", ":"), sort_keys=True
    ).encode()
    written = await context.io.write(payload, workspace_dir / "group.json")
    return (
        {"group_file": "group.json", "group_checksum": written.sha256},
        tuple(run_ids),
    )


async def _register_file(
    context: ExecutionContext,
    workspace_dir: Path,
    *,
    filename: str,
    kind: str,
    content_type: str,
    metadata: dict[str, Any],
) -> Any:
    if Path(filename).name != filename:
        raise ArtifactError(f"grouped poster artifact must be flat: {filename}")
    path = workspace_dir / filename
    if not path.is_file():
        raise ArtifactError(f"required grouped poster artifact is missing: {filename}")
    return await register_physical_artifact(
        job_id=context.delivery.canonical_job_id,
        attempt_id=context.attempt.attempt_id,
        fence_token=context.attempt.fence_token,
        source=context.workspace.boundary.classify(path, require_exists=True),
        kind=kind,
        name=filename,
        content_type=content_type,
        retention_class="extended",
        metadata=metadata,
    )


async def _register_candidate_files(
    context: ExecutionContext,
    workspace_dir: Path,
    *,
    subject_key: str,
    run_id: str,
    member: dict[str, Any],
    files_key: str = "candidate_files",
    role: str = "review_candidate",
) -> dict[str, Any]:
    raw = member.get(files_key)
    if not isinstance(raw, dict):
        return {}
    artifacts: dict[str, Any] = {}
    for reference, filename in list(raw.items())[:_MAX_COUNT]:
        if (
            not isinstance(reference, str)
            or not isinstance(filename, str)
            or Path(filename).name != filename
            or not filename.endswith(".jpg")
        ):
            continue
        try:
            artifact = await _register_file(
                context,
                workspace_dir,
                filename=filename,
                kind="evidence_image",
                content_type="image/jpeg",
                metadata={
                    "family": "poster_pipeline",
                    "role": role,
                    "candidate_reference": reference,
                    "subject_key": subject_key,
                    "run_id": run_id,
                    # Only the top stack representatives are re-fetched at full
                    # resolution; everything else — and every reject — is w500.
                    "provider_size": "w500" if role == "rejected_candidate" else "mixed",
                },
            )
        except Exception:  # noqa: BLE001 - candidate evidence is best-effort
            continue
        artifacts[reference] = artifact
    return artifacts


def _attach_evidence_artifacts(document: dict[str, Any], artifacts: dict[str, Any]) -> None:
    """Freeze artifact identity into the run archive's rejected-candidate block.

    Separate from the survivor block on purpose: these entries stay
    ``objective_eligible: False`` and are never a source of review eligibility —
    they exist so the review UI can render what each gate threw away.
    """
    block = document.get("review_evidence")
    entries = block.get("candidates") if isinstance(block, dict) else None
    if not isinstance(block, dict) or not isinstance(entries, list):
        return
    retained: list[dict[str, Any]] = []
    for entry in entries[:_MAX_COUNT]:
        if not isinstance(entry, dict):
            continue
        reference = entry.get("reference")
        artifact = artifacts.get(reference) if isinstance(reference, str) else None
        if artifact is None:
            continue
        entry["position"] = len(retained)
        entry["objective_eligible"] = False
        entry["artifact_id"] = artifact.id
        entry["artifact_checksum"] = artifact.checksum
        entry["artifact_storage_key"] = artifact.storage_key
        retained.append(entry)
    block["candidates"] = retained
    block["archived_count"] = len(retained)
    block["truncated_count"] = int(block.get("truncated_count") or 0) + (
        len(entries) - len(retained)
    )


def _attach_candidate_artifacts(
    workspace_dir: Path,
    *,
    archive_file: str,
    run_id: str,
    artifacts: dict[str, Any],
    evidence: dict[str, Any] | None = None,
) -> None:
    path = workspace_dir / archive_file
    if not path.is_file():
        raise RuntimeError(f"member archive is missing: {archive_file}")
    if path.stat().st_size > _MAX_GROUP_RESULT_BYTES:
        raise RuntimeError(f"member archive exceeds the size limit: {archive_file}")
    try:
        document = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"member archive is invalid: {archive_file}") from exc
    if not isinstance(document, dict):
        raise RuntimeError(f"member archive is not an object: {archive_file}")
    if document.get("run_id") != run_id:
        raise RuntimeError(f"member archive run identity is invalid: {archive_file}")
    review = document.get("review")
    survivors = review.get("survivors") if isinstance(review, dict) else None
    if not isinstance(survivors, list):
        raise RuntimeError(f"member archive has no review candidate list: {archive_file}")
    retained: list[dict[str, Any]] = []
    for survivor in survivors[:_MAX_COUNT]:
        if not isinstance(survivor, dict):
            continue
        reference = survivor.get("reference")
        if not isinstance(reference, str) or survivor.get("objective_eligible") is not True:
            continue
        artifact = artifacts.get(reference)
        if artifact is None:
            continue
        survivor["position"] = len(retained)
        survivor["artifact_id"] = artifact.id
        survivor["artifact_checksum"] = artifact.checksum
        survivor["artifact_storage_key"] = artifact.storage_key
        retained.append(survivor)
    review["survivors"] = retained
    review["archived_count"] = len(retained)
    review["truncated_count"] = int(review.get("truncated_count") or 0) + (
        len(survivors) - len(retained)
    )
    checksum_input = {
        "version": review.get("version"),
        "order_algorithm": review.get("order_algorithm"),
        "survivors": retained,
    }
    review["checksum"] = hashlib.sha256(
        json.dumps(checksum_input, separators=(",", ":"), sort_keys=True).encode()
    ).hexdigest()
    # Deliberately outside the survivor checksum — evidence is not part of the
    # eligibility contract it certifies.
    _attach_evidence_artifacts(document, evidence or {})
    path.write_text(
        json.dumps(document, allow_nan=False, separators=(",", ":"), sort_keys=True)
    )


async def _project_group_runs(
    context: ExecutionContext,
    request: PosterPipelineGroupRequestV1,
    *,
    snapshots: dict[str, dict[str, Any]],
    projected: list[dict[str, Any]],
) -> None:
    async with context.session_factory() as session, session.begin():
        if context.cancellation.cancel_called:
            raise asyncio.CancelledError
        if not await context.writer.owns_current_attempt(session):
            raise RuntimeError("poster group attempt lost its fence before projection")
        job = await session.get(Job, context.delivery.canonical_job_id)
        if job is None:
            raise RuntimeError("poster group canonical job disappeared before projection")
        for member_request, member in zip(request.members, projected, strict=True):
            key = poster_subject_key(member_request)
            snapshot = snapshots[key]
            archive_artifact = member["archive_artifact"]
            recommendation = member.get("recommendation")
            counts = member.get("counts") if isinstance(member.get("counts"), dict) else {}
            timings = (
                member.get("timings") if isinstance(member.get("timings"), dict) else {}
            )
            duration = member.get("duration_seconds")
            session.add(
                PipelineRun(
                    run_id=member["run_id"],
                    job_id=context.delivery.canonical_job_id,
                    subject_key=key,
                    attempt_id=context.attempt.attempt_id,
                    fence_token=context.attempt.fence_token,
                    movie_id=member_request.movie_id,
                    series_id=(
                        member_request.series_id
                        if member_request.series_id is not None
                        else snapshot.get("series_id")
                    ),
                    season_id=member_request.season_id,
                    media_type=_media_type(member_request),
                    subject_snapshot=snapshot,
                    status=member["status"],
                    scorer_name=member.get("scorer_name") or None,
                    counts_json=json.dumps(counts, sort_keys=True),
                    timings_json=json.dumps(timings, sort_keys=True),
                    duration_seconds=(
                        float(duration) if isinstance(duration, int | float) else None
                    ),
                    batch_id=job.parent_id,
                    correlation_id=job.correlation_id,
                    selected_artifact_id=(
                        member["selected_artifact"].id
                        if member.get("selected_artifact") is not None
                        else None
                    ),
                    archive_artifact_id=archive_artifact.id,
                    auto_pick_filename=(
                        recommendation.get("orig_filename")
                        if isinstance(recommendation, dict)
                        else None
                    ),
                    error=(
                        str(member.get("error"))[:2000]
                        if member.get("error") is not None
                        else None
                    ),
                    completed_at=datetime.now(UTC),
                )
            )


def _validated_member_results(
    document: dict[str, Any],
    request: PosterPipelineGroupRequestV1,
    planned_run_ids: tuple[str, ...],
    *,
    announced_files: set[str] | None = None,
) -> list[dict[str, Any]]:
    if (
        document.get("version") != 1
        or document.get("library") != request.library
        or document.get("chunk_index") != request.chunk_index
        or document.get("member_count") != len(request.members)
    ):
        raise RuntimeError("poster group result identity is invalid")
    raw_members = document.get("members")
    if not isinstance(raw_members, list) or len(raw_members) != len(request.members):
        raise RuntimeError("poster group result member count is invalid")
    expected = [poster_subject_key(member) for member in request.members]
    actual = [
        member.get("subject_key") if isinstance(member, dict) else None
        for member in raw_members
    ]
    if actual != expected:
        raise RuntimeError("poster group result member identity or order is invalid")
    actual_run_ids = [member.get("run_id") for member in raw_members]
    if actual_run_ids != list(planned_run_ids):
        raise RuntimeError("poster group result run identity or order is invalid")
    allowed_statuses = {"completed", "flagged_manual", "no_candidates", "failed"}
    if any(member.get("status") not in allowed_statuses for member in raw_members):
        raise RuntimeError("poster group result contains an invalid member status")
    expected_files = {"group-result.json"}
    for index, member in enumerate(raw_members):
        if member.get("member_index") != index:
            raise RuntimeError("poster group result member ordinal is invalid")
        archive_file = member.get("archive_file")
        candidate_files = member.get("candidate_files")
        if not isinstance(candidate_files, dict):
            raise RuntimeError("poster group result candidate artifact map is invalid")
        expected_candidates = {
            f"s{index:03d}-candidate-{position:03d}.jpg"
            for position in range(len(candidate_files))
        }
        if any(
            not isinstance(reference, str)
            or not reference
            or not isinstance(filename, str)
            for reference, filename in candidate_files.items()
        ) or set(candidate_files.values()) != expected_candidates:
            raise RuntimeError("poster group result candidate artifact attribution is invalid")
        # Rejected candidates are review evidence, not primary output, but they
        # are announced files and so must be attributed just as exactly.
        rejected_files = member.get("rejected_files", {})
        if not isinstance(rejected_files, dict):
            raise RuntimeError("poster group result rejected artifact map is invalid")
        expected_rejected = {
            f"s{index:03d}-rejected-{position:03d}.jpg"
            for position in range(len(rejected_files))
        }
        if any(
            not isinstance(reference, str)
            or not reference
            or not isinstance(filename, str)
            for reference, filename in rejected_files.items()
        ) or set(rejected_files.values()) != expected_rejected:
            raise RuntimeError("poster group result rejected artifact attribution is invalid")
        if member["status"] == "failed":
            if archive_file is not None or candidate_files or rejected_files:
                raise RuntimeError("failed poster group member contains primary artifacts")
        elif archive_file != f"run-{index:03d}.json":
            raise RuntimeError("poster group result archive attribution is invalid")
        if isinstance(archive_file, str):
            expected_files.add(archive_file)
        expected_files.update(candidate_files.values())
        expected_files.update(rejected_files.values())
    if announced_files is not None and announced_files != expected_files:
        raise RuntimeError("poster group result and announced artifacts differ")
    return raw_members


async def execute_poster_pipeline_group(
    context: ExecutionContext,
    request: PosterPipelineGroupRequestV1,
) -> dict[str, Any]:
    """Execute one stage-major chunk, register evidence, and atomically project N rows."""
    from marquee.core.jobs.internal_runner_host import (  # noqa: PLC0415
        OUTCOME_CANCELLED,
        OUTCOME_SUCCEEDED,
        run_internal_operation,
    )
    from marquee.core.jobs.runner_protocol import RunnerOperation  # noqa: PLC0415

    if context.cancellation.cancel_called:
        raise asyncio.CancelledError
    workspace_dir = _workspace_dir(context)
    snapshots = _member_snapshots(context)
    expected_keys = {poster_subject_key(member) for member in request.members}
    if set(snapshots) != expected_keys:
        raise RuntimeError("poster group request and live snapshot members differ")

    group_ref, planned_run_ids = await _write_group_file(
        context,
        request,
        workspace_dir=workspace_dir,
        snapshots=snapshots,
    )
    shared, profile_error, residual_error = await _stage_personalization(
        context,
        library=request.library,
        workspace_dir=workspace_dir,
    )
    manifest = {"params": {**group_ref, **shared}}
    bridge = (
        RunnerProgressBridge(context.progress, stage_map=_STAGE_MAP)
        if isinstance(context.progress, ExecutionProgress)
        else None
    )

    def should_stop() -> bool:
        return bool(getattr(context.cancellation, "cancel_called", False))

    try:
        outcome = await run_internal_operation(
            context.process_launcher,
            operation=RunnerOperation.POSTER_GROUP,
            manifest=manifest,
            on_progress=bridge.on_frame if bridge is not None else None,
            should_stop=should_stop,
            resolve_output=lambda key: workspace_dir / key,
            runtime_options=_runner_runtime_options(context),
        )
    finally:
        if bridge is not None:
            with contextlib.suppress(Exception):
                await bridge.close()
    if outcome.outcome == OUTCOME_CANCELLED:
        raise asyncio.CancelledError
    if outcome.outcome != OUTCOME_SUCCEEDED:
        raise RuntimeError(
            f"poster group runner did not succeed: {outcome.error or outcome.outcome}"
        )
    if context.cancellation.cancel_called:
        raise asyncio.CancelledError

    result_read = await context.io.read(
        workspace_dir / "group-result.json", maximum_bytes=_MAX_GROUP_RESULT_BYTES
    )
    try:
        result_document = json.loads(result_read.payload)
    except (TypeError, ValueError) as exc:
        raise RuntimeError("poster group result document is invalid") from exc
    if not isinstance(result_document, dict):
        raise RuntimeError("poster group result document is not an object")
    raw_members = _validated_member_results(
        result_document,
        request,
        planned_run_ids,
        announced_files={produced.key for produced in outcome.files},
    )

    try:
        group_artifact = await _register_file(
            context,
            workspace_dir,
            filename="group-result.json",
            kind="command_report",
            content_type="application/json",
            metadata={
                "family": "poster_pipeline",
                "stage": "group_result",
                "group_fallback": True,
            },
        )
    except Exception as exc:  # noqa: BLE001 - fallback evidence is mandatory
        raise RuntimeError("poster group fallback evidence could not be registered") from exc

    projected: list[dict[str, Any]] = []
    warnings: list[str] = list(outcome.warnings[:20])
    if profile_error is not None:
        warnings.append(f"taste profile fallback: {profile_error}")
    if residual_error is not None:
        warnings.append(f"ranking residual fallback: {residual_error}")
    for index, (member_request, raw_member) in enumerate(
        zip(request.members, raw_members, strict=True)
    ):
        key = poster_subject_key(member_request)
        member_warnings = raw_member.get("warnings")
        if isinstance(member_warnings, list):
            warnings.extend(
                f"{key}: {warning}" for warning in member_warnings[:2]
            )
        run_id = planned_run_ids[index]
        status = str(raw_member.get("status") or "failed")
        recommendation = (
            raw_member.get("recommendation")
            if isinstance(raw_member.get("recommendation"), dict)
            else None
        )
        candidates = await _register_candidate_files(
            context,
            workspace_dir,
            subject_key=key,
            run_id=run_id,
            member=raw_member,
        )
        rejected = await _register_candidate_files(
            context,
            workspace_dir,
            subject_key=key,
            run_id=run_id,
            member=raw_member,
            files_key="rejected_files",
            role="rejected_candidate",
        )
        selected_artifact = (
            candidates.get(recommendation.get("orig_filename"))
            if recommendation is not None
            else None
        )
        member_error = raw_member.get("error")
        archive_artifact = group_artifact
        archive_file = raw_member.get("archive_file")
        if status != "failed" and isinstance(archive_file, str):
            try:
                _attach_candidate_artifacts(
                    workspace_dir,
                    archive_file=archive_file,
                    run_id=run_id,
                    artifacts=candidates,
                    evidence=rejected,
                )
                archive_artifact = await _register_file(
                    context,
                    workspace_dir,
                    filename=archive_file,
                    kind="command_report",
                    content_type="application/json",
                    metadata={
                        "family": "poster_pipeline",
                        "stage": "archive",
                        "grouped": True,
                        "subject_key": key,
                    },
                )
            except Exception as exc:  # noqa: BLE001 - isolate member archive publication
                status = "failed"
                member_error = f"member archive registration failed: {exc}"
                warnings.append(f"{key}: member archive registration failed")
        elif status != "failed":
            status = "failed"
            member_error = "member archive was not announced"
            warnings.append(f"{key}: member archive was not announced")
        if status == "failed":
            recommendation = None
            selected_artifact = None
        projected.append(
            {
                **raw_member,
                "run_id": run_id,
                "status": status,
                "recommendation": recommendation,
                "selected_artifact": selected_artifact,
                "archive_artifact": archive_artifact,
                "error": member_error,
            }
        )

    member_outcomes: list[str] = []
    failed_keys: list[str] = []
    for member in projected:
        key = str(member["subject_key"])
        if member["status"] == "failed":
            member_outcomes.append("failed")
            failed_keys.append(key)
            continue
        candidate_count = min(int(member.get("candidate_count") or 0), _MAX_COUNT)
        member_outcome, _message, _reason = _map_outcome(
            member["status"], member.get("recommendation"), candidate_count
        )
        counts = member.get("counts") if isinstance(member.get("counts"), dict) else {}
        if (
            str(member.get("personalization_mode") or shared["personalization_mode"])
            == "collecting"
            and int(counts.get("ranked", 0) or 0) > 0
        ):
            member_outcome = "review_required"
        member_outcomes.append(member_outcome)

    if failed_keys or "review_required" in member_outcomes:
        aggregate_outcome = "review_required"
        message = "Grouped poster analysis completed with members requiring review."
    elif member_outcomes and all(value == "no_change" for value in member_outcomes):
        aggregate_outcome = "no_change"
        message = "Grouped poster analysis completed without viable recommendations."
    else:
        aggregate_outcome = "succeeded"
        message = "Grouped poster analysis completed successfully."
    artifact_ids = [group_artifact.id]
    artifact_ids.extend(
        member["archive_artifact"].id
        for member in projected
        if member["archive_artifact"].id not in artifact_ids
    )
    result = PosterPipelineGroupResultV1(
        outcome=aggregate_outcome,
        library=request.library,
        chunk_index=request.chunk_index,
        member_count=len(request.members),
        succeeded_count=member_outcomes.count("succeeded"),
        no_change_count=member_outcomes.count("no_change"),
        review_required_count=member_outcomes.count("review_required"),
        projected_count=len(projected),
        failed_count=len(failed_keys),
        run_ids=tuple(member["run_id"] for member in projected),
        failed_subject_keys=tuple(failed_keys),
        message=message,
        warnings=tuple(str(warning)[:300] for warning in warnings[:20]),
        artifact_ids=tuple(artifact_ids[:20]),
    )
    if context.cancellation.cancel_called:
        raise asyncio.CancelledError
    await _project_group_runs(
        context,
        request,
        snapshots=snapshots,
        projected=projected,
    )
    return result.model_dump(mode="json")
