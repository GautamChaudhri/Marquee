"""Canonical immutable ML publication handlers for JMC4C."""

from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path

from marquee.core.jobs.artifact_service import register_physical_artifact
from marquee.core.jobs.delivery import ExecutionContext, register_execution_handler
from marquee.core.jobs.documents import (
    LearnedHeadTrainRequestV1,
    MlPublicationResultV1,
    TasteEnrichRequestV1,
    TasteMapRequestV1,
    TasteRebuildRequestV1,
)
from marquee.core.jobs.execution_progress import ExecutionProgress
from marquee.core.jobs.ml_publication import (
    ActivePublication,
    MlPublicationError,
    activate_immutable_artifact,
    resolve_active_publication,
)
from marquee.core.jobs.runner_progress import RunnerProgressBridge

# Runner-native trainer stages -> the registered ML progress vocabulary (JMC6I
# §6.2). The learned-head operation deliberately has no bridge here: its progress
# and behavior are reserved to JMC6J.
_TASTE_PROFILE_STAGE_MAP = {
    "starting": "collecting",
    "clip": "features",
    "clip-negatives": "features",
    "dino": "features",
    "dino-negatives": "features",
    "calibration": "evaluating",
    "saving": "training",
    "completed": "validating",
}
_TASTE_MAP_STAGE_MAP = {
    "load": "loading",
    "project": "features",
    "cluster": "evaluating",
    "save": "training",
}
_ENRICHMENT_STAGE_MAP = {
    "enriching": "features",
    "resolve": "features",
}


def _runner_bridge(
    context: ExecutionContext, stage_map: dict[str, str]
) -> RunnerProgressBridge | None:
    progress = getattr(context, "progress", None)
    if not isinstance(progress, ExecutionProgress):
        return None
    return RunnerProgressBridge(progress, stage_map=stage_map)


async def _bridge_stage(bridge: RunnerProgressBridge | None, stage_key: str) -> None:
    if bridge is not None:
        await bridge.stage(stage_key)


def _ml_workspace_dir(context: ExecutionContext) -> Path:
    directory = context.workspace.directory
    return directory.root.resolved() / directory.key.value


async def _ml_owns_fence(context: ExecutionContext) -> bool:
    async with context.session_factory() as session:
        return await context.writer.owns_current_attempt(session)


def _validate_taste_profile(path: Path) -> None:
    """Loader-compatibility gate (H18): the production loader must read the artifact."""
    from marquee.ml.taste_store import NumpyTasteStore  # noqa: PLC0415

    NumpyTasteStore(path)._ensure_loaded()


