"""Transport-free canonical batch parents and fixed atomic creation."""

from __future__ import annotations

import re
import secrets
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import uuid4

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from marquee.core.configuration_cache import configuration_provider
from marquee.core.jobs.contracts import MigrationState, TriggerKind
from marquee.core.jobs.definitions import JobDefinitionError
from marquee.core.jobs.event_service import job_event_writer
from marquee.core.jobs.manifest import JOB_DEFINITION_REGISTRY
from marquee.core.jobs.subjects import AggregateBatchSnapshot
from marquee.core.jobs.submission import (
    Initiator,
    ParentBinding,
    SubjectLocator,
    SubmissionIntent,
    SubmissionInvariantError,
    SubmissionResult,
    SubmissionValidationError,
    _json_policy,
    _lock_idempotency,
    _prepare,
    _validate_idempotency_key,
    submit_jobs,
)
from marquee.models import Job, JobAttempt, JobBatch, JobDispatch

MAX_FIXED_CHILDREN = 500
MAX_DYNAMIC_CHILDREN = 500
MAX_BATCH_FAILURE_ITEMS = 20
_SCOPE_REFERENCE = re.compile(r"^[A-Za-z0-9._:-]+$")


@dataclass(frozen=True, slots=True)
class BatchScope:
    reference: str
    display_name: str
    summary: str | None = None

    def __post_init__(self) -> None:
        if (
            not self.reference
            or len(self.reference) > 64
            or not _SCOPE_REFERENCE.fullmatch(self.reference)
        ):
            raise SubmissionValidationError("batch scope reference is invalid")
        if not self.display_name or len(self.display_name) > 500:
            raise SubmissionValidationError("batch scope display name is invalid")
        if self.summary is not None and len(self.summary) > 500:
            raise SubmissionValidationError("batch scope summary is too long")


@dataclass(frozen=True, slots=True)
class FixedBatchResult:
    parent: SubmissionResult
    children: tuple[SubmissionResult, ...]
    sealed_child_total: int


@dataclass(frozen=True, slots=True)
class DynamicBatchResult:
    parent: SubmissionResult
    generation: int
    sealed: bool
    created_total: int


@dataclass(frozen=True, slots=True)
class BatchProjectionResult:
    parent_job_id: str
    projection_sequence: int
    changed: bool
    phase: str
    outcome: str | None
    terminal_total: int
    created_total: int


def _submission_result(job: Job, disposition: Literal["created", "reused"]) -> SubmissionResult:
    return SubmissionResult(
        job_id=job.id,
        disposition=disposition,
        phase=job.phase,
        snapshot_link=f"/api/jobs/{job.id}/snapshot",
        detail_link=f"/projection-room/jobs/{job.id}",
        activity_link=f"/projection-room?view=queue&job={job.id}",
        idempotent=disposition == "reused",
    )


async def _existing_fixed_batch(
    session: AsyncSession,
    *,
    parent: Job,
    request: dict,
    scope: BatchScope,
    trigger: TriggerKind,
    initiator: dict[str, str] | None,
    prepared_children: tuple,
) -> FixedBatchResult:
    child_total = len(prepared_children)
    projection = await session.get(JobBatch, parent.id)
    if (
        parent.request != request
        or parent.trigger_kind != trigger.value
        or parent.initiator != initiator
        or parent.subject_kind != "aggregate_batch"
        or parent.subject_reference != scope.reference
        or parent.subject_snapshot.get("display_name") != scope.display_name
        or parent.subject_snapshot.get("scope_summary") != scope.summary
        or projection is None
        or projection.mode != "fixed"
        or not projection.sealed
        or projection.sealed_child_total != child_total
        or projection.created_total != child_total
    ):
        raise SubmissionValidationError(
            "batch idempotency key belongs to different semantic intent"
        )
    parent_dispatches = await session.scalar(
        select(func.count(JobDispatch.id)).where(JobDispatch.job_id == parent.id)
    )
    parent_attempts = await session.scalar(
        select(func.count(JobAttempt.id)).where(JobAttempt.job_id == parent.id)
    )
    if (
        parent.pgq_job_id is not None
        or parent.dispatch_generation != 0
        or parent_dispatches
        or parent_attempts
    ):
        raise SubmissionInvariantError("canonical batch parent acquired transport state")
    children = tuple(
        (
            await session.scalars(
                select(Job)
                .where(Job.parent_id == parent.id)
                .order_by(Job.created_at, Job.id)
                .limit(MAX_FIXED_CHILDREN + 1)
            )
        ).all()
    )
    if len(children) != child_total:
        raise SubmissionInvariantError("fixed batch projection does not match its children")
    children_by_key = {child.idempotency_key: child for child in children}
    if len(children_by_key) != child_total:
        raise SubmissionInvariantError("fixed batch children have invalid idempotency identity")
    for prepared in prepared_children:
        child = children_by_key.get(prepared.intent.idempotency_key)
        locator = prepared.intent.subject
        if (
            child is None
            or child.type != prepared.intent.job_type
            or child.request != prepared.request
            or child.subject_kind != locator.kind
            or child.subject_reference != locator.reference
            or child.trigger_kind != TriggerKind.BATCH.value
            or child.initiator != prepared.initiator
            or child.parent_id != parent.id
            or child.root_id != parent.root_id
            or child.correlation_id != parent.correlation_id
            or child.pgq_job_id is None
        ):
            raise SubmissionValidationError(
                "batch idempotency key belongs to different child intent"
            )
    return FixedBatchResult(
        parent=_submission_result(parent, "reused"),
        children=tuple(_submission_result(child, "reused") for child in children),
        sealed_child_total=child_total,
    )


