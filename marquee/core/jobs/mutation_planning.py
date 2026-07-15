"""Canonical planned/confirmed mutation submission (JMC5B B02/B03).

A destructive mutation is created as a real canonical job in ``phase='planned'``
with **no** PgQueuer ticket and ``dispatch_generation = 0``.  The immutable
request, before snapshot, expected target, source signature, confirmation
requirements, and expiry are stored on the strict 1:1 ``MediaOperationDetail``.

``confirm_mutation`` locks the job and its detail, revalidates the plan against
current reality, records confirmation provenance, and transactionally creates the
first dispatch exactly once.  Confirmation never rewrites the requested
operation: the stored request is replayed verbatim.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from uuid import uuid4

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from marquee.core.configuration_cache import configuration_provider
from marquee.core.jobs.contracts import TriggerKind
from marquee.core.jobs.event_service import job_event_writer
from marquee.core.jobs.mutation_documents import MutationTargetV1
from marquee.core.jobs.pgqueuer_gateway import PgQueuerInvariantError, pgqueuer_gateway
from marquee.core.jobs.submission import (
    Initiator,
    SubjectLocator,
    SubmissionError,
    SubmissionIntent,
    SubmissionInvariantError,
    SubmissionResult,
    SubmissionValidationError,
    _lock_idempotency,
    _prepare,
    _resolve_subject,
    _result,
)
from marquee.models.job import Job, JobDispatch
from marquee.models.job_evidence import MediaOperationDetail

#: Plans expire so a stale approval can never publish against drifted media.
DEFAULT_PLAN_TTL = timedelta(minutes=15)
MAX_PLAN_TTL = timedelta(hours=24)

PlanConflictReason = Literal[
    "already_dispatched",
    "expired",
    "missing",
    "not_planned",
    "signature_changed",
    "stale_plan",
    "subject_retired",
    "terminal",
]


class MutationPlanError(SubmissionError):
    """Base class for planned/confirmed mutation failures."""


class PlanValidationError(MutationPlanError):
    """The plan request itself is unusable."""


class PlanConflictError(MutationPlanError):
    """Confirmation cannot proceed against the current canonical state."""

    def __init__(self, reason: PlanConflictReason, message: str) -> None:
        super().__init__(message)
        self.reason: PlanConflictReason = reason


@dataclass(frozen=True, slots=True)
class MutationPlan:
    """Immutable plan documents recorded when the planned job is created."""

    operation_kind: str
    media_file_id: int | None
    media_snapshot: Mapping[str, Any]
    before_targets: tuple[MutationTargetV1, ...]
    requested_targets: tuple[MutationTargetV1, ...]
    expected_targets: tuple[MutationTargetV1, ...]
    input_signature: str
    confirmation_requirements: Mapping[str, Any] | None = None
    ttl: timedelta = DEFAULT_PLAN_TTL


def plan_version(detail: MediaOperationDetail) -> str:
    """Stable content fingerprint of the immutable plan documents."""
    material = json.dumps(
        {
            "operation_kind": detail.operation_kind,
            "media_snapshot": detail.media_snapshot,
            "target_snapshot": detail.target_snapshot,
            "requested_target": detail.requested_target,
            "expected_target": detail.expected_target,
            "input_signature": detail.input_signature,
        },
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _dump_targets(targets: tuple[MutationTargetV1, ...]) -> list[dict[str, Any]]:
    return [target.model_dump(mode="json") for target in targets]


async def plan_mutation(
    session: AsyncSession,
    *,
    job_type: str,
    request: Mapping[str, object],
    subject: SubjectLocator,
    initiator: Initiator | None,
    idempotency_key: str,
    plan: MutationPlan,
    trigger: TriggerKind = TriggerKind.MANUAL,
    priority: int | None = None,
) -> SubmissionResult:
    """Create one canonical planned job with no transport ticket."""
    if not session.in_transaction():
        raise PlanValidationError("caller must own an active transaction")
    if not plan.requested_targets:
        raise PlanValidationError("a mutation plan must request at least one target")
    if plan.ttl <= timedelta(0) or plan.ttl > MAX_PLAN_TTL:
        raise PlanValidationError("plan ttl must be positive and at most 24 hours")
    if not plan.input_signature:
        raise PlanValidationError("a mutation plan requires a source signature")

    now = datetime.now(UTC)
    prepared = _prepare(
        SubmissionIntent(
            job_type=job_type,
            request=request,
            subject=subject,
            trigger=trigger,
            initiator=initiator,
            idempotency_key=idempotency_key,
            priority=priority,
            eligible_at=None,
            parent=None,
        ),
        now=now,
    )
    definition = prepared.definition
    if definition.parent_policy is not None:
        raise PlanValidationError("parent-only definitions are not planned mutations")

    await _lock_idempotency(session, idempotency_key)
    existing = await session.scalar(select(Job).where(Job.idempotency_key == idempotency_key))
    if existing is not None:
        # A repeated compatible plan is idempotent; a changed request is a conflict.
        if existing.type != prepared.intent.job_type or existing.request != prepared.request:
            raise PlanConflictError(
                "stale_plan", "an incompatible plan already exists for this idempotency key"
            )
        return _result(existing, "reused")

    try:
        resolved = await _resolve_subject(session, prepared.intent.subject)
        builder = definition.subject_builder
        if builder is None:
            raise SubmissionInvariantError("enabled definition has no subject builder")
        snapshot = builder(resolved).model_dump(mode="json")
    except SubmissionError:
        raise
    except (TypeError, ValueError, ValidationError) as exc:
        raise PlanValidationError("job subject is invalid") from exc

    configuration = configuration_provider.snapshot_for(definition.configuration_keys)
    job_id = uuid4().hex
    job = Job(
        id=job_id,
        type=prepared.intent.job_type,
        payload_version=definition.request.current_version,
        result_version=definition.result.current_version,
        error_version=definition.error.current_version,
        request=prepared.request,
        plan={
            "version": 1,
            "entrypoint": definition.entrypoint,
            "execution_class": definition.execution_class.value,
            "timeout_seconds": definition.timeout.seconds,
            "effect_safety": definition.effect_safety.value,
            "presenter_key": definition.presenter_key,
            "requires_confirmation": True,
        },
        phase="planned",
        desired_state="run",
        dispatch_generation=0,
        priority=prepared.priority,
        eligible_at=prepared.eligible_at,
        idempotency_key=prepared.intent.idempotency_key,
        retry_policy=None,
        execution_policy_id=f"definition:{definition.job_type}:v1",
        configuration_version=configuration.version,
        configuration_snapshot=configuration.values,
        root_id=job_id,
        correlation_id=job_id,
        trigger_kind=prepared.intent.trigger.value,
        initiator=prepared.initiator,
        feature_area=definition.feature_area.value,
        presentation_family=definition.presentation_family,
        subject_kind=prepared.intent.subject.kind,
        subject_reference=prepared.intent.subject.reference,
        subject_snapshot=snapshot,
        planned_at=now,
    )
    detail = MediaOperationDetail(
        job_id=job_id,
        operation_kind=plan.operation_kind,
        media_file_id=plan.media_file_id,
        media_snapshot=dict(plan.media_snapshot),
        target_snapshot={"before": _dump_targets(plan.before_targets)},
        input_signature=plan.input_signature,
        plan_expires_at=now + plan.ttl,
        requested_target={"targets": _dump_targets(plan.requested_targets)},
        expected_target={"targets": _dump_targets(plan.expected_targets)},
    )
    session.add_all([job, detail])
    await session.flush()
    await job_event_writer.append(
        session,
        job_id=job_id,
        event_key="job.planned",
        state="planned",
        message=f"{definition.job_type} planned",
        detail={
            "requested_targets": len(plan.requested_targets),
            "plan_version": plan_version(detail),
            "expires_at": detail.plan_expires_at.isoformat(),
            "requirements": dict(plan.confirmation_requirements or {}),
        },
    )
    return _result(job, "created")


async def confirm_mutation(
    session: AsyncSession,
    *,
    job_id: str,
    expected_plan_version: str,
    current_input_signature: str,
    confirmed_by: Initiator | None,
    expected_configuration_version: int | None = None,
) -> SubmissionResult:
    """Validate a plan and create its first dispatch exactly once."""
    if not session.in_transaction():
        raise PlanValidationError("caller must own an active transaction")
    now = datetime.now(UTC)
    job = await session.scalar(select(Job).where(Job.id == job_id).with_for_update())
    if job is None:
        raise PlanConflictError("missing", "the planned job no longer exists")
    detail = await session.scalar(
        select(MediaOperationDetail)
        .where(MediaOperationDetail.job_id == job_id)
        .with_for_update()
    )
    if detail is None:
        raise PlanConflictError("missing", "the planned job has no mutation evidence")

    if job.phase == "terminal":
        raise PlanConflictError("terminal", "the plan is already terminal")
    if job.phase != "planned":
        # Already confirmed: idempotent replay of a compatible confirmation.
        if job.dispatch_generation >= 1 and job.desired_state == "run":
            return _result(job, "reused")
        raise PlanConflictError("not_planned", "the job is not awaiting confirmation")
    if job.desired_state == "cancel":
        raise PlanConflictError("terminal", "the plan was cancelled")
    if job.dispatch_generation != 0:
        raise PlanConflictError("already_dispatched", "the plan already has a dispatch")
    if detail.plan_expires_at is not None and detail.plan_expires_at <= now:
        raise PlanConflictError("expired", "the plan has expired and must be recreated")

    actual_plan_version = plan_version(detail)
    if expected_plan_version != actual_plan_version:
        raise PlanConflictError("stale_plan", "the plan changed since it was presented")
    if current_input_signature != detail.input_signature:
        raise PlanConflictError("signature_changed", "the source media changed since planning")
    if (
        expected_configuration_version is not None
        and expected_configuration_version != job.configuration_version
    ):
        raise PlanConflictError("stale_plan", "the effective configuration changed since planning")

    definition = _definition_for(job)
    generation = 1
    dedupe_key = f"marquee:{job.id}:{generation}"
    job.phase = "queued"
    job.dispatch_generation = generation
    job.queued_at = now
    job.retry_policy = _retry_policy_document(definition)
    detail.confirmation = {
        "confirmed_at": now.isoformat(),
        "confirmed_by": confirmed_by.as_document() if confirmed_by else None,
        "plan_version": actual_plan_version,
        "input_signature": detail.input_signature,
        "configuration_version": job.configuration_version,
    }
    session.add(
        JobDispatch(
            job_id=job.id,
            generation=generation,
            entrypoint=definition.entrypoint,
            dedupe_key=dedupe_key,
            priority=job.priority,
            eligible_at=job.eligible_at,
            disposition="active",
        )
    )
    await job_event_writer.append(
        session,
        job_id=job.id,
        event_key="job.queued",
        state="queued",
        message=f"{job.type} confirmed",
        detail={"dispatch_generation": generation, "entrypoint": definition.entrypoint},
    )
    try:
        await pgqueuer_gateway.enqueue(
            session,
            job_id=job.id,
            entrypoint=definition.entrypoint,
            payload_version=job.payload_version,
            dispatch_generation=generation,
            priority=job.priority,
            execute_after=max(job.eligible_at - now, timedelta(0)),
            dedupe_key=dedupe_key,
        )
    except PgQueuerInvariantError as exc:
        raise SubmissionInvariantError("canonical transport dispatch could not be linked") from exc
    return _result(job, "created")


def _definition_for(job: Job):
    from marquee.core.jobs.definitions import JobDefinitionError
    from marquee.core.jobs.manifest import JOB_DEFINITION_REGISTRY

    try:
        definition = JOB_DEFINITION_REGISTRY.get(job.type)
        return JOB_DEFINITION_REGISTRY.for_dispatch(job.type, entrypoint=definition.entrypoint)
    except JobDefinitionError as exc:
        raise SubmissionValidationError("job type is not enabled for confirmation") from exc


def _retry_policy_document(definition) -> dict[str, Any]:
    from marquee.core.jobs.submission import _json_policy

    return _json_policy(definition.retry_policy)