async def _publish_native_taste_profile(
    context: ExecutionContext, *, library: str, expected_generation: int, seed: int
) -> dict[str, object]:
    """Run the real taste trainer in the contained runner, then validate/register/activate."""
    from marquee.core.jobs.internal_runner_host import (  # noqa: PLC0415
        OUTCOME_CANCELLED,
        OUTCOME_SUCCEEDED,
        run_internal_operation,
    )
    from marquee.core.jobs.runner_protocol import RunnerOperation  # noqa: PLC0415

    if context.cancellation.cancel_called:
        raise asyncio.CancelledError

    family = "taste_profile"
    workspace_dir = _ml_workspace_dir(context)
    manifest = {
        "params": {
            "family": family,
            "library": library,
            "seed": seed,
            "source": {"mode": "library"},
            "skip_ocr": True,
            "skip_dino": True,
        }
    }

    def should_stop() -> bool:
        return bool(getattr(context.cancellation, "cancel_called", False))

    def resolve(key: str) -> Path:
        return workspace_dir / key

    bridge = _runner_bridge(context, _TASTE_PROFILE_STAGE_MAP)
    try:
        outcome = await run_internal_operation(
            context.process_launcher,
            operation=RunnerOperation.TASTE_PROFILE,
            manifest=manifest,
            on_progress=bridge.on_frame if bridge is not None else None,
            should_stop=should_stop,
            resolve_output=resolve,
        )
    finally:
        if bridge is not None:
            await bridge.close()
    if outcome.outcome == OUTCOME_CANCELLED:
        raise asyncio.CancelledError
    if outcome.outcome != OUTCOME_SUCCEEDED:
        raise RuntimeError(f"taste profile runner did not succeed: {outcome.error or outcome.outcome}")

    await _bridge_stage(bridge, "validating")
    profile_path = workspace_dir / "profile.npz"
    _validate_taste_profile(profile_path)

    if not await _ml_owns_fence(context):
        context.workspace.quarantine(code="stale_fence", summary="taste profile publication fenced")
        raise RuntimeError("taste profile attempt lost its fence before publication")

    await _bridge_stage(bridge, "registering")
    source = context.workspace.boundary.classify(profile_path, require_exists=True)
    artifact = await register_physical_artifact(
        job_id=context.delivery.canonical_job_id,
        attempt_id=context.attempt.attempt_id,
        fence_token=context.attempt.fence_token,
        source=source,
        kind=family,
        name="profile.npz",
        content_type="application/octet-stream",
        retention_class="extended",
        metadata={"family": family, "library": library, "seed": seed},
    )
    if context.cancellation.cancel_called:
        raise asyncio.CancelledError

    await _bridge_stage(bridge, "publishing")
    checksum = artifact.checksum or ""
    activation = await activate_immutable_artifact(
        context,
        family=f"{family}:{library}",
        expected_generation=expected_generation,
        version=f"v1-{checksum[:16]}",
        artifact=artifact,
    )
    summary = outcome.summary if isinstance(outcome.summary, dict) else {}
    return MlPublicationResultV1(
        outcome="succeeded" if activation.activated else "superseded",
        family=family,
        version=activation.version,
        checksum=activation.checksum,
        expected_generation=expected_generation,
        active_generation=activation.generation,
        activated=activation.activated,
        artifact_ids=(artifact.id,),
        metrics={"exemplars": int(summary.get("exemplars", 0) or 0), "seed": seed},
    ).model_dump(mode="json")


async def _stage_active_publication(
    context: ExecutionContext,
    *,
    family: str,
    destination: Path,
) -> ActivePublication:
    """Copy one coordinator-selected active artifact into the attempt workspace."""
    async with context.session_factory() as session:
        active = await resolve_active_publication(session, family=family)
    copied = await context.io.copy(active.path, destination)
    if copied.sha256 != active.checksum:
        destination.unlink(missing_ok=True)
        raise RuntimeError(f"active ML publication checksum changed: {family}")
    return active


