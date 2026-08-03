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
import hashlib
import json
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from marquee.core.jobs.artifact_service import ArtifactError, register_physical_artifact
from marquee.core.jobs.documents import (
    PosterCandidateSummaryV1,
    PosterPipelineRequestV1,
    PosterPipelineResultV1,
    poster_pipeline_subject_key,
)
from marquee.core.jobs.execution_progress import ExecutionProgress
from marquee.core.jobs.ml_publication import (
    MlPublicationError,
    acknowledge_consumption,
    resolve_loaded_ranking_residual,
    resolve_loaded_taste_profile,
)
from marquee.core.jobs.runner_progress import RunnerProgressBridge
from marquee.core.jobs.runner_protocol import RunnerRuntimeOptions
from marquee.core.jobs.runner_runtime import poster_runner_runtime_options
from marquee.core.pipeline_config import pipeline_settings
from marquee.ml.residual import baseline_signature
from marquee.models import Job, JobAttempt, Movie, PipelineRun, Season, Series

if TYPE_CHECKING:
    from marquee.core.jobs.delivery import ExecutionContext

# Runner pipeline stage -> the definition's declared poster progress vocabulary.
# Listed in execution order, and the mapped positions must never decrease down
# this table: the job card's bar reports the furthest declared stage reached
# while the per-subject roster reports the stage each subject is in, so any
# backwards step here makes the two disagree by construction. ``filtering``
# collects the three gates that narrow the candidate set; ``analyzing`` is the
# detail feature pass, which necessarily runs after them.
_STAGE_MAP = {
    "fetch": "downloading",
    "sha256": "deduplicating",
    "gate-resolution": "validating",
    "style-features": "extracting",
    "gate-style": "filtering",
    "ocr": "filtering",
    "phash": "filtering",
    "detail-features": "analyzing",
    "neutral-order": "scoring",
    "rank": "scoring",
    "output": "rendering",
}
_MAX_COUNT = 100
# A run archive carries one full CandidateScore per *downloaded* candidate, so it
# scales with the TMDB catalogue for the title — roughly 8 KB each in practice. A
# 1 MiB ceiling meant every title with more than ~125 posters failed while small
# ones passed. 16 MiB is ~2000 candidates: far beyond anything TMDB returns, but
# still a bound. `ARTIFACT_POLICIES["command_report"]` must not sit below this or
# the registration step rejects what this check just allowed.
MAX_RUN_ARCHIVE_BYTES = 16 * 1024 * 1024
_REJECTION_KEYS = ("metadata_gated", "resolution_gated", "style_gated", "gated")


def _media_type(request: PosterPipelineRequestV1) -> str:
    if request.series_id is not None:
        return "series"
    if request.season_id is not None:
        return "season"
    return "movie"


def _subject_params(
    request: PosterPipelineRequestV1, subject: Mapping[str, Any] | None
) -> dict[str, Any]:
    """Freeze the subject the runner fetches for.

    Season identity comes from the sealed live snapshot. The display title keeps
    its season suffix, while OCR receives the bare series title so title tokens
    match the artwork.
    """
    media_type = _media_type(request)
    series_id = request.series_id
    season_number = subject.get("season_number") if subject else None
    if media_type == "season":
        series_id = subject.get("series_id") if subject else None
        if not isinstance(series_id, int):
            raise ValueError("a season poster run requires a series id in its subject snapshot")
    if media_type == "season" and not isinstance(season_number, int):
        raise ValueError("a season poster run requires a season number in its subject snapshot")
    ocr_title = subject.get("series_title") if subject and media_type == "season" else None
    if media_type == "season" and not isinstance(ocr_title, str):
        raise ValueError("a season poster run requires a series title in its subject snapshot")
    return {
        "title": request.title,
        "media_type": media_type,
        "movie_id": request.movie_id,
        "tmdb_id": request.tmdb_id,
        "series_id": series_id,
        "season_id": request.season_id,
        "season_number": season_number if media_type == "season" else None,
        "ocr_title": ocr_title,
    }