async def create_fixed_batch(
    session: AsyncSession,
    *,
    parent_job_type: str,
    parent_request: Mapping[str, object],
    scope: BatchScope,
    trigger: TriggerKind,
    initiator: Initiator | None,
    idempotency_key: str,
    children: Sequence[SubmissionIntent],
    priority: int | None = None,
) -> FixedBatchResult:
    """Create a sealed ticketless parent and every child in one caller transaction."""
    if not session.in_transaction():
        raise SubmissionValidationError("caller must own an active transaction")
    ordered_children = tuple(children)
    if len(ordered_children) > MAX_FIXED_CHILDREN:
        raise SubmissionValidationError(f"fixed batch exceeds the {MAX_FIXED_CHILDREN}-child cap")
    try:
        definition = JOB_DEFINITION_REGISTRY.get(parent_job_type)
    except JobDefinitionError as exc:
        raise SubmissionValidationError("batch parent definition is unavailable") from exc
    if (
        definition.migration_state != MigrationState.PARENT_ONLY
        or definition.parent_policy is None
        or not definition.parent_policy.fixed_children
    ):
        raise SubmissionValidationError("job type is not a fixed canonical batch parent")
    if trigger == TriggerKind.WEBHOOK or trigger not in definition.trigger_kinds:
        raise SubmissionValidationError("trigger is not enabled for this batch parent")
    _validate_idempotency_key(parent_job_type, idempotency_key)
    try:
        normalized_request = definition.request.validate(
            parent_request, version=definition.request.current_version
        ).model_dump(mode="json", exclude_none=True)
    except (TypeError, ValueError, ValidationError) as exc:
        raise SubmissionValidationError("batch parent request is invalid") from exc
    parent_initiator = initiator.as_document() if initiator else None
    now = datetime.now(UTC)
    parent_priority = definition.default_priority if priority is None else priority
    if not 0 <= parent_priority <= 100:
        raise SubmissionValidationError("batch parent priority must be between zero and 100")

    prepared_children = []
    for child in ordered_children:
        if child.parent is not None:
            raise SubmissionValidationError("fixed child must not carry a parent binding")
        if child.trigger != TriggerKind.BATCH or child.initiator != initiator:
            raise SubmissionValidationError("fixed child provenance is inconsistent")
        if child.job_type not in definition.child_job_types:
            raise SubmissionValidationError("fixed child type is outside the parent policy")
        prepared_children.append(_prepare(child, now=now))
    prepared_children_tuple = tuple(prepared_children)

    snapshot = AggregateBatchSnapshot(
        display_id=f"batch:{scope.reference}",
        display_name=scope.display_name,
        batch_type=parent_job_type,
        child_count=len(ordered_children),
        sealed=True,
        scope_summary=scope.summary,
    )
    subject_builder = definition.subject_builder
    if subject_builder is None:
        raise SubmissionInvariantError("batch parent definition has no subject builder")
    try:
        snapshot = subject_builder(snapshot)
        configuration = configuration_provider.snapshot_for(definition.configuration_keys)
    except (TypeError, ValueError, ValidationError) as exc:
        raise SubmissionValidationError("batch scope or configuration is invalid") from exc

    await _lock_idempotency(session, idempotency_key)
    existing = await session.scalar(select(Job).where(Job.idempotency_key == idempotency_key))
    if existing is not None:
        if existing.type != parent_job_type:
            raise SubmissionValidationError(
                "batch idempotency key belongs to different semantic intent"
            )
        return await _existing_fixed_batch(
            session,
            parent=existing,
            request=normalized_request,
            scope=scope,
            trigger=trigger,
            initiator=parent_initiator,
            prepared_children=prepared_children_tuple,
        )

    parent_id = uuid4().hex
    empty = not ordered_children
    eligible_at = now + timedelta(seconds=definition.default_eligibility_delay_seconds)
    parent = Job(
        id=parent_id,
        type=parent_job_type,
        payload_version=definition.request.current_version,
        result_version=definition.result.current_version,
        error_version=definition.error.current_version,
        request=normalized_request,
        plan={
            "version": 1,
            "parent_only": True,
            "effect_safety": definition.effect_safety.value,
            "presenter_key": definition.presenter_key,
            "progress_policy": _json_policy(definition.progress_policy),
            "action_policy": _json_policy(definition.action_policy),
        },
        phase="terminal" if empty else "queued",
        outcome="no_change" if empty else None,
        desired_state="run",
        dispatch_generation=0,
        priority=parent_priority,
        eligible_at=eligible_at,
        idempotency_key=idempotency_key,
        retry_policy=_json_policy(definition.retry_policy),
        execution_policy_id=None,
        configuration_version=configuration.version,
        configuration_snapshot=configuration.values,
        root_id=parent_id,
        correlation_id=parent_id,
        trigger_kind=trigger.value,
        initiator=parent_initiator,
        feature_area=definition.feature_area.value,
        presentation_family=definition.presentation_family,
        subject_kind="aggregate_batch",
        subject_reference=scope.reference,
        subject_snapshot=snapshot.model_dump(mode="json"),
        queued_at=None if empty else now,
        terminal_at=now if empty else None,
        attention={
            "level": "normal",
            "reason": "none",
            "message": "No matching work was found.",
        }
        if empty
        else None,
    )
    projection = JobBatch(
        parent_job_id=parent_id,
        mode="fixed",
        generation=1,
        sealed=True,
        sealed_at=now,
        sealed_child_total=len(ordered_children),
        created_total=len(ordered_children),
        terminal_total=0,
        projection_sequence=1,
    )
    session.add_all([parent, projection])
    await job_event_writer.append(
        session,
        job_id=parent_id,
        event_key="batch.sealed",
        state="terminal" if empty else "queued",
        message=(
            "Fixed batch sealed with no matching work"
            if empty
            else "Fixed batch sealed and children queued"
        ),
        detail={"mode": "fixed", "sealed_child_total": len(ordered_children)},
    )
    await session.flush()

    child_results: tuple[SubmissionResult, ...] = ()
    if ordered_children:
        binding = ParentBinding(
            parent_id=parent_id,
            root_id=parent_id,
            correlation_id=parent_id,
        )
        bound_children = tuple(replace(child, parent=binding) for child in ordered_children)
        child_results = await submit_jobs(session, intents=bound_children)
        child_jobs = [await session.get(Job, result.job_id) for result in child_results]
        if any(child is None for child in child_jobs) or any(
            child.configuration_version != parent.configuration_version
            for child in child_jobs
            if child is not None
        ):
            raise SubmissionInvariantError(
                "fixed parent and children have inconsistent configuration versions"
            )
    await session.flush()
    return FixedBatchResult(
        parent=_submission_result(parent, "created"),
        children=child_results,
        sealed_child_total=len(ordered_children),
    )