async def _publish_native_taste_map(
    context: ExecutionContext, *, library: str, expected_generation: int, seed: int
) -> dict[str, object]:
    """Build, production-load, register, and fenced-CAS activate a native taste map."""
    from marquee.core.jobs.internal_runner_host import (  # noqa: PLC0415
        OUTCOME_CANCELLED,
        OUTCOME_SUCCEEDED,
        run_internal_operation,
    )
    from marquee.core.jobs.runner_protocol import RunnerOperation  # noqa: PLC0415
    from marquee.ml.namespaces import get_namespace  # noqa: PLC0415
    from marquee.ml.taste_map import load_map  # noqa: PLC0415

    if context.cancellation.cancel_called:
        raise asyncio.CancelledError
    workspace_dir = _ml_workspace_dir(context)
    active_profile = await _stage_active_publication(
        context,
        family=f"taste_profile:{library}",
        destination=workspace_dir / "profile.npz",
    )

    bridge = _runner_bridge(context, _TASTE_MAP_STAGE_MAP)
    try:
        outcome = await run_internal_operation(
            context.process_launcher,
            operation=RunnerOperation.TASTE_MAP,
            manifest={
                "params": {
                    "library": library,
                    "seed": seed,
                    "profile_generation": active_profile.generation,
                    "profile_checksum": active_profile.checksum,
                }
            },
            on_progress=bridge.on_frame if bridge is not None else None,
            should_stop=lambda: bool(context.cancellation.cancel_called),
            resolve_output=lambda key: workspace_dir / key,
        )
    finally:
        if bridge is not None:
            await bridge.close()
    if outcome.outcome == OUTCOME_CANCELLED:
        raise asyncio.CancelledError
    if outcome.outcome != OUTCOME_SUCCEEDED:
        raise RuntimeError(f"taste map runner did not succeed: {outcome.error or outcome.outcome}")

    await _bridge_stage(bridge, "validating")
    map_path = workspace_dir / "map.npz"
    loaded = load_map(namespace=get_namespace(library), path=map_path)
    if not loaded.get("points"):
        raise RuntimeError("taste map production loader returned no points")
    if not await _ml_owns_fence(context):
        context.workspace.quarantine(code="stale_fence", summary="taste map publication fenced")
        raise RuntimeError("taste map attempt lost its fence before publication")

    await _bridge_stage(bridge, "registering")
    artifact = await register_physical_artifact(
        job_id=context.delivery.canonical_job_id,
        attempt_id=context.attempt.attempt_id,
        fence_token=context.attempt.fence_token,
        source=context.workspace.boundary.classify(map_path, require_exists=True),
        kind="taste_map",
        name="map.npz",
        content_type="application/octet-stream",
        retention_class="extended",
        metadata={
            "family": "taste_map",
            "library": library,
            "seed": seed,
            "profile_generation": active_profile.generation,
            "profile_checksum": active_profile.checksum,
        },
    )
    await _bridge_stage(bridge, "publishing")
    activation = await activate_immutable_artifact(
        context,
        family=f"taste_map:{library}",
        expected_generation=expected_generation,
        version=f"v1-{(artifact.checksum or '')[:16]}",
        artifact=artifact,
    )
    summary = outcome.summary if isinstance(outcome.summary, dict) else {}
    return MlPublicationResultV1(
        outcome="succeeded" if activation.activated else "superseded",
        family="taste_map",
        version=activation.version,
        checksum=activation.checksum,
        expected_generation=expected_generation,
        active_generation=activation.generation,
        activated=activation.activated,
        artifact_ids=(artifact.id,),
        metrics={
            "exemplars": int(summary.get("exemplars", len(loaded["points"])) or 0),
            "seed": seed,
        },
    ).model_dump(mode="json")


async def _publish_native_enrichment(
    context: ExecutionContext, *, library: str, expected_generation: int, seed: int
) -> dict[str, object]:
    """Publish enrichment as a native successor of the active taste profile."""
    from marquee.core.jobs.internal_runner_host import (  # noqa: PLC0415
        OUTCOME_CANCELLED,
        OUTCOME_SUCCEEDED,
        run_internal_operation,
    )
    from marquee.core.jobs.runner_protocol import RunnerOperation  # noqa: PLC0415

    if context.cancellation.cancel_called:
        raise asyncio.CancelledError
    workspace_dir = _ml_workspace_dir(context)
    active_profile = await _stage_active_publication(
        context,
        family=f"taste_profile:{library}",
        destination=workspace_dir / "source-profile.npz",
    )
    if active_profile.generation != expected_generation:
        return MlPublicationResultV1(
            outcome="superseded",
            family="taste_profile",
            version=active_profile.version,
            checksum=active_profile.checksum,
            expected_generation=expected_generation,
            active_generation=active_profile.generation,
            activated=False,
            artifact_ids=(),
            metrics={"seed": seed},
        ).model_dump(mode="json")

    bridge = _runner_bridge(context, _ENRICHMENT_STAGE_MAP)
    try:
        outcome = await run_internal_operation(
            context.process_launcher,
            operation=RunnerOperation.ENRICHMENT,
            manifest={
                "params": {
                    "library": library,
                    "seed": seed,
                    "profile_generation": active_profile.generation,
                    "profile_checksum": active_profile.checksum,
                    "use_tmdb": False,
                }
            },
            on_progress=bridge.on_frame if bridge is not None else None,
            should_stop=lambda: bool(context.cancellation.cancel_called),
            resolve_output=lambda key: workspace_dir / key,
        )
    finally:
        if bridge is not None:
            await bridge.close()
    if outcome.outcome == OUTCOME_CANCELLED:
        raise asyncio.CancelledError
    if outcome.outcome != OUTCOME_SUCCEEDED:
        raise RuntimeError(f"taste enrichment runner failed: {outcome.error or outcome.outcome}")

    await _bridge_stage(bridge, "validating")
    profile_path = workspace_dir / "profile.npz"
    _validate_taste_profile(profile_path)
    if not await _ml_owns_fence(context):
        context.workspace.quarantine(code="stale_fence", summary="taste enrichment fenced")
        raise RuntimeError("taste enrichment attempt lost its fence before publication")
    await _bridge_stage(bridge, "registering")
    artifact = await register_physical_artifact(
        job_id=context.delivery.canonical_job_id,
        attempt_id=context.attempt.attempt_id,
        fence_token=context.attempt.fence_token,
        source=context.workspace.boundary.classify(profile_path, require_exists=True),
        kind="taste_profile",
        name="profile.npz",
        content_type="application/octet-stream",
        retention_class="extended",
        metadata={
            "family": "taste_profile",
            "operation": "enrichment",
            "library": library,
            "seed": seed,
            "base_generation": active_profile.generation,
            "base_checksum": active_profile.checksum,
        },
    )
    await _bridge_stage(bridge, "publishing")
    activation = await activate_immutable_artifact(
        context,
        family=f"taste_profile:{library}",
        expected_generation=expected_generation,
        version=f"v1-{(artifact.checksum or '')[:16]}",
        artifact=artifact,
    )
    summary = outcome.summary if isinstance(outcome.summary, dict) else {}
    return MlPublicationResultV1(
        outcome="succeeded" if activation.activated else "superseded",
        family="taste_profile",
        version=activation.version,
        checksum=activation.checksum,
        expected_generation=expected_generation,
        active_generation=activation.generation,
        activated=activation.activated,
        artifact_ids=(artifact.id,),
        metrics={
            "exemplars": int(summary.get("exemplars", 0) or 0),
            "resolved": int(summary.get("resolved", 0) or 0),
            "seed": seed,
        },
    ).model_dump(mode="json")


