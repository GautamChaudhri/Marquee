"""Fenced compare-and-set activation for immutable ML job artifacts."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from sqlalchemy import select, text

from marquee.core.jobs.delivery import ExecutionContext
from marquee.core.jobs.event_service import job_event_writer
from marquee.models import Job, JobArtifact, MlActivePublication


class MlPublicationError(RuntimeError):
    """The immutable artifact or activation provenance is invalid."""


@dataclass(frozen=True, slots=True)
class ActivationResult:
    activated: bool
    generation: int
    version: str
    checksum: str


async def activate_immutable_artifact(
    context: ExecutionContext,
    *,
    family: str,
    expected_generation: int,
    version: str,
    artifact: JobArtifact,
) -> ActivationResult:
    """CAS one family pointer after artifact registration and final fence/cancel checks."""
    if context.cancellation.cancel_called:
        raise asyncio.CancelledError
    if artifact.status != "available" or artifact.checksum is None:
        raise MlPublicationError("immutable artifact is not available")
    if artifact.job_id != context.delivery.canonical_job_id:
        raise MlPublicationError("immutable artifact belongs to another job")
    if artifact.attempt_id != context.attempt.attempt_id:
        raise MlPublicationError("immutable artifact belongs to another attempt")
    if artifact.kind != family.partition(":")[0]:
        raise MlPublicationError("immutable artifact kind does not match its publication family")

    async with context.session_factory() as session, session.begin():
        # Serialize the absent-row case as well as ordinary updates without relying on a
        # process-local lock. The family string is bounded by the request/handler policy.
        await session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:family, 0))"),
            {"family": family},
        )
        if context.cancellation.cancel_called:
            raise asyncio.CancelledError
        if not await context.writer.owns_current_attempt(session):
            raise MlPublicationError("stale attempt cannot activate an ML artifact")
        job = await session.scalar(
            select(Job).where(Job.id == context.delivery.canonical_job_id).with_for_update()
        )
        stored_artifact = await session.scalar(
            select(JobArtifact).where(JobArtifact.id == artifact.id).with_for_update()
        )
        if (
            job is None
            or stored_artifact is None
            or stored_artifact.status != "available"
            or stored_artifact.checksum != artifact.checksum
            or job.current_attempt_id != context.attempt.attempt_id
            or job.fence_token != context.attempt.fence_token
        ):
            raise MlPublicationError("artifact or attempt identity changed before activation")
        active = await session.scalar(
            select(MlActivePublication)
            .where(MlActivePublication.family == family)
            .with_for_update()
        )
        generation = active.generation if active is not None else 0
        if generation != expected_generation:
            return ActivationResult(
                activated=False,
                generation=generation,
                version=active.version if active is not None else version,
                checksum=active.checksum if active is not None else artifact.checksum,
            )
        next_generation = generation + 1
        if active is None:
            active = MlActivePublication(
                family=family,
                generation=next_generation,
                artifact_id=artifact.id,
                version=version,
                checksum=artifact.checksum,
                job_id=context.delivery.canonical_job_id,
                attempt_id=context.attempt.attempt_id,
                fence_token=context.attempt.fence_token,
            )
            session.add(active)
        else:
            active.generation = next_generation
            active.artifact_id = artifact.id
            active.version = version
            active.checksum = artifact.checksum
            active.job_id = context.delivery.canonical_job_id
            active.attempt_id = context.attempt.attempt_id
            active.fence_token = context.attempt.fence_token
        await job_event_writer.append(
            session,
            job_id=context.delivery.canonical_job_id,
            attempt_id=context.attempt.attempt_id,
            event_key="ml.publication.activated",
            state=job.phase,
            message="Immutable ML artifact activated",
            detail={
                "artifact_id": artifact.id,
                "family": family,
                "generation": next_generation,
                "version": version,
            },
            canonical_version=context.attempt.fence_token,
        )
        return ActivationResult(
            activated=True,
            generation=next_generation,
            version=version,
            checksum=artifact.checksum,
        )