def _initiator_from_document(document: dict | None) -> Initiator | None:
    if document is None:
        return None
    return Initiator(
        kind=document["kind"],
        identifier=document.get("identifier"),
        display_name=document.get("display_name"),
    )


def _require_dynamic_definition(parent_job_type: str):
    try:
        definition = JOB_DEFINITION_REGISTRY.get(parent_job_type)
    except JobDefinitionError as exc:
        raise SubmissionValidationError("batch parent definition is unavailable") from exc
    if (
        definition.migration_state != MigrationState.PARENT_ONLY
        or definition.parent_policy is None
        or definition.parent_policy.fixed_children
    ):
        raise SubmissionValidationError("job type is not a dynamic canonical batch parent")
    return definition


async def open_dynamic_batch(
    session: AsyncSession,
    *,
    parent_job_type: str,
    parent_request: Mapping[str, object],
    scope: BatchScope,
    trigger: TriggerKind,
    initiator: Initiator | None,
    idempotency_key: str,
    priority: int | None = None,
) -> DynamicBatchResult:
    """Open one fenced, ticketless dynamic generation in the caller transaction."""
    if not session.in_transaction():
        raise SubmissionValidationError("caller must own an active transaction")
    definition = _require_dynamic_definition(parent_job_type)
    if trigger == TriggerKind.WEBHOOK or trigger not in definition.trigger_kinds:
        raise SubmissionValidationError("trigger is not enabled for this batch parent")
    _validate_idempotency_key(parent_job_type, idempotency_key)
    try:
        normalized_request = definition.request.validate(
            parent_request, version=definition.request.current_version
        ).model_dump(mode="json", exclude_none=True)
    except (TypeError, ValueError, ValidationError) as exc:
        raise SubmissionValidationError("batch parent request is invalid") from exc
    parent_initiator = initiator.as_document() if initiator else None
    parent_priority = definition.default_priority if priority is None else priority
    if not 0 <= parent_priority <= 100:
        raise SubmissionValidationError("batch parent priority must be between zero and 100")
    snapshot = AggregateBatchSnapshot(
        display_id=f"batch:{scope.reference}",
        display_name=scope.display_name,
        batch_type=parent_job_type,
        child_count=0,
        sealed=False,
        scope_summary=scope.summary,
    )
    if definition.subject_builder is None:
        raise SubmissionInvariantError("batch parent definition has no subject builder")
    try:
        snapshot = definition.subject_builder(snapshot)
        configuration = configuration_provider.snapshot_for(definition.configuration_keys)
    except (TypeError, ValueError, ValidationError) as exc:
        raise SubmissionValidationError("batch scope or configuration is invalid") from exc

    await _lock_idempotency(session, idempotency_key)
    existing = await session.scalar(select(Job).where(Job.idempotency_key == idempotency_key))
    if existing is not None:
        projection = await session.get(JobBatch, existing.id)
        if (
            existing.type != parent_job_type
            or existing.request != normalized_request
            or existing.trigger_kind != trigger.value
            or existing.initiator != parent_initiator
            or existing.subject_kind != "aggregate_batch"
            or existing.subject_reference != scope.reference
            or existing.subject_snapshot.get("display_name") != scope.display_name
            or existing.subject_snapshot.get("scope_summary") != scope.summary
            or projection is None
            or projection.mode != "dynamic"
            or existing.pgq_job_id is not None
            or existing.dispatch_generation != 0
        ):
            raise SubmissionValidationError(
                "batch idempotency key belongs to different semantic intent"
            )
        return DynamicBatchResult(
            parent=_submission_result(existing, "reused"),
            generation=projection.generation,
            sealed=projection.sealed,
            created_total=projection.created_total,
        )

    now = datetime.now(UTC)
    generation = secrets.randbits(63) or 1
    parent_id = uuid4().hex
    parent = Job(
        id=parent_id,
        type=parent_job_type,
        payload_version=definition.request.current_version,
        result_version=definition.result.current_version,
        error_version=definition.error.current_version,
        request=normalized_request,
        plan={
            "version": 1,
            "parent_only": True,
            "effect_safety": definition.effect_safety.value,
            "presenter_key": definition.presenter_key,
            "progress_policy": _json_policy(definition.progress_policy),
            "action_policy": _json_policy(definition.action_policy),
        },
        phase="queued",
        desired_state="run",
        dispatch_generation=0,
        priority=parent_priority,
        eligible_at=now + timedelta(seconds=definition.default_eligibility_delay_seconds),
        idempotency_key=idempotency_key,
        retry_policy=_json_policy(definition.retry_policy),
        configuration_version=configuration.version,
        configuration_snapshot=configuration.values,
        root_id=parent_id,
        correlation_id=parent_id,
        trigger_kind=trigger.value,
        initiator=parent_initiator,
        feature_area=definition.feature_area.value,
        presentation_family=definition.presentation_family,
        subject_kind="aggregate_batch",
        subject_reference=scope.reference,
        subject_snapshot=snapshot.model_dump(mode="json"),
        queued_at=now,
    )
    projection = JobBatch(
        parent_job_id=parent_id,
        mode="dynamic",
        generation=generation,
        sealed=False,
        created_total=0,
        terminal_total=0,
        projection_sequence=1,
    )
    session.add_all([parent, projection])
    await job_event_writer.append(
        session,
        job_id=parent_id,
        event_key="batch.opened",
        state="queued",
        message="Dynamic batch generation opened",
        detail={"generation": generation, "mode": "dynamic"},
    )
    await session.flush()
    return DynamicBatchResult(
        parent=_submission_result(parent, "created"),
        generation=generation,
        sealed=False,
        created_total=0,
    )