async def _publish_native_learned_head(
    context: ExecutionContext,
    *,
    library: str,
    expected_generation: int,
    seed: int,
    feedback_revision: str,
) -> dict[str, object]:
    """Train, validate, register, and atomically activate a native learned head."""
    from marquee.core.jobs.internal_runner_host import (  # noqa: PLC0415
        OUTCOME_CANCELLED,
        OUTCOME_SUCCEEDED,
        run_internal_operation,
    )
    from marquee.core.jobs.runner_protocol import RunnerOperation  # noqa: PLC0415
    from marquee.ml import feedback_store  # noqa: PLC0415
    from marquee.ml.learned_head import LogisticHead  # noqa: PLC0415
    from marquee.ml.namespaces import get_namespace  # noqa: PLC0415

    if context.cancellation.cancel_called:
        raise asyncio.CancelledError
    try:
        async with context.session_factory() as session:
            current = await resolve_active_publication(
                session, family=f"learned_head:{library}"
            )
    except MlPublicationError:
        current = None
    current_generation = current.generation if current is not None else 0
    if current_generation != expected_generation:
        return MlPublicationResultV1(
            outcome="superseded",
            family="learned_head",
            version=current.version if current is not None else "unpublished",
            checksum=current.checksum if current is not None else "0" * 64,
            expected_generation=expected_generation,
            active_generation=current_generation,
            activated=False,
            artifact_ids=(),
            metrics={"seed": seed},
        ).model_dump(mode="json")
    workspace_dir = _ml_workspace_dir(context)
    snapshot_path = workspace_dir / "feedback.jsonl"
    source_path = feedback_store.labels_path(get_namespace(library))
    feedback_checksum = hashlib.sha256(b"").hexdigest()
    if source_path.is_file():
        if source_path.stat().st_size > 64 * 1024 * 1024:
            raise RuntimeError("feedback source exceeds the learned-head snapshot limit")
        copied = await context.io.copy(source_path, snapshot_path)
        feedback_checksum = copied.sha256

    configuration = context.configuration
    outcome = await run_internal_operation(
        context.process_launcher,
        operation=RunnerOperation.LEARNED_HEAD,
        manifest={
            "params": {
                "library": library,
                "seed": seed,
                "feedback_revision": feedback_revision,
                "feedback_checksum": feedback_checksum,
                "mode": configuration.get("HEAD_TRAIN_MODE"),
                "min_labels": configuration.get("HEAD_MIN_LABELS"),
                "min_movies": configuration.get("HEAD_MIN_MOVIES"),
                "min_pairs": configuration.get("HEAD_MIN_PAIRS"),
            }
        },
        should_stop=lambda: bool(context.cancellation.cancel_called),
        resolve_output=lambda key: workspace_dir / key,
    )
    if outcome.outcome == OUTCOME_CANCELLED:
        raise asyncio.CancelledError
    summary = outcome.summary if isinstance(outcome.summary, dict) else {}
    if outcome.outcome == "no_change":
        return MlPublicationResultV1(
            outcome="no_change",
            family="learned_head",
            version=current.version if current is not None else "unpublished",
            checksum=current.checksum if current is not None else "0" * 64,
            expected_generation=expected_generation,
            active_generation=current_generation,
            activated=False,
            artifact_ids=(),
            metrics={
                "feedback_rows": int(summary.get("feedback_rows", 0) or 0),
                "seed": seed,
            },
        ).model_dump(mode="json")
    if outcome.outcome != OUTCOME_SUCCEEDED:
        raise RuntimeError(f"learned-head runner failed: {outcome.error or outcome.outcome}")

    head_path = workspace_dir / "head.npz"
    LogisticHead.load(head_path)
    if not await _ml_owns_fence(context):
        context.workspace.quarantine(code="stale_fence", summary="learned head fenced")
        raise RuntimeError("learned-head attempt lost its fence before publication")
    artifact = await register_physical_artifact(
        job_id=context.delivery.canonical_job_id,
        attempt_id=context.attempt.attempt_id,
        fence_token=context.attempt.fence_token,
        source=context.workspace.boundary.classify(head_path, require_exists=True),
        kind="learned_head",
        name="head.npz",
        content_type="application/octet-stream",
        retention_class="extended",
        metadata={
            "family": "learned_head",
            "library": library,
            "seed": seed,
            "feedback_revision": feedback_revision,
            "feedback_checksum": feedback_checksum,
        },
    )
    activation = await activate_immutable_artifact(
        context,
        family=f"learned_head:{library}",
        expected_generation=expected_generation,
        version=f"v1-{(artifact.checksum or '')[:16]}",
        artifact=artifact,
    )
    return MlPublicationResultV1(
        outcome="succeeded" if activation.activated else "superseded",
        family="learned_head",
        version=activation.version,
        checksum=activation.checksum,
        expected_generation=expected_generation,
        active_generation=activation.generation,
        activated=activation.activated,
        artifact_ids=(artifact.id,),
        metrics={
            "feedback_rows": int(summary.get("feedback_rows", 0) or 0),
            "n_movies": int(summary.get("n_movies", 0) or 0),
            "n_pairs": int(summary.get("n_pairs", 0) or 0),
            "seed": seed,
        },
    ).model_dump(mode="json")


