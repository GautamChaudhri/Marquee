"""Canonical single-subject poster analysis (JMC6H H2).

Runs the *real* Marquee poster pipeline inside the contained internal runner, then
projects the outcome into a canonical ``PipelineRun`` and registered artifacts.

This module owns no transport acknowledgement, canonical job mutation, shared
progress bridge, or active-artwork pointer. It never deploys, resets, or restores
library artwork — analysis only. Heavy provider/OCR/model work happens inside the
attempt-owned runner process; here we only fence, register confined evidence, and
write the durable projection.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import uuid
from datetime import UTC, datetime
from typing import Any

from marquee.core.jobs.artifact_service import ArtifactError, register_physical_artifact
from marquee.core.jobs.delivery import ExecutionContext
from marquee.core.jobs.documents import (
    PosterCandidateSummaryV1,
    PosterPipelineRequestV1,
    PosterPipelineResultV1,
)
from marquee.core.jobs.execution_progress import ExecutionProgress
from marquee.core.jobs.ml_publication import MlPublicationError, resolve_active_publication
from marquee.core.jobs.runner_progress import RunnerProgressBridge
from marquee.core.jobs.runner_protocol import RunnerRuntimeOptions
from marquee.core.jobs.runner_runtime import poster_runner_runtime_options
from marquee.core.pipeline_config import pipeline_settings
from marquee.ml.residual import baseline_signature
from marquee.models import Job, PipelineRun

# Runner pipeline stage -> the definition's declared poster progress vocabulary.
_STAGE_MAP = {
    "fetch": "downloading",
    "sha256": "deduplicating",
    "gate-resolution": "validating",
    "style-features": "extracting",
    "gate-style": "validating",
    "ocr": "validating",
    "phash": "deduplicating",
    "detail-features": "extracting",
    "rank": "scoring",
    "output": "rendering",
}
_MAX_COUNT = 100
_REJECTION_KEYS = ("metadata_gated", "resolution_gated", "style_gated", "gated")


def _media_type(request: PosterPipelineRequestV1) -> str:
    if request.series_id is not None:
        return "series"
    if request.season_id is not None:
        return "season"
    return "movie"


def _subject_params(request: PosterPipelineRequestV1) -> dict[str, Any]:
    return {
        "title": request.title,
        "media_type": _media_type(request),
        "movie_id": request.movie_id,
        "tmdb_id": request.tmdb_id,
        "series_id": request.series_id,
        "season_id": request.season_id,
    }


def _workspace_dir(context: ExecutionContext):
    directory = context.workspace.directory
    return directory.root.resolved() / directory.key.value


def _runner_runtime_options(context: ExecutionContext) -> RunnerRuntimeOptions:
    return poster_runner_runtime_options(context.configuration)


async def _owns_fence(context: ExecutionContext) -> bool:
    async with context.session_factory() as session:
        return await context.writer.owns_current_attempt(session)


async def _register_archive(context: ExecutionContext, workspace_dir) -> Any:
    """Copy the confined run.json into immutable managed storage as evidence."""
    path = workspace_dir / "run.json"
    if not path.is_file():
        return None
    try:
        source = context.workspace.boundary.classify(path, require_exists=True)
        return await register_physical_artifact(
            job_id=context.delivery.canonical_job_id,
            attempt_id=context.attempt.attempt_id,
            fence_token=context.attempt.fence_token,
            source=source,
            kind="command_report",
            name="run.json",
            content_type="application/json",
            retention_class="extended",
            metadata={"family": "poster_pipeline", "stage": "archive"},
        )
    except ArtifactError:
        return None


async def _register_selected(
    context: ExecutionContext, workspace_dir, recommendation: dict[str, Any]
) -> Any:
    """Register the selected candidate image (best-effort, jpeg only)."""
    filename = recommendation.get("orig_filename")
    if not isinstance(filename, str) or not filename.lower().endswith((".jpg", ".jpeg")):
        return None
    path = workspace_dir / filename
    if not path.is_file():
        return None
    try:
        source = context.workspace.boundary.classify(path, require_exists=True)
        return await register_physical_artifact(
            job_id=context.delivery.canonical_job_id,
            attempt_id=context.attempt.attempt_id,
            fence_token=context.attempt.fence_token,
            source=source,
            kind="evidence_image",
            name="selected.jpg",
            content_type="image/jpeg",
            retention_class="extended",
            metadata={"family": "poster_pipeline", "role": "selected_candidate"},
        )
    except ArtifactError:
        return None


async def _register_candidate_files(
    context: ExecutionContext, workspace_dir, summary: dict[str, Any]
) -> dict[str, Any]:
    """Register every bounded review candidate announced by the contained runner."""
    raw = summary.get("candidate_files")
    if not isinstance(raw, dict):
        return {}
    artifacts: dict[str, Any] = {}
    for reference, key in list(raw.items())[:100]:
        if not isinstance(reference, str) or not isinstance(key, str) or not key.endswith(".jpg"):
            continue
        path = workspace_dir / key
        if not path.is_file():
            continue
        try:
            artifact = await register_physical_artifact(
                job_id=context.delivery.canonical_job_id,
                attempt_id=context.attempt.attempt_id,
                fence_token=context.attempt.fence_token,
                source=context.workspace.boundary.classify(path, require_exists=True),
                kind="evidence_image",
                name=key,
                content_type="image/jpeg",
                retention_class="extended",
                metadata={
                    "family": "poster_pipeline",
                    "role": "review_candidate",
                    "candidate_reference": reference,
                },
            )
        except ArtifactError:
            continue
        artifacts[reference] = artifact
    return artifacts


def _attach_candidate_artifacts(workspace_dir, artifacts: dict[str, Any]) -> None:
    """Freeze canonical artifact identity into the immutable run archive."""
    path = workspace_dir / "run.json"
    if not path.is_file() or path.stat().st_size > 1024 * 1024:
        return
    try:
        document = json.loads(path.read_text())
    except (OSError, ValueError):
        return
    candidates = document.get("candidates") if isinstance(document, dict) else None
    if not isinstance(candidates, list):
        return
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        artifact = artifacts.get(candidate.get("orig_filename"))
        if artifact is None:
            continue
        candidate["artifact_id"] = artifact.id
        candidate["artifact_checksum"] = artifact.checksum
        candidate["artifact_storage_key"] = artifact.storage_key
    path.write_text(json.dumps(document, allow_nan=False, separators=(",", ":"), sort_keys=True))


def _rejections(counts: dict[str, Any]) -> dict[str, int]:
    rejections = {}
    for key in _REJECTION_KEYS:
        value = counts.get(key)
        if isinstance(value, int) and value > 0:
            rejections[key] = value
    return rejections


def _candidate_summary(
    recommendation: dict[str, Any] | None, selected_artifact: Any
) -> PosterCandidateSummaryV1 | None:
    if recommendation is None:
        return None
    filename = recommendation.get("orig_filename")
    if not isinstance(filename, str):
        return None
    score = recommendation.get("final_score")
    clamped = None
    if isinstance(score, int | float):
        clamped = max(0.0, min(1.0, float(score)))
    return PosterCandidateSummaryV1(
        candidate_id=filename[:80],
        source="tmdb",
        decision="recommended",
        score=clamped,
        artifact_key=(
            f"artifact:{selected_artifact.id}" if selected_artifact is not None else None
        ),
    )


def _map_outcome(
    pipeline_status: str, recommendation: dict[str, Any] | None, candidate_count: int
) -> tuple[str, str, str | None]:
    if recommendation is not None:
        return (
            "succeeded",
            "Poster analysis recommended a candidate without changing library artwork.",
            None,
        )
    if candidate_count == 0:
        return (
            "no_change",
            "Poster analysis completed without a viable recommendation.",
            "No configured candidate source produced a viable poster.",
        )
    return (
        "review_required",
        "Poster analysis completed; no candidate passed the gates.",
        "No candidate passed the configured gates — manual review required.",
    )


async def _write_pipeline_run(
    context: ExecutionContext,
    request: PosterPipelineRequestV1,
    *,
    run_id: str,
    status: str,
    counts: dict[str, Any],
    summary: dict[str, Any],
    recommendation: dict[str, Any] | None,
    selected_artifact: Any,
    archive_artifact: Any,
) -> None:
    duration = summary.get("counts", {})
    async with context.session_factory() as session, session.begin():
        if not await context.writer.owns_current_attempt(session):
            raise RuntimeError("poster pipeline attempt lost its fence before projection")
        job = await session.get(Job, context.delivery.canonical_job_id)
        if job is None:
            raise RuntimeError("poster pipeline canonical job disappeared before projection")
        session.add(
            PipelineRun(
                run_id=run_id,
                job_id=context.delivery.canonical_job_id,
                attempt_id=context.attempt.attempt_id,
                fence_token=context.attempt.fence_token,
                movie_id=request.movie_id,
                series_id=request.series_id,
                season_id=request.season_id,
                media_type=_media_type(request),
                subject_snapshot=dict(context.subject) if context.subject else {},
                status=status,
                scorer_name=(summary.get("scorer_name") or None),
                counts_json=json.dumps(counts, sort_keys=True),
                duration_seconds=(
                    float(duration.get("total"))
                    if isinstance(duration, dict) and isinstance(duration.get("total"), int | float)
                    else None
                ),
                batch_id=job.parent_id,
                correlation_id=job.correlation_id,
                selected_artifact_id=(
                    selected_artifact.id if selected_artifact is not None else None
                ),
                archive_artifact_id=(archive_artifact.id if archive_artifact is not None else None),
                auto_pick_filename=(
                    recommendation.get("orig_filename") if recommendation else None
                ),
                completed_at=datetime.now(UTC),
            )
        )


async def execute_poster_pipeline(
    context: ExecutionContext, request: PosterPipelineRequestV1
) -> dict[str, Any]:
    """Run the real pipeline via the contained runner; project, never deploy."""
    # Deferred so importing this handler module stays free of the runner/process
    # subsystem (and any module-level import cycle through the handler registry).
    from marquee.core.jobs.internal_runner_host import (
        OUTCOME_CANCELLED,
        OUTCOME_SUCCEEDED,
        run_internal_operation,
    )
    from marquee.core.jobs.runner_protocol import RunnerOperation

    if context.cancellation.cancel_called:
        raise asyncio.CancelledError

    run_id = uuid.uuid4().hex
    manifest = {
        "params": {
            "subject": _subject_params(request),
            "source": {"mode": "tmdb"},
            "run_id": run_id,
            "baseline_signature": baseline_signature(pipeline_settings.scorer_weights),
        }
    }
    workspace_dir = _workspace_dir(context)
    library = "movies" if _media_type(request) == "movie" else "tv"
    try:
        async with context.session_factory() as session:
            active_residual = await resolve_active_publication(
                session, family=f"ranking_residual:{library}"
            )
    except MlPublicationError:
        active_residual = None
    if active_residual is not None:
        copied = await context.io.copy(active_residual.path, workspace_dir / "residual.npz")
        if copied.sha256 != active_residual.checksum:
            (workspace_dir / "residual.npz").unlink(missing_ok=True)
            raise RuntimeError("active residual checksum changed while staging")
        manifest["params"]["ranking_residual"] = {
            "artifact_id": active_residual.artifact_id,
            "generation": active_residual.generation,
            "version": active_residual.version,
            "checksum": active_residual.checksum,
        }
    try:
        async with context.session_factory() as session:
            active_profile = await resolve_active_publication(
                session, family=f"taste_profile:{library}"
            )
    except MlPublicationError:
        active_profile = None
    if active_profile is not None:
        copied = await context.io.copy(active_profile.path, workspace_dir / "profile.npz")
        if copied.sha256 != active_profile.checksum:
            (workspace_dir / "profile.npz").unlink(missing_ok=True)
            raise RuntimeError("active taste-profile checksum changed while staging")
        manifest["params"]["taste_profile"] = {
            "generation": active_profile.generation,
            "version": active_profile.version,
            "checksum": active_profile.checksum,
        }
    personalization_mode = "personalized" if active_profile is not None else "collecting"
    manifest["params"]["personalization_mode"] = personalization_mode
    if personalization_mode == "collecting":
        (workspace_dir / "residual.npz").unlink(missing_ok=True)
        manifest["params"].pop("ranking_residual", None)

    # The typed bridge maps runner stages/counts onto the registered progress
    # vocabulary (JMC6I §6.1): real done/total become the determinate current
    # scope, survivors ride as bounded metrics, and the overall scope tracks the
    # furthest registered stage monotonically.
    progress = getattr(context, "progress", None)
    bridge = (
        RunnerProgressBridge(progress, stage_map=_STAGE_MAP)
        if isinstance(progress, ExecutionProgress)
        else None
    )

    def should_stop() -> bool:
        return bool(getattr(context.cancellation, "cancel_called", False))

    def resolve(key: str):
        return workspace_dir / key

    try:
        outcome = await run_internal_operation(
            context.process_launcher,
            operation=RunnerOperation.POSTER_SINGLE,
            manifest=manifest,
            on_progress=bridge.on_frame if bridge is not None else None,
            should_stop=should_stop,
            resolve_output=resolve,
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
            f"poster pipeline runner did not succeed: {outcome.error or outcome.outcome}"
        )

    summary = outcome.summary if isinstance(outcome.summary, dict) else {}
    pipeline_status = str(summary.get("pipeline_status") or "completed")
    counts = summary.get("counts") if isinstance(summary.get("counts"), dict) else {}
    recommendation = (
        summary.get("recommendation") if isinstance(summary.get("recommendation"), dict) else None
    )
    result_personalization_mode = str(
        summary.get("personalization_mode")
        or ("personalized" if recommendation is not None else personalization_mode)
    )
    run_id = str(summary.get("run_id") or run_id)
    candidate_count = min(int(summary.get("candidate_count") or 0), _MAX_COUNT)

    if not await _owns_fence(context):
        context.workspace.quarantine(code="stale_fence", summary="poster projection fenced")
        raise RuntimeError("poster pipeline attempt lost its fence before projection")

    candidate_artifacts = await _register_candidate_files(context, workspace_dir, summary)
    selected_artifact = (
        candidate_artifacts.get(recommendation.get("orig_filename"))
        if recommendation is not None
        else None
    )
    if selected_artifact is None and recommendation is not None:
        selected_artifact = await _register_selected(context, workspace_dir, recommendation)
    _attach_candidate_artifacts(workspace_dir, candidate_artifacts)
    archive_artifact = await _register_archive(context, workspace_dir)
    await _write_pipeline_run(
        context,
        request,
        run_id=run_id,
        status=pipeline_status,
        counts=counts,
        summary=summary,
        recommendation=recommendation,
        selected_artifact=selected_artifact,
        archive_artifact=archive_artifact,
    )

    artifact_ids = tuple(
        artifact.id for artifact in (archive_artifact, selected_artifact) if artifact is not None
    )
    poster_outcome, message, review_reason = _map_outcome(
        pipeline_status, recommendation, candidate_count
    )
    if result_personalization_mode == "collecting" and int(counts.get("ranked", 0) or 0) > 0:
        poster_outcome = "review_required"
        message = str(
            summary.get("message")
            or "Marquee filtered unusable posters, but has not learned your preferences yet."
        )
        review_reason = "Choose a poster explicitly to teach Marquee your visual preferences."
    return PosterPipelineResultV1(
        outcome=poster_outcome,
        message=message,
        summary={
            "run_id": run_id,
            "pipeline_status": pipeline_status,
            "counts": {key: int(value) for key, value in counts.items() if isinstance(value, int)},
            "scorer_name": summary.get("scorer_name"),
            "personalization_mode": result_personalization_mode,
            "review_reason": review_reason,
        },
        subject_label=request.title,
        source_count=min(int(summary.get("source_count") or 0), _MAX_COUNT),
        candidate_count=candidate_count,
        accepted_count=min(int(counts.get("ranked", 0) or 0), _MAX_COUNT),
        rejected_by_gate=_rejections(counts),
        recommendation=_candidate_summary(recommendation, selected_artifact),
        profile_version=request.profile_version,
        model_version=request.model_version,
        prior_poster_checksum=request.prior_poster_checksum,
        review_reason=review_reason,
        warnings=outcome.warnings[:20],
        artifact_ids=artifact_ids,
    ).model_dump(mode="json")