async def append_dynamic_child(
    session: AsyncSession,
    *,
    parent_id: str,
    generation: int,
    child: SubmissionIntent,
) -> SubmissionResult:
    """Append one ordinary canonical child under a row-locked open generation."""
    if not session.in_transaction():
        raise SubmissionValidationError("caller must own an active transaction")
    projection = await session.scalar(
        select(JobBatch).where(JobBatch.parent_job_id == parent_id).with_for_update()
    )
    parent = await session.scalar(select(Job).where(Job.id == parent_id).with_for_update())
    if projection is None or parent is None or projection.mode != "dynamic":
        raise SubmissionValidationError("dynamic batch parent is unavailable")
    if generation != projection.generation:
        raise SubmissionValidationError("dynamic batch generation is stale")
    if projection.sealed:
        raise SubmissionValidationError("dynamic batch is permanently sealed")
    definition = _require_dynamic_definition(parent.type)
    if child.parent is not None:
        raise SubmissionValidationError("dynamic child must not carry a parent binding")
    if (
        child.trigger != TriggerKind.BATCH
        or child.initiator != _initiator_from_document(parent.initiator)
        or child.job_type not in definition.child_job_types
    ):
        raise SubmissionValidationError("dynamic child provenance or policy is inconsistent")
    existing = await session.scalar(select(Job).where(Job.idempotency_key == child.idempotency_key))
    if existing is None and projection.created_total >= MAX_DYNAMIC_CHILDREN:
        raise SubmissionValidationError(
            f"dynamic batch exceeds the {MAX_DYNAMIC_CHILDREN}-child cap"
        )
    binding = ParentBinding(
        parent_id=parent.id,
        root_id=parent.root_id,
        correlation_id=parent.correlation_id or parent.id,
    )
    result = (await submit_jobs(session, intents=(replace(child, parent=binding),)))[0]
    child_job = await session.get(Job, result.job_id)
    if child_job is None or child_job.parent_id != parent.id:
        raise SubmissionInvariantError("dynamic child linkage is inconsistent")
    if child_job.configuration_version != parent.configuration_version:
        raise SubmissionValidationError("dynamic generation configuration changed before append")
    if result.disposition == "created":
        projection.created_total += 1
        projection.projection_sequence += 1
        projection.updated_at = datetime.now(UTC)
        snapshot = dict(parent.subject_snapshot)
        snapshot["child_count"] = projection.created_total
        parent.subject_snapshot = snapshot
        await job_event_writer.append(
            session,
            job_id=parent.id,
            event_key="batch.child_appended",
            state=parent.phase,
            message="Dynamic batch child queued",
            detail={
                "generation": generation,
                "child_job_id": child_job.id,
                "created_total": projection.created_total,
            },
        )
    await session.flush()
    return result