async def execute_taste_rebuild(context: ExecutionContext) -> dict[str, object]:
    request = TasteRebuildRequestV1.model_validate(context.request)
    return await _publish_native_taste_profile(
        context,
        library=request.library,
        expected_generation=request.expected_generation,
        seed=request.seed,
    )


async def execute_taste_map(context: ExecutionContext) -> dict[str, object]:
    request = TasteMapRequestV1.model_validate(context.request)
    return await _publish_native_taste_map(
        context,
        library=request.library,
        expected_generation=request.expected_generation,
        seed=request.seed,
    )


async def execute_taste_enrich(context: ExecutionContext) -> dict[str, object]:
    request = TasteEnrichRequestV1.model_validate(context.request)
    return await _publish_native_enrichment(
        context,
        library=request.library,
        expected_generation=request.expected_generation,
        seed=request.seed,
    )


async def execute_learned_head(context: ExecutionContext) -> dict[str, object]:
    request = LearnedHeadTrainRequestV1.model_validate(context.request)
    return await _publish_native_learned_head(
        context,
        library=request.library,
        expected_generation=request.expected_generation,
        seed=request.seed,
        feedback_revision=request.feedback_revision,
    )


register_execution_handler("taste_rebuild", execute_taste_rebuild)
register_execution_handler("taste_map", execute_taste_map)
register_execution_handler("taste_enrich", execute_taste_enrich)
register_execution_handler("learned_head_train", execute_learned_head)
