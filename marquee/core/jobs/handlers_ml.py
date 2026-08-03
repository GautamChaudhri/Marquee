"""Canonical immutable ML publication handlers for JMC4C."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from sqlalchemy import select

from marquee.core.jobs.artifact_service import (
    physical_artifact_file,
    register_physical_artifact,
    verify_physical_artifact,
)
from marquee.core.jobs.delivery import ExecutionContext, register_execution_handler
from marquee.core.jobs.documents import (
    MlPublicationResultV1,
    RankingResidualTrainRequestV1,
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
from marquee.core.pipeline_config import pipeline_settings
from marquee.core.taste_preferences import mark_profile_build_running, record_profile_build_terminal
from marquee.ml.residual import ResidualArtifact, baseline_signature, freeze_residual_evidence
from marquee.models import (
    JobArtifact,
    PosterPreferenceEvent,
    TasteExemplar,
    TasteProfileRevision,
)

# Runner-native trainer stages -> the registered ML progress vocabulary (JMC6I
# §6.2). Each active ML publication below has an explicit bridge.
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


async def _stage_tv_library_scan(
    context: ExecutionContext, workspace_dir: Path
) -> tuple[dict[str, dict[str, object]], dict[str, float]]:
    """Copy deployed TV artwork into the attempt workspace as the training set.

    Positives come from the library: every ``show.jpg`` and ``seasonNN.jpg`` the
    scanner finds, staged into per-kind subdirectories so the trainer tags each
    poster with the kind it actually is. Negatives still come from recorded
    evidence — a poster you rejected never lands on disk, so the library alone
    cannot express dislike.

    The scan runs here rather than in the runner because the runner is confined to
    the workspace and cannot reach the media roots.
    """
    from marquee.core.tv_taste_scan import scan_tv_posters  # noqa: PLC0415

    async with context.session_factory() as session:
        records = await scan_tv_posters(session)
    floor = pipeline_settings.TV_TASTE_MIN_POSTERS
    if len(records) < floor:
        raise RuntimeError(
            f"TV taste profile needs at least {floor} deployed posters, found {len(records)}. "
            "Add artwork to the library or lower TV_TASTE_MIN_POSTERS."
        )

    training_dir = workspace_dir / "training"
    negative_dir = workspace_dir / "negative"
    negative_dir.mkdir(parents=True, exist_ok=True)
    asset_identity: dict[str, dict[str, object]] = {}
    for record in records:
        destination = training_dir / record.asset_kind
        destination.mkdir(parents=True, exist_ok=True)
        await context.io.copy(record.path, destination / record.staged_name)
        asset_identity[record.staged_name] = record.identity()

    async with context.session_factory() as session:
        negatives = list(
            (
                await session.scalars(
                    select(TasteExemplar)
                    .where(
                        TasteExemplar.namespace.in_(("global", "tv")),
                        TasteExemplar.polarity == "negative",
                        TasteExemplar.status == "active",
                        TasteExemplar.retained_artifact_id.is_not(None),
                    )
                    .order_by(TasteExemplar.id)
                )
            ).all()
        )
        artifacts = {
            row.id: await session.get(JobArtifact, row.retained_artifact_id) for row in negatives
        }
    negative_weights: dict[str, float] = {}
    for exemplar in negatives:
        retained = artifacts.get(exemplar.id)
        if retained is None or retained.checksum != exemplar.checksum:
            raise RuntimeError("TV taste negative exemplar artifact lineage is invalid")
        await verify_physical_artifact(retained)
        _boundary, classified = physical_artifact_file(retained)
        source_path = classified.root.resolved().joinpath(*classified.key.parts)
        filename = f"{exemplar.id}.jpg"
        copied = await context.io.copy(source_path, negative_dir / filename)
        if copied.sha256 != exemplar.checksum:
            raise RuntimeError("TV taste negative exemplar changed while staging")
        negative_weights[filename] = float(exemplar.evidence_weight)
    return asset_identity, negative_weights


async def _stage_seeding_bundle(
    context: ExecutionContext, workspace_dir: Path, *, library: str
) -> int:
    """TEMPORARY (seeding bundle): stage curated posters on disk as the training set.

    The canonical movies path needs a frozen revision with enough positive subjects,
    which a fresh install does not have. This trains on a bundled directory instead so
    the engine has something to rank with on day one. It stages positives only — the
    resulting profile has no negative exemplars, unlike every canonical build.

    Runs in the handler rather than the runner for the same reason the TV library scan
    does: the runner is confined to the attempt workspace and cannot reach data roots.
    """
    bundle = pipeline_settings.TASTE_SEEDING_DIR / library
    if not bundle.is_dir():
        raise RuntimeError(f"taste seeding bundle not found at {bundle}")
    # Dotfiles are load-bearing to exclude: the bundle ships a .actors.jpg sidecar that
    # scan_images would otherwise happily embed as an exemplar named ".actors".
    posters = sorted(
        path
        for path in bundle.iterdir()
        if path.is_file()
        and not path.name.startswith(".")
        and path.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}
    )
    floor = pipeline_settings.TASTE_SEEDING_MIN_POSTERS
    if len(posters) < floor:
        raise RuntimeError(
            f"taste seeding bundle needs at least {floor} posters, found {len(posters)} in {bundle}"
        )
    training_dir = workspace_dir / "training"
    training_dir.mkdir(parents=True, exist_ok=True)
    for poster in posters:
        await context.io.copy(poster, training_dir / poster.name)
    return len(posters)


async def _publish_native_taste_profile(
    context: ExecutionContext,
    *,
    library: str,
    expected_generation: int,
    seed: int,
    source: str = "canonical",
    revision_digest: str | None = None,
    profile_build_id: str | None = None,
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
    source_mode = "library"
    positive_weights: dict[str, float] = {}
    negative_weights: dict[str, float] = {}
    asset_identity: dict[str, dict[str, object]] = {}
    if revision_digest is not None:
        async with context.session_factory() as session:
            revision = await session.get(TasteProfileRevision, revision_digest)
            if revision is None or revision.positive_subjects < 50:
                raise RuntimeError("canonical taste revision is unavailable or below threshold")
            frozen = list(
                (
                    await session.scalars(
                        select(TasteExemplar).where(
                            TasteExemplar.id.in_(revision.exemplar_ids),
                        )
                    )
                ).all()
            )
            by_id = {row.id: row for row in frozen}
            manifest_rows = revision.exemplar_manifest
            if len(manifest_rows) != len(revision.exemplar_ids) or any(
                not isinstance(entry, dict) or entry.get("exemplar_id") != exemplar_id
                for entry, exemplar_id in zip(manifest_rows, revision.exemplar_ids, strict=True)
            ):
                raise RuntimeError("canonical taste revision has an invalid frozen manifest")
            if len(by_id) != len(revision.exemplar_ids) or any(
                by_id.get(exemplar_id) is None
                or by_id[exemplar_id].status != "active"
                or by_id[exemplar_id].checksum != entry.get("checksum")
                or by_id[exemplar_id].namespace != entry.get("namespace")
                or by_id[exemplar_id].polarity != entry.get("polarity")
                or float(by_id[exemplar_id].evidence_weight) != float(entry.get("weight", 0))
                or by_id[exemplar_id].retained_artifact_id != entry.get("retained_artifact_id")
                or by_id[exemplar_id].embedding_identity != entry.get("embedding_identity")
                or by_id[exemplar_id].supersedes_exemplar_id != entry.get("supersedes_exemplar_id")
                for exemplar_id, entry in zip(revision.exemplar_ids, manifest_rows, strict=True)
            ):
                raise RuntimeError("canonical taste revision no longer matches frozen evidence")
            exemplars = [row for row in frozen if row.namespace in {"global", library}]
            artifacts = {
                row.id: await session.get(JobArtifact, row.retained_artifact_id)
                for row in exemplars
                if row.retained_artifact_id is not None
            }
        if (
            len(
                {
                    (row.subject_kind, row.subject_reference)
                    for row in exemplars
                    if row.polarity == "positive"
                }
            )
            < 50
        ):
            raise RuntimeError("canonical taste revision lacks enough applicable positive subjects")
        training_dir = workspace_dir / "training"
        negative_dir = workspace_dir / "negative"
        training_dir.mkdir(parents=True, exist_ok=True)
        negative_dir.mkdir(parents=True, exist_ok=True)
        for exemplar in sorted(exemplars, key=lambda row: row.id):
            retained = artifacts.get(exemplar.id)
            if retained is None or retained.checksum != exemplar.checksum:
                raise RuntimeError("canonical taste exemplar artifact lineage is invalid")
            await verify_physical_artifact(retained)
            _boundary, classified = physical_artifact_file(retained)
            source_path = classified.root.resolved().joinpath(*classified.key.parts)
            filename = f"{exemplar.id}.jpg"
            destination = training_dir if exemplar.polarity == "positive" else negative_dir
            copied = await context.io.copy(source_path, destination / filename)
            if copied.sha256 != exemplar.checksum:
                raise RuntimeError("canonical taste exemplar changed while staging")
            if exemplar.polarity == "positive":
                positive_weights[filename] = float(exemplar.evidence_weight)
            else:
                negative_weights[filename] = float(exemplar.evidence_weight)
        source_mode = "fixture"
    elif source == "seeding_bundle":  # TEMPORARY (seeding bundle)
        await _stage_seeding_bundle(context, workspace_dir, library=library)
        source_mode = "seeding_bundle"
    elif library == "tv":
        asset_identity, negative_weights = await _stage_tv_library_scan(context, workspace_dir)
        source_mode = "library_scan"
    manifest = {
        "params": {
            "family": family,
            "library": library,
            "seed": seed,
            "source": {
                "mode": source_mode,
                "positive_weights": positive_weights,
                "negative_weights": negative_weights,
                "asset_identity": asset_identity,
            },
            "revision": revision_digest,
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
            configuration=context.configuration,
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
        raise RuntimeError(
            f"taste profile runner did not succeed: {outcome.error or outcome.outcome}"
        )

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
        metadata={
            "family": family,
            "library": library,
            "seed": seed,
            "revision": revision_digest,
        },
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
    _validate_taste_profile(profile_path)
    if revision_digest is not None and not activation.activated:
        async with context.session_factory() as session:
            revision = await session.get(
                TasteProfileRevision, revision_digest, with_for_update=True
            )
            if revision is not None:
                revision.state = "failed"
                revision.failure = {
                    "reason": "publication_conflict",
                    "library": library,
                    "job_id": context.delivery.canonical_job_id,
                }
                await session.commit()
    if revision_digest is not None and activation.activated:
        async with context.session_factory() as session:
            revision = await session.get(
                TasteProfileRevision, revision_digest, with_for_update=True
            )
            if revision is None:
                raise RuntimeError("canonical taste revision disappeared during publication")
            if library == "movies":
                revision.movie_generation = activation.generation
                revision.movie_checksum = activation.checksum
            else:
                revision.tv_generation = activation.generation
                revision.tv_checksum = activation.checksum
            if revision.state != "failed":
                revision.state = "published"
            await session.commit()
    if profile_build_id is not None:
        async with context.session_factory() as session:
            await record_profile_build_terminal(
                session,
                build_id=profile_build_id,
                job_id=context.delivery.canonical_job_id,
                state="succeeded" if activation.activated else "superseded",
                result_generation=activation.generation,
                result_checksum=activation.checksum,
                consumer_reload_checksum=None,
            )
            await session.commit()
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
        metrics={
            "exemplars": int(summary.get("exemplars", 0) or 0),
            "negatives": int(summary.get("negatives", 0) or 0),
            "seed": seed,
        },
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
            configuration=context.configuration,
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
            configuration=context.configuration,
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


async def _publish_native_ranking_residual(
    context: ExecutionContext,
    *,
    library: str,
    expected_generation: int,
    seed: int,
    evidence_revision: str,
    expected_baseline_signature: str | None = None,
    expected_profile_checksum: str | None = None,
    expected_profile_generation: int | None = None,
) -> dict[str, object]:
    """Train, evaluate, register, and atomically activate a bounded residual."""
    from marquee.core.jobs.internal_runner_host import (  # noqa: PLC0415
        OUTCOME_CANCELLED,
        OUTCOME_SUCCEEDED,
        run_internal_operation,
    )
    from marquee.core.jobs.runner_protocol import RunnerOperation  # noqa: PLC0415

    if context.cancellation.cancel_called:
        raise asyncio.CancelledError
    async with context.session_factory() as session:
        try:
            current = await resolve_active_publication(
                session, family=f"ranking_residual:{library}"
            )
        except MlPublicationError:
            current = None
        try:
            profile = await resolve_active_publication(session, family=f"taste_profile:{library}")
        except MlPublicationError:
            return MlPublicationResultV1(
                outcome="no_change",
                family="ranking_residual",
                version="unpublished",
                checksum="0" * 64,
                expected_generation=expected_generation,
                active_generation=0,
                activated=False,
                artifact_ids=(),
                metrics={"seed": seed, "events": 0},
            ).model_dump(mode="json")
    current_generation = current.generation if current is not None else 0
    if current_generation != expected_generation:
        return MlPublicationResultV1(
            outcome="superseded",
            family="ranking_residual",
            version=current.version if current is not None else "unpublished",
            checksum=current.checksum if current is not None else "0" * 64,
            expected_generation=expected_generation,
            active_generation=current_generation,
            activated=False,
            artifact_ids=(),
            metrics={"seed": seed},
        ).model_dump(mode="json")
    workspace_dir = _ml_workspace_dir(context)
    snapshot_path = workspace_dir / "preference-events.json"
    async with context.session_factory() as session:
        events = list(
            (
                await session.scalars(
                    select(PosterPreferenceEvent)
                    .where(PosterPreferenceEvent.namespace == library)
                    .order_by(PosterPreferenceEvent.created_at, PosterPreferenceEvent.id)
                    .limit(100_000)
                )
            ).all()
        )
    frozen_evidence = freeze_residual_evidence(events)
    encoded = json.dumps(
        frozen_evidence.rows, allow_nan=False, separators=(",", ":"), sort_keys=True
    ).encode()
    if len(encoded) > 64 * 1024 * 1024:
        raise RuntimeError("canonical preference snapshot exceeds the residual runner limit")
    await asyncio.to_thread(snapshot_path.write_text, encoded.decode("utf-8"), encoding="utf-8")
    snapshot_checksum = frozen_evidence.checksum
    actual_revision = frozen_evidence.digest
    if evidence_revision != actual_revision:
        return MlPublicationResultV1(
            outcome="superseded",
            family="ranking_residual",
            version=current.version if current is not None else "unpublished",
            checksum=current.checksum if current is not None else "0" * 64,
            expected_generation=expected_generation,
            active_generation=current_generation,
            activated=False,
            artifact_ids=(),
            metrics={"seed": seed, "events": len(events)},
        ).model_dump(mode="json")

    configuration = context.configuration
    weights = {
        name: float(configuration.get(key, getattr(pipeline_settings, key)))
        for name, key in {
            "knn_sim": "WEIGHT_KNN_SIM",
            "aesthetic": "WEIGHT_AESTHETIC",
            "title_colorfulness": "WEIGHT_TITLE_COLORFULNESS",
            "face_area": "WEIGHT_FACE_AREA",
            "text_residual": "WEIGHT_TEXT_RESIDUAL",
            "provenance": "WEIGHT_PROVENANCE",
            "sharpness": "WEIGHT_SHARPNESS",
            "resolution": "WEIGHT_RESOLUTION",
            "lang_match": "WEIGHT_LANG_MATCH",
            "dino_knn": "WEIGHT_DINO_KNN",
            "taste_typicality": "WEIGHT_TASTE_TYPICALITY",
            "quality_artifacts": "WEIGHT_QUALITY_ARTIFACTS",
            "official_family": "WEIGHT_OFFICIAL_FAMILY",
        }.items()
    }
    actual_baseline_signature = baseline_signature(weights)
    if (
        (
            expected_baseline_signature is not None
            and expected_baseline_signature != actual_baseline_signature
        )
        or (expected_profile_checksum is not None and expected_profile_checksum != profile.checksum)
        or (
            expected_profile_generation is not None
            and expected_profile_generation != profile.generation
        )
    ):
        return MlPublicationResultV1(
            outcome="superseded",
            family="ranking_residual",
            version=current.version if current is not None else "unpublished",
            checksum=current.checksum if current is not None else "0" * 64,
            expected_generation=expected_generation,
            active_generation=current_generation,
            activated=False,
            artifact_ids=(),
            metrics={"seed": seed, "events": len(frozen_evidence.event_ids)},
        ).model_dump(mode="json")
    if current is not None:
        active_residual = ResidualArtifact.load(current.path)
        compatible, _reason = active_residual.compatible(
            namespace=library,
            baseline=baseline_signature(weights),
            profile_checksum=profile.checksum,
            profile_generation=profile.generation,
        )
        if compatible:
            await context.io.copy(current.path, workspace_dir / "active-residual.npz")
    outcome = await run_internal_operation(
        context.process_launcher,
        operation=RunnerOperation.RANKING_RESIDUAL,
        manifest={
            "params": {
                "library": library,
                "seed": seed,
                "evidence_revision": actual_revision,
                "snapshot_checksum": snapshot_checksum,
                "baseline_signature": actual_baseline_signature,
                "profile_checksum": profile.checksum,
                "profile_generation": profile.generation,
                "min_subjects": configuration.get("RESIDUAL_MIN_SUBJECTS", 25),
                "min_pairs": configuration.get("RESIDUAL_MIN_PAIRS", 200),
                "min_improvement": 0.02,
            }
        },
        configuration=context.configuration,
        should_stop=lambda: bool(context.cancellation.cancel_called),
        resolve_output=lambda key: workspace_dir / key,
    )
    if outcome.outcome == OUTCOME_CANCELLED:
        raise asyncio.CancelledError
    summary = outcome.summary if isinstance(outcome.summary, dict) else {}
    if outcome.outcome != OUTCOME_SUCCEEDED:
        raise RuntimeError(f"residual runner failed: {outcome.error or outcome.outcome}")
    if summary.get("publication_outcome") == "no_change":
        return MlPublicationResultV1(
            outcome="no_change",
            family="ranking_residual",
            version=current.version if current is not None else "unpublished",
            checksum=current.checksum if current is not None else "0" * 64,
            expected_generation=expected_generation,
            active_generation=current_generation,
            activated=False,
            artifact_ids=(),
            metrics={
                "events": int(summary.get("event_rows", 0) or 0),
                "pairs": int(summary.get("pairs", 0) or 0),
                "seed": seed,
            },
        ).model_dump(mode="json")
    residual_path = workspace_dir / "residual.npz"
    residual = ResidualArtifact.load(residual_path)
    if (
        residual.evidence_revision != actual_revision
        or residual.profile_checksum != profile.checksum
        or residual.profile_generation != profile.generation
    ):
        raise RuntimeError("residual artifact lineage does not match its frozen inputs")
    if not await _ml_owns_fence(context):
        context.workspace.quarantine(code="stale_fence", summary="ranking residual fenced")
        raise RuntimeError("residual attempt lost its fence before publication")
    artifact = await register_physical_artifact(
        job_id=context.delivery.canonical_job_id,
        attempt_id=context.attempt.attempt_id,
        fence_token=context.attempt.fence_token,
        source=context.workspace.boundary.classify(residual_path, require_exists=True),
        kind="ranking_residual",
        name="residual.npz",
        content_type="application/octet-stream",
        retention_class="extended",
        metadata={
            "family": "ranking_residual",
            "library": library,
            "seed": seed,
            "evidence_revision": actual_revision,
            "snapshot_checksum": snapshot_checksum,
            "baseline_signature": residual.baseline_signature,
            "profile_checksum": residual.profile_checksum,
            "profile_generation": residual.profile_generation,
            "evaluation": summary.get("evaluation"),
            "subject_count": int(summary.get("subjects", 0) or 0),
            "pair_count": int(summary.get("pairs", 0) or 0),
            "partitions": residual.partitions,
        },
    )
    activation = await activate_immutable_artifact(
        context,
        family=f"ranking_residual:{library}",
        expected_generation=expected_generation,
        version=f"v1-{(artifact.checksum or '')[:16]}",
        artifact=artifact,
    )
    return MlPublicationResultV1(
        outcome="succeeded" if activation.activated else "superseded",
        family="ranking_residual",
        version=activation.version,
        checksum=activation.checksum,
        expected_generation=expected_generation,
        active_generation=activation.generation,
        activated=activation.activated,
        artifact_ids=(artifact.id,),
        metrics={
            "events": int(summary.get("event_rows", 0) or 0),
            "pairs": int(summary.get("pairs", 0) or 0),
            "improvement": float((summary.get("evaluation") or {}).get("improvement", 0.0)),
            "seed": seed,
        },
    ).model_dump(mode="json")


async def execute_taste_rebuild(context: ExecutionContext) -> dict[str, object]:
    request = TasteRebuildRequestV1.model_validate(context.request)
    try:
        if request.profile_build_id is not None:
            async with context.session_factory() as session:
                await mark_profile_build_running(
                    session,
                    build_id=request.profile_build_id,
                    job_id=context.delivery.canonical_job_id,
                )
                await session.commit()
        return await _publish_native_taste_profile(
            context,
            library=request.library,
            expected_generation=request.expected_generation,
            seed=request.seed,
            source=request.source,
            revision_digest=request.revision,
            profile_build_id=request.profile_build_id,
        )
    except BaseException as exc:
        if request.revision is not None:
            async with context.session_factory() as session:
                revision = await session.get(
                    TasteProfileRevision, request.revision, with_for_update=True
                )
                if revision is not None and revision.state != "personalized":
                    revision.state = "failed"
                    revision.failure = {
                        "reason": "cancelled"
                        if isinstance(exc, asyncio.CancelledError)
                        else "build_failed",
                        "library": request.library,
                        "error_type": type(exc).__name__,
                        "job_id": context.delivery.canonical_job_id,
                    }
                    await session.commit()
        if request.profile_build_id is not None:
            async with context.session_factory() as session:
                await record_profile_build_terminal(
                    session,
                    build_id=request.profile_build_id,
                    job_id=context.delivery.canonical_job_id,
                    state="cancelled" if isinstance(exc, asyncio.CancelledError) else "failed",
                    failure={
                        "reason": "cancelled"
                        if isinstance(exc, asyncio.CancelledError)
                        else "build_failed",
                        "error_type": type(exc).__name__,
                        "job_id": context.delivery.canonical_job_id,
                    },
                )
                await session.commit()
        raise


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


async def execute_ranking_residual(context: ExecutionContext) -> dict[str, object]:
    request = RankingResidualTrainRequestV1.model_validate(context.request)
    return await _publish_native_ranking_residual(
        context,
        library=request.library,
        expected_generation=request.expected_generation,
        seed=request.seed,
        evidence_revision=request.evidence_revision,
        expected_baseline_signature=request.baseline_signature,
        expected_profile_checksum=request.profile_checksum,
        expected_profile_generation=request.profile_generation,
    )


register_execution_handler("taste_rebuild", execute_taste_rebuild)
register_execution_handler("taste_map", execute_taste_map)
register_execution_handler("taste_enrich", execute_taste_enrich)
register_execution_handler("ranking_residual_train", execute_ranking_residual)