def _aggregate_outcome(counts: dict[str, int], *, parent_cancelled: bool) -> str:
    positive = counts["succeeded"] + counts["no_change"]
    failure = counts["failed"] + counts["dead_letter"] + counts["unsafe"]
    partial = counts["partially_succeeded"]
    cancelled = counts["cancelled"]
    superseded = counts["superseded"]
    if partial:
        return "partially_succeeded"
    if positive:
        if failure or cancelled or superseded:
            return "partially_succeeded"
        if counts["succeeded"]:
            return "succeeded"
        return "no_change"
    if failure:
        return "unsafe" if counts["unsafe"] else "failed"
    if cancelled and cancelled == sum(counts.values()) and parent_cancelled:
        return "cancelled"
    if superseded and superseded == sum(counts.values()):
        return "superseded"
    if cancelled:
        return "cancelled"
    return "no_change"


async def project_batch(
    session: AsyncSession,
    *,
    parent_id: str,
    expected_projection_sequence: int | None = None,
    repair: bool = False,
) -> BatchProjectionResult:
    """Recompute one bounded ticketless parent projection under its row lock."""
    projection = await session.scalar(
        select(JobBatch).where(JobBatch.parent_job_id == parent_id).with_for_update()
    )
    parent = await session.scalar(select(Job).where(Job.id == parent_id).with_for_update())
    if projection is None or parent is None:
        raise SubmissionValidationError("canonical batch parent is unavailable")
    if (
        expected_projection_sequence is not None
        and projection.projection_sequence != expected_projection_sequence
    ):
        raise SubmissionValidationError("batch projection changed before repair")
    definition = JOB_DEFINITION_REGISTRY.get(parent.type)
    children = tuple(
        (
            await session.scalars(
                select(Job)
                .where(Job.parent_id == parent.id)
                .order_by(Job.created_at, Job.id)
                .limit(MAX_DYNAMIC_CHILDREN + 1)
            )
        ).all()
    )
    if len(children) > MAX_DYNAMIC_CHILDREN or len(children) != projection.created_total:
        raise SubmissionInvariantError("batch projection does not match its bounded child set")
    if any(child.type not in definition.child_job_types for child in children):
        raise SubmissionInvariantError("batch contains a child outside its registered policy")
    outcome_names = (
        "succeeded",
        "partially_succeeded",
        "no_change",
        "failed",
        "cancelled",
        "superseded",
        "dead_letter",
        "unsafe",
    )
    counts = {name: sum(child.outcome == name for child in children) for name in outcome_names}
    terminal_total = sum(counts.values())
    if any(child.phase == "terminal" and child.outcome is None for child in children):
        raise SubmissionInvariantError("terminal batch child has no canonical outcome")
    now = datetime.now(UTC)
    terminal = projection.sealed and terminal_total == projection.created_total
    if terminal:
        outcome = (
            "no_change"
            if projection.created_total == 0
            else _aggregate_outcome(counts, parent_cancelled=parent.desired_state == "cancel")
        )
        phase = "terminal"
    elif parent.desired_state == "cancel":
        outcome = None
        phase = "stopping"
    elif any(child.phase in {"running", "stopping", "terminal"} for child in children):
        outcome = None
        phase = "running"
    else:
        outcome = None
        phase = "queued"
    failures = [
        {"job_id": child.id, "outcome": child.outcome}
        for child in children
        if child.outcome in {"failed", "partially_succeeded", "dead_letter", "unsafe"}
    ][:MAX_BATCH_FAILURE_ITEMS]
    failure_summary = (
        {
            "items": failures,
            "truncated": len(failures)
            < counts["failed"]
            + counts["partially_succeeded"]
            + counts["dead_letter"]
            + counts["unsafe"],
        }
        if failures
        else None
    )
    attention_summary = (
        {
            "level": "error" if counts["unsafe"] else "warning",
            "reason": "unsafe" if counts["unsafe"] else "failed",
            "message": "Some batch children require attention.",
        }
        if failures
        else None
    )
    previous = (
        projection.terminal_total,
        *(getattr(projection, f"{name}_total") for name in outcome_names),
        projection.failure_summary,
        projection.attention_summary,
        parent.phase,
        parent.outcome,
        parent.attention,
    )
    current = (
        terminal_total,
        *(counts[name] for name in outcome_names),
        failure_summary,
        attention_summary,
        phase,
        outcome,
        attention_summary,
    )
    changed = previous != current
    if changed:
        projection.terminal_total = terminal_total
        for name in outcome_names:
            setattr(projection, f"{name}_total", counts[name])
        projection.failure_summary = failure_summary
        projection.attention_summary = attention_summary
        projection.projection_sequence += 1
        projection.updated_at = now
        parent.phase = phase
        parent.outcome = outcome
        parent.attention = attention_summary
        if phase in {"running", "stopping", "terminal"} and parent.started_at is None:
            parent.started_at = now
        parent.terminal_at = now if terminal else None
        parent.stopping_at = (
            now if phase == "stopping" and parent.stopping_at is None else parent.stopping_at
        )
        await job_event_writer.append(
            session,
            job_id=parent.id,
            event_key="batch.repaired" if repair else "batch.projected",
            state=outcome or phase,
            message="Batch projection repaired" if repair else "Batch projection advanced",
            detail={
                "projection_sequence": projection.projection_sequence,
                "created_total": projection.created_total,
                "terminal_total": terminal_total,
                "phase": phase,
                "outcome": outcome,
            },
        )
    await session.flush()
    return BatchProjectionResult(
        parent_job_id=parent.id,
        projection_sequence=projection.projection_sequence,
        changed=changed,
        phase=parent.phase,
        outcome=parent.outcome,
        terminal_total=projection.terminal_total,
        created_total=projection.created_total,
    )