async def _resolved_subject_params(
    session,
    request: PosterPipelineRequestV1,
    subject: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Fill legacy season snapshots from live rows before sealing runner input."""
    if _media_type(request) != "season":
        return _subject_params(request, subject)

    resolved = dict(subject or {})
    if (
        not isinstance(resolved.get("series_id"), int)
        or not isinstance(resolved.get("season_number"), int)
        or not isinstance(resolved.get("series_title"), str)
    ):
        season = await session.get(Season, request.season_id)
        if season is None:
            raise ValueError("a season poster run requires a live season")
        series = await session.get(Series, season.series_id)
        if series is None:
            raise ValueError("a season poster run requires a live parent series")
        if not isinstance(resolved.get("series_id"), int):
            resolved["series_id"] = series.id
        if not isinstance(resolved.get("season_number"), int):
            resolved["season_number"] = season.season_number
        if not isinstance(resolved.get("series_title"), str):
            resolved["series_title"] = series.title
    return _subject_params(request, resolved)


async def _text_gate_params(
    session, request: PosterPipelineRequestV1, media_type: str
) -> dict[str, Any]:
    """Resolve the OCR text gate for this subject.

    The runner is a separate process with no database, so the per-subject
    metadata is read here and the scope is passed by name — the child reloads
    the profile itself from the shared store.

    Scope matters most for seasons: their art prints "SEASON 4", which the
    movie-scoped ``title_only`` profile counts as residual text and rejects
    outright at ``OCR_MAX_RESIDUAL_BOXES=0``. The season scope falls back to
    ``title_and_season``, which expects it.
    """
    if media_type == "movie":
        movie = await session.get(Movie, request.movie_id)
        return {
            "scope": "movie",
            "profile_id": getattr(movie, "text_profile_id", None),
            "director": getattr(movie, "director", None),
            "studios": getattr(movie, "production_companies_json", None),
            "tagline": getattr(movie, "tagline", None),
        }

    if media_type == "series":
        series = await session.get(Series, request.series_id)
        profile_id = getattr(series, "show_text_profile_id", None)
    else:
        season = await session.get(Season, request.season_id)
        series = await session.get(Series, season.series_id) if season else None
        profile_id = getattr(series, "season_text_profile_id", None)
    return {
        "scope": "show" if media_type == "series" else "season",
        "profile_id": profile_id,
        "director": getattr(series, "director", None),
        "studios": getattr(series, "production_companies_json", None),
        "tagline": getattr(series, "tagline", None),
    }


def _workspace_dir(context: ExecutionContext):
    directory = context.workspace.directory
    return directory.root.resolved() / directory.key.value


def _runner_runtime_options(context: ExecutionContext) -> RunnerRuntimeOptions:
    return poster_runner_runtime_options(context.configuration)


def _pipeline_baseline_signature(context: ExecutionContext) -> str:
    """Derive ranking identity from the job's sealed execution configuration."""
    effective = pipeline_settings.model_copy(update=dict(context.configuration or {}))
    return baseline_signature(effective.scorer_weights)


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
    context: ExecutionContext,
    workspace_dir,
    summary: dict[str, Any],
    *,
    run_id: str,
    files_key: str = "candidate_files",
    role: str = "review_candidate",
) -> dict[str, Any]:
    """Register every bounded review candidate announced by the contained runner."""
    raw = summary.get(files_key)
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
                    "role": role,
                    "candidate_reference": reference,
                    # Scopes per-run artifact expiry; the group handler has
                    # always carried it, the single-subject one now matches.
                    "run_id": run_id,
                    # Only the top stack representatives are re-fetched at full
                    # resolution; everything else — and every reject — is w500.
                    "provider_size": "w500" if role == "rejected_candidate" else "mixed",
                },
            )
        except ArtifactError:
            continue
        artifacts[reference] = artifact
    return artifacts


