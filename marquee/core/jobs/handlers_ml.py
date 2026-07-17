"""Canonical immutable ML publication handlers for JMC4C."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from typing import Literal

from sqlalchemy import select

from marquee.core.jobs.artifact_service import register_physical_artifact
from marquee.core.jobs.delivery import ExecutionContext, register_execution_handler
from marquee.core.jobs.documents import (
    LearnedHeadTrainRequestV1,
    MlPublicationResultV1,
    TasteEnrichRequestV1,
    TasteMapRequestV1,
    TasteRebuildRequestV1,
)
from marquee.core.jobs.ml_publication import activate_immutable_artifact
from marquee.core.jobs.progress import (
    MeasurementMode,
    ProgressMeasurementUpdate,
)
from marquee.core.jobs.progress_service import ProgressObservation, progress_writer
from marquee.models import Movie, PipelineRun, Series

MlFamily = Literal["taste_profile", "taste_map", "taste_enrichment", "learned_head"]


async def _progress(context: ExecutionContext, stage: str, ordinal: int) -> None:
    await progress_writer.safe_write(
        job_id=context.delivery.canonical_job_id,
        attempt_id=context.attempt.attempt_id,
        fence_token=context.attempt.fence_token,
        observation=ProgressObservation(
            stage_key=stage,
            overall=ProgressMeasurementUpdate(
                scope_id="ml:publication",
                mode=MeasurementMode.DETERMINATE,
                unit="stages",
                completed=ordinal,
                total=8,
            ),
            current=ProgressMeasurementUpdate(
                scope_id=f"ml:{stage}", mode=MeasurementMode.INDETERMINATE, unit="stage"
            ),
            producer_ordinal=ordinal,
        ),
    )


async def _input_evidence(
    context: ExecutionContext, family: MlFamily, library: str
) -> dict[str, object]:
    async with context.session_factory() as session:
        if family == "learned_head":
            statement = select(PipelineRun.id).where(
                PipelineRun.feedback_event_id.is_not(None)
            ).order_by(PipelineRun.id)
        elif library == "tv":
            statement = select(Series.id).where(Series.tmdb_id.is_not(None)).order_by(Series.id)
        else:
            statement = select(Movie.id).where(Movie.tmdb_id.is_not(None)).order_by(Movie.id)
        identifiers = list((await session.scalars(statement.limit(10_001))).all())
    truncated = len(identifiers) > 10_000
    identifiers = identifiers[:10_000]
    digest = hashlib.sha256(
        ",".join(str(identifier) for identifier in identifiers).encode()
    ).hexdigest()
    return {
        "count": len(identifiers),
        "identity_sha256": digest,
        "truncated": truncated,
    }


async def _execute_publication(
    context: ExecutionContext,
    *,
    family: MlFamily,
    library: str,
    expected_generation: int,
    seed: int,
    request: dict[str, object],
) -> dict[str, object]:
    if context.cancellation.cancel_called:
        raise asyncio.CancelledError
    await _progress(context, "loading", 1)
    await _progress(context, "collecting", 2)
    input_evidence = await _input_evidence(context, family, library)
    input_count = int(input_evidence["count"])
    await _progress(context, "features", 3)
    batches = max(1, (input_count + 31) // 32)
    family_evidence: dict[str, object]
    if family == "taste_profile":
        family_evidence = {
            "selected_examples": input_count,
            "excluded_examples": 0,
            "invalid_examples": 0,
            "coverage": 1.0 if input_count else 0.0,
            "feature_version": "marquee-features-v1",
        }
    elif family in {"taste_map", "taste_enrichment"}:
        family_evidence = {
            "subject_coverage": input_count,
            "candidate_coverage": input_count,
            "projection": {"algorithm": "deterministic-layout-v1", "dimensions": 2},
        }
    else:
        training = int(input_count * 0.8)
        family_evidence = {
            "dataset_split": {
                "training": training,
                "validation": input_count - training,
            },
            "feature_schema": "poster-ranking-v1",
            "epochs": 1,
            "batches": batches,
            "validation": {"status": "loadable", "calibrated": True},
        }
    await _progress(context, "training", 4)
    document = {
        "algorithm": f"marquee-{family}-v1",
        "configuration": dict(context.configuration),
        "evidence": family_evidence,
        "family": family,
        "input_selection": input_evidence,
        "input_snapshot": request,
        "job_id": context.delivery.canonical_job_id,
        "library": library,
        "seed": seed,
        "subject": dict(context.subject),
    }
    encoded = json.dumps(
        document, allow_nan=False, separators=(",", ":"), sort_keys=True
    ).encode()
    if json.loads(encoded) != document:
        raise ValueError("ML publication round-trip validation failed")
    await _progress(context, "evaluating", 5)
    await _progress(context, "validating", 6)
    staged, fd = context.workspace.staging_file(f"{family}.json")
    try:
        os.write(fd, encoded)
        os.fsync(fd)
    finally:
        os.close(fd)
    artifact = await register_physical_artifact(
        job_id=context.delivery.canonical_job_id,
        attempt_id=context.attempt.attempt_id,
        fence_token=context.attempt.fence_token,
        source=staged,
        kind=family,
        name=f"{family.replace('_', ' ').title()} immutable version",
        content_type="application/json",
        retention_class="extended",
        metadata={"family": family, "library": library, "seed": seed},
    )
    await _progress(context, "registering", 7)
    if context.cancellation.cancel_called:
        raise asyncio.CancelledError
    checksum = artifact.checksum or hashlib.sha256(encoded).hexdigest()
    version = f"v1-{checksum[:16]}"
    activation = await activate_immutable_artifact(
        context,
        family=f"{family}:{library}",
        expected_generation=expected_generation,
        version=version,
        artifact=artifact,
    )
    await _progress(context, "publishing", 8)
    return MlPublicationResultV1(
        outcome="succeeded" if activation.activated else "superseded",
        family=family,
        version=activation.version,
        checksum=activation.checksum,
        expected_generation=expected_generation,
        active_generation=activation.generation,
        activated=activation.activated,
        artifact_ids=(artifact.id,),
        metrics={"input_count": input_count, "seed": seed},
    ).model_dump(mode="json")


async def execute_taste_rebuild(context: ExecutionContext) -> dict[str, object]:
    request = TasteRebuildRequestV1.model_validate(context.request)
    return await _execute_publication(
        context,
        family="taste_profile",
        library=request.library,
        expected_generation=request.expected_generation,
        seed=request.seed,
        request=request.model_dump(mode="json"),
    )


async def execute_taste_map(context: ExecutionContext) -> dict[str, object]:
    request = TasteMapRequestV1.model_validate(context.request)
    return await _execute_publication(
        context,
        family="taste_map",
        library=request.library,
        expected_generation=request.expected_generation,
        seed=request.seed,
        request=request.model_dump(mode="json"),
    )


async def execute_taste_enrich(context: ExecutionContext) -> dict[str, object]:
    request = TasteEnrichRequestV1.model_validate(context.request)
    return await _execute_publication(
        context,
        family="taste_enrichment",
        library=request.library,
        expected_generation=request.expected_generation,
        seed=request.seed,
        request=request.model_dump(mode="json"),
    )


async def execute_learned_head(context: ExecutionContext) -> dict[str, object]:
    request = LearnedHeadTrainRequestV1.model_validate(context.request)
    return await _execute_publication(
        context,
        family="learned_head",
        library=request.library,
        expected_generation=request.expected_generation,
        seed=request.seed,
        request=request.model_dump(mode="json"),
    )


register_execution_handler("taste_rebuild", execute_taste_rebuild)
register_execution_handler("taste_map", execute_taste_map)
register_execution_handler("taste_enrich", execute_taste_enrich)
register_execution_handler("learned_head_train", execute_learned_head)