async def seal_dynamic_batch(
    session: AsyncSession, *, parent_id: str, generation: int
) -> BatchProjectionResult:
    """Permanently seal the exact open generation and aggregate its current state."""
    if not session.in_transaction():
        raise SubmissionValidationError("caller must own an active transaction")
    projection = await session.scalar(
        select(JobBatch).where(JobBatch.parent_job_id == parent_id).with_for_update()
    )
    parent = await session.scalar(select(Job).where(Job.id == parent_id).with_for_update())
    if projection is None or parent is None or projection.mode != "dynamic":
        raise SubmissionValidationError("dynamic batch parent is unavailable")
    if generation != projection.generation:
        raise SubmissionValidationError("dynamic batch generation is stale")
    if not projection.sealed:
        now = datetime.now(UTC)
        projection.sealed = True
        projection.sealed_at = now
        projection.sealed_child_total = projection.created_total
        projection.projection_sequence += 1
        projection.updated_at = now
        snapshot = dict(parent.subject_snapshot)
        snapshot["sealed"] = True
        snapshot["child_count"] = projection.created_total
        parent.subject_snapshot = snapshot
        await job_event_writer.append(
            session,
            job_id=parent.id,
            event_key="batch.sealed",
            state=parent.phase,
            message="Dynamic batch generation permanently sealed",
            detail={
                "generation": generation,
                "sealed_child_total": projection.created_total,
            },
        )
    return await project_batch(session, parent_id=parent.id)