def _attach_evidence_artifacts(document: dict[str, Any], artifacts: dict[str, Any]) -> None:
    """Freeze artifact identity into the run archive's rejected-candidate block.

    Separate from the survivor block on purpose: these entries stay
    ``objective_eligible: False`` and never confer review eligibility — they
    exist so the review UI can render what each gate threw away.
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
    workspace_dir, artifacts: dict[str, Any], evidence: dict[str, Any] | None = None
) -> str | None:
    """Freeze canonical artifact identity into the immutable run archive.

    Returns why it could not, or ``None`` on success. Skipping is not harmless:
    a survivor with no ``artifact_id`` makes its image 404 in review, so the
    caller records the reason instead of shipping a review nobody can act on.
    """
    path = workspace_dir / "run.json"
    if not path.is_file():
        return "run archive is missing"
    size = path.stat().st_size
    if size > MAX_RUN_ARCHIVE_BYTES:
        return f"run archive is {size} bytes, over the {MAX_RUN_ARCHIVE_BYTES} byte limit"
    try:
        document = json.loads(path.read_text())
    except (OSError, ValueError):
        return "run archive is invalid"
    if not isinstance(document, dict):
        return "run archive is not an object"
    review = document.get("review")
    survivors = review.get("survivors") if isinstance(review, dict) else None
    if not isinstance(review, dict) or not isinstance(survivors, list):
        return "run archive has no review candidate list"
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
    path.write_text(json.dumps(document, allow_nan=False, separators=(",", ":"), sort_keys=True))
    return None


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
        # The request names exactly one subject, so a season analysis carries no
        # series_id. The run is a projection, not the request: denormalize the parent
        # series here or every TV query that groups by show silently drops seasons.
        series_id = request.series_id
        if series_id is None and request.season_id is not None:
            season = await session.get(Season, request.season_id)
            series_id = season.series_id if season is not None else None
        session.add(
            PipelineRun(
                run_id=run_id,
                job_id=context.delivery.canonical_job_id,
                attempt_id=context.attempt.attempt_id,
                fence_token=context.attempt.fence_token,
                movie_id=request.movie_id,
                series_id=series_id,
                season_id=request.season_id,
                media_type=_media_type(request),
                subject_snapshot=dict(context.subject) if context.subject else {},
                subject_key=poster_pipeline_subject_key(request),
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
    baseline = _pipeline_baseline_signature(context)
    async with context.session_factory() as session:
        subject_params = await _resolved_subject_params(session, request, context.subject)
        text_gate = await _text_gate_params(session, request, _media_type(request))
    manifest = {
        "params": {
            "subject": subject_params,
            "source": {"mode": "tmdb"},
            "run_id": run_id,
            "baseline_signature": baseline,
            "text_gate": text_gate,
        }
    }
    workspace_dir = _workspace_dir(context)
    library = "movies" if _media_type(request) == "movie" else "tv"
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
        manifest["params"]["taste_profile"] = {
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
                    consumer_role="poster_pipeline",
                    instance_id=attempt.runtime_instance_id,
                    load_result={
                        **(profile_load_result or {}),
                        "supplied_to": "contained_poster_runner",
                        "staged_checksum": copied.sha256,
                    },
                )
                await session.commit()
    personalization_mode = "personalized" if active_profile is not None else "collecting"
    manifest["params"]["personalization_mode"] = personalization_mode
    if profile_resolution_error is not None:
        manifest["params"]["personalization_fallback"] = profile_resolution_error
    residual_resolution_error: str | None = None
    active_residual = None
    if active_profile is not None:
        try:
            async with context.session_factory() as session:
                active_residual, _residual_load_result = await resolve_loaded_ranking_residual(
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
        manifest["params"]["ranking_residual"] = {
            "artifact_id": active_residual.artifact_id,
            "generation": active_residual.generation,
            "version": active_residual.version,
            "checksum": active_residual.checksum,
        }
    if residual_resolution_error is not None:
        manifest["params"]["residual_fallback"] = residual_resolution_error
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
            configuration=context.configuration,
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

    candidate_artifacts = await _register_candidate_files(
        context, workspace_dir, summary, run_id=run_id
    )
    rejected_artifacts = await _register_candidate_files(
        context,
        workspace_dir,
        summary,
        run_id=run_id,
        files_key="rejected_files",
        role="rejected_candidate",
    )
    selected_artifact = (
        candidate_artifacts.get(recommendation.get("orig_filename"))
        if recommendation is not None
        else None
    )
    if selected_artifact is None and recommendation is not None:
        selected_artifact = await _register_selected(context, workspace_dir, recommendation)
    attach_skipped = _attach_candidate_artifacts(
        workspace_dir, candidate_artifacts, rejected_artifacts
    )
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
            "personalization_fallback": profile_resolution_error,
            "residual_fallback": residual_resolution_error,
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
        warnings=(
            (*outcome.warnings, f"review candidates are unlinked: {attach_skipped}")[:20]
            if attach_skipped is not None
            else outcome.warnings[:20]
        ),
        artifact_ids=artifact_ids,
    ).model_dump(mode="json")