async def project_active_child(session: AsyncSession, child: Job) -> None:
    """Advance a direct canonical parent when a child starts, stops, or terminates."""
    if child.phase not in {"running", "stopping", "terminal"} or child.parent_id is None:
        return
    projection = await session.get(JobBatch, child.parent_id)
    if projection is not None:
        await project_batch(session, parent_id=child.parent_id)


async def project_terminal_child(session: AsyncSession, child: Job) -> None:
    """Advance a direct canonical parent in the same first-terminal transaction."""
    if child.phase == "terminal":
        await project_active_child(session, child)


async def cancel_batch_descendants(
    session: AsyncSession, *, parent: Job, page_size: int = 100
) -> BatchProjectionResult:
    """Cancel only direct nonterminal descendants in bounded primary-key pages."""
    if page_size < 1 or page_size > 100:
        raise SubmissionValidationError("batch cancellation page size is invalid")
    projection = await session.scalar(
        select(JobBatch).where(JobBatch.parent_job_id == parent.id).with_for_update()
    )
    if projection is None:
        raise SubmissionValidationError("canonical batch projection is unavailable")
    parent.desired_state = "cancel"
    now = datetime.now(UTC)
    if not projection.sealed:
        projection.sealed = True
        projection.sealed_at = now
        projection.sealed_child_total = projection.created_total
        projection.projection_sequence += 1
        snapshot = dict(parent.subject_snapshot)
        snapshot["sealed"] = True
        snapshot["child_count"] = projection.created_total
        parent.subject_snapshot = snapshot
        await job_event_writer.append(
            session,
            job_id=parent.id,
            event_key="batch.sealed",
            state=parent.phase,
            message="Dynamic batch sealed by parent cancellation",
            detail={
                "generation": projection.generation,
                "sealed_child_total": projection.created_total,
            },
        )
    from marquee.core.jobs.pgqueuer_gateway import pgqueuer_gateway

    cursor = ""
    while True:
        page = tuple(
            (
                await session.scalars(
                    select(Job)
                    .where(
                        Job.parent_id == parent.id,
                        Job.phase != "terminal",
                        Job.id > cursor,
                    )
                    .order_by(Job.id)
                    .limit(page_size)
                )
            ).all()
        )
        if not page:
            break
        for child in page:
            await pgqueuer_gateway.cancel_known_ticket(session, job_id=child.id)
        cursor = page[-1].id
    return await project_batch(session, parent_id=parent.id)


async def reprioritize_batch_descendants(
    session: AsyncSession,
    *,
    parent: Job,
    priority: int,
    page_size: int = 100,
) -> int:
    """Apply priority only to direct still-queued children in bounded pages."""
    if page_size < 1 or page_size > 100:
        raise SubmissionValidationError("batch priority page size is invalid")
    projection = await session.scalar(
        select(JobBatch).where(JobBatch.parent_job_id == parent.id).with_for_update()
    )
    if projection is None:
        raise SubmissionValidationError("canonical batch projection is unavailable")
    from marquee.core.jobs.pgqueuer_gateway import pgqueuer_gateway

    changed = 0
    cursor = ""
    while True:
        page = tuple(
            (
                await session.scalars(
                    select(Job)
                    .where(
                        Job.parent_id == parent.id,
                        Job.phase == "queued",
                        Job.id > cursor,
                    )
                    .order_by(Job.id)
                    .limit(page_size)
                )
            ).all()
        )
        if not page:
            break
        for child in page:
            await pgqueuer_gateway.reprioritize_known_ticket(
                session, job_id=child.id, priority=priority
            )
            changed += 1
        cursor = page[-1].id
    parent.priority = priority
    await job_event_writer.append(
        session,
        job_id=parent.id,
        event_key="job.priority_changed",
        state=parent.phase,
        message="Batch child priority changed within execution classes",
        detail={"priority": priority, "queued_children": changed},
    )
    await session.flush()
    return changed


async def retry_batch(
    session: AsyncSession, *, original: Job, expected_fence_token: int
) -> SubmissionResult:
    """Create a new canonical parent generation with definition-owned child selection."""
    projection = await session.scalar(
        select(JobBatch).where(JobBatch.parent_job_id == original.id).with_for_update()
    )
    if projection is None or original.phase != "terminal":
        raise SubmissionValidationError("only a terminal canonical batch can be retried")
    definition = JOB_DEFINITION_REGISTRY.get(original.type)
    if definition.parent_policy is None:
        raise SubmissionValidationError("batch retry policy is unavailable")
    all_children = tuple(
        (
            await session.scalars(
                select(Job)
                .where(Job.parent_id == original.id)
                .order_by(Job.created_at, Job.id)
                .limit(MAX_DYNAMIC_CHILDREN + 1)
            )
        ).all()
    )
    if len(all_children) > MAX_DYNAMIC_CHILDREN:
        raise SubmissionInvariantError("batch retry child set exceeds its bound")
    if definition.parent_policy.retry_children == "all":
        selected = all_children
    else:
        selected = tuple(
            child
            for child in all_children
            if child.outcome
            in {"failed", "partially_succeeded", "cancelled", "dead_letter", "unsafe"}
        )
    initiator = _initiator_from_document(original.initiator)
    intents = tuple(
        SubmissionIntent(
            job_type=child.type,
            request=child.request,
            subject=SubjectLocator(
                kind=child.subject_kind,
                reference=child.subject_reference or child.id,
            ),
            trigger=TriggerKind.BATCH,
            initiator=initiator,
            idempotency_key=(
                f"{child.type}:retry-{original.id[:12]}-{child.id[:12]}-{expected_fence_token}"
            ),
            priority=child.priority,
        )
        for child in selected
    )
    scope = BatchScope(
        reference=original.subject_reference or original.id,
        display_name=original.subject_snapshot.get("display_name", "Retried batch"),
        summary=original.subject_snapshot.get("scope_summary"),
    )
    retry_key = f"{original.type}:retry-{original.id}-{expected_fence_token}"
    trigger = TriggerKind(original.trigger_kind)
    if projection.mode == "fixed":
        created = await create_fixed_batch(
            session,
            parent_job_type=original.type,
            parent_request=original.request,
            scope=scope,
            trigger=trigger,
            initiator=initiator,
            idempotency_key=retry_key,
            children=intents,
            priority=original.priority,
        )
        result = created.parent
        child_results = created.children
    else:
        opened = await open_dynamic_batch(
            session,
            parent_job_type=original.type,
            parent_request=original.request,
            scope=scope,
            trigger=trigger,
            initiator=initiator,
            idempotency_key=retry_key,
            priority=original.priority,
        )
        child_results_list = []
        for intent in intents:
            child_results_list.append(
                await append_dynamic_child(
                    session,
                    parent_id=opened.parent.job_id,
                    generation=opened.generation,
                    child=intent,
                )
            )
        await seal_dynamic_batch(
            session, parent_id=opened.parent.job_id, generation=opened.generation
        )
        result = opened.parent
        child_results = tuple(child_results_list)
    replacement = await session.get(Job, result.job_id)
    if replacement is None:
        raise SubmissionInvariantError("batch retry successor disappeared")
    replacement.retry_of_job_id = original.id
    for source, child_result in zip(selected, child_results, strict=True):
        successor = await session.get(Job, child_result.job_id)
        if successor is None:
            raise SubmissionInvariantError("batch retry child successor disappeared")
        successor.retry_of_job_id = source.id
    await job_event_writer.append(
        session,
        job_id=replacement.id,
        event_key="job.retried",
        state=replacement.outcome or replacement.phase,
        message="Batch retry successor created",
        detail={"original_job_id": original.id, "selected_children": len(selected)},
    )
    await session.flush()
    return result
