"""Typed caller-transaction authority for canonical job submission."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from uuid import uuid4

from pydantic import ValidationError
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from marquee.core.configuration_cache import ExecutionConfigurationSnapshot, configuration_provider
from marquee.core.jobs.contracts import TriggerKind
from marquee.core.jobs.definitions import ActiveOverlapMode, JobDefinition, JobDefinitionError
from marquee.core.jobs.event_service import job_event_writer
from marquee.core.jobs.manifest import JOB_DEFINITION_REGISTRY
from marquee.core.jobs.pgqueuer_gateway import (
    MAX_BULK_ENQUEUE,
    MAX_DEFER,
    MAX_PRIORITY,
    MIN_PRIORITY,
    EnqueueIntent,
    PgQueuerInvariantError,
    pgqueuer_gateway,
)
from marquee.core.jobs.subjects import (
    MaintenanceScopeSnapshot,
    ModelProfileTrainingSnapshot,
    PosterCandidateSetSnapshot,
    SubjectNotFoundError,
    SubjectSnapshot,
    SystemWorkSnapshot,
    build_episode_snapshot,
    build_media_file_snapshot,
    build_movie_snapshot,
    build_season_snapshot,
    build_series_snapshot,
)
from marquee.models.job import Job, JobDispatch

_IDENTITY = re.compile(r"^[a-z][a-z0-9_.-]*$")
_IDEMPOTENCY = re.compile(r"^[a-z][a-z0-9_]{0,79}:[A-Za-z0-9._:-]{1,160}$")
_FORBIDDEN_INITIATOR_PARTS = ("password", "path", "secret", "token")
MAX_BULK_SUBMISSIONS = MAX_BULK_ENQUEUE


class SubmissionError(ValueError):
    """Safe typed failure returned by the canonical producer boundary."""

    code = "submission_invalid"


class SubmissionValidationError(SubmissionError):
    code = "submission_validation_failed"


class IdempotencyConflictError(SubmissionError):
    code = "idempotency_conflict"

    @property
    def api_detail(self) -> str | dict[str, str]:
        return self.code


class ActiveOverlapConflictError(IdempotencyConflictError):
    code = "active_overlap_conflict"

    def __init__(self, active_job_id: str) -> None:
        super().__init__(self.code)
        self.active_job_id = active_job_id
        self.snapshot_link = f"/api/jobs/{active_job_id}/snapshot"
        self.detail_link = f"/projection-room/jobs/{active_job_id}"

    @property
    def api_detail(self) -> dict[str, str]:
        return {
            "code": self.code,
            "job_id": self.active_job_id,
            "snapshot_url": self.snapshot_link,
            "detail_url": self.detail_link,
        }


class SubmissionInvariantError(SubmissionError):
    code = "submission_invariant_failed"


@dataclass(frozen=True, slots=True)
class SubjectLocator:
    """Stable live-domain lookup identity; never a caller-supplied snapshot."""

    kind: str
    reference: str

    def __post_init__(self) -> None:
        if not _IDENTITY.fullmatch(self.kind) or len(self.kind) > 40:
            raise SubmissionValidationError("subject locator kind is invalid")
        if not self.reference or len(self.reference) > 64:
            raise SubmissionValidationError("subject locator reference is invalid")


@dataclass(frozen=True, slots=True)
class Initiator:
    """Bounded, sanitized provenance preserved until authentication arrives."""

    kind: str
    identifier: str | None = None
    display_name: str | None = None

    def __post_init__(self) -> None:
        if not _IDENTITY.fullmatch(self.kind) or len(self.kind) > 40:
            raise SubmissionValidationError("initiator kind is invalid")
        for value in (self.identifier, self.display_name):
            if value is not None and (not value or len(value) > 200):
                raise SubmissionValidationError("initiator identity is invalid")
        document = self.as_document()
        if any(
            part in key.lower()
            for key in document
            for part in _FORBIDDEN_INITIATOR_PARTS
        ):
            raise SubmissionValidationError("initiator provenance is not safe")

    def as_document(self) -> dict[str, str]:
        return {
            key: value
            for key, value in {
                "kind": self.kind,
                "identifier": self.identifier,
                "display_name": self.display_name,
            }.items()
            if value is not None
        }


@dataclass(frozen=True, slots=True)
class ParentBinding:
    parent_id: str
    root_id: str | None = None
    correlation_id: str | None = None

    def __post_init__(self) -> None:
        if not self.parent_id or len(self.parent_id) > 32:
            raise SubmissionValidationError("parent binding is invalid")
        if self.root_id is not None and (not self.root_id or len(self.root_id) > 32):
            raise SubmissionValidationError("parent root binding is invalid")
        if self.correlation_id is not None and (
            not self.correlation_id or len(self.correlation_id) > 64
        ):
            raise SubmissionValidationError("parent correlation binding is invalid")


@dataclass(frozen=True, slots=True)
class SubmissionIntent:
    job_type: str
    request: Mapping[str, object]
    subject: SubjectLocator
    trigger: TriggerKind
    initiator: Initiator | None
    idempotency_key: str
    priority: int | None = None
    eligible_at: datetime | None = None
    parent: ParentBinding | None = None


@dataclass(frozen=True, slots=True)
class SubmissionResult:
    job_id: str
    disposition: Literal["created", "reused"]
    phase: str
    snapshot_link: str
    detail_link: str
    activity_link: str
    idempotent: bool


@dataclass(frozen=True, slots=True)
class _PreparedSubmission:
    intent: SubmissionIntent
    definition: JobDefinition
    request: dict[str, Any]
    priority: int
    eligible_at: datetime
    initiator: dict[str, str] | None


def _result(job: Job, disposition: Literal["created", "reused"]) -> SubmissionResult:
    return SubmissionResult(
        job_id=job.id,
        disposition=disposition,
        phase=job.phase,
        snapshot_link=f"/api/jobs/{job.id}/snapshot",
        detail_link=f"/projection-room/jobs/{job.id}",
        activity_link=f"/projection-room?view=queue&job={job.id}",
        idempotent=disposition == "reused",
    )


def _json_policy(value: Any) -> dict[str, Any]:
    return json.loads(json.dumps(asdict(value), allow_nan=False, separators=(",", ":")))


def _validate_idempotency_key(
    job_type: str, value: str, *, trigger: TriggerKind | None = None
) -> str:
    prefix = "schedule:" if trigger == TriggerKind.SCHEDULE else f"{job_type}:"
    if (
        not _IDEMPOTENCY.fullmatch(value)
        or len(value) > 200
        or not value.startswith(prefix)
    ):
        raise SubmissionValidationError("idempotency key is invalid for this job type")
    return value


def _prepare(intent: SubmissionIntent, *, now: datetime) -> _PreparedSubmission:
    try:
        definition = JOB_DEFINITION_REGISTRY.get(intent.job_type)
        definition = JOB_DEFINITION_REGISTRY.for_dispatch(
            intent.job_type, entrypoint=definition.entrypoint
        )
    except JobDefinitionError as exc:
        raise SubmissionValidationError("job type is not enabled for submission") from exc
    if intent.trigger == TriggerKind.WEBHOOK:
        raise SubmissionValidationError("webhook submission is reserved")
    if intent.trigger not in definition.trigger_kinds:
        raise SubmissionValidationError("trigger is not enabled for this job type")
    _validate_idempotency_key(
        intent.job_type, intent.idempotency_key, trigger=intent.trigger
    )
    try:
        normalized = definition.request.validate(
            intent.request, version=definition.request.current_version
        ).model_dump(mode="json", exclude_none=True)
    except (TypeError, ValueError, ValidationError) as exc:
        raise SubmissionValidationError("job request is invalid") from exc
    priority = definition.default_priority if intent.priority is None else intent.priority
    if not MIN_PRIORITY <= priority <= MAX_PRIORITY:
        raise SubmissionValidationError(
            f"priority must be between {MIN_PRIORITY} and {MAX_PRIORITY}"
        )
    eligible_at = intent.eligible_at or now + timedelta(
        seconds=definition.default_eligibility_delay_seconds
    )
    if eligible_at.tzinfo is None or eligible_at.utcoffset() is None:
        raise SubmissionValidationError("eligible_at must be timezone-aware")
    eligible_at = eligible_at.astimezone(UTC)
    if eligible_at - now > MAX_DEFER:
        raise SubmissionValidationError("eligible_at cannot be deferred more than 365 days")
    return _PreparedSubmission(
        intent=intent,
        definition=definition,
        request=normalized,
        priority=priority,
        eligible_at=eligible_at,
        initiator=intent.initiator.as_document() if intent.initiator else None,
    )


async def _lock_idempotency(session: AsyncSession, key: str) -> None:
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": key},
    )

def _active_overlap_scope(
    prepared: _PreparedSubmission,
    *,
    parent_id: str | None,
    correlation_id: str,
) -> dict[str, str | None]:
    policy = prepared.definition.overlap_policy
    return {
        "job_type": prepared.intent.job_type,
        "subject_kind": prepared.intent.subject.kind,
        "subject_reference": prepared.intent.subject.reference,
        "parent_id": parent_id if policy.include_parent_scope else None,
        "correlation_id": (
            correlation_id
            if policy.include_parent_scope and parent_id is not None
            else None
        ),
    }


async def _resolve_active_overlap(
    session: AsyncSession,
    prepared: _PreparedSubmission,
    configuration: ExecutionConfigurationSnapshot,
    *,
    parent_id: str | None,
    correlation_id: str,
) -> Job | None:
    if prepared.definition.overlap_policy.mode == ActiveOverlapMode.ALLOW:
        return None
    scope = _active_overlap_scope(
        prepared,
        parent_id=parent_id,
        correlation_id=correlation_id,
    )
    lock_key = json.dumps(scope, sort_keys=True, separators=(",", ":"))
    await _lock_idempotency(session, f"active:{lock_key}")

    query = select(Job).where(
        Job.type == prepared.intent.job_type,
        Job.subject_kind == prepared.intent.subject.kind,
        Job.subject_reference == prepared.intent.subject.reference,
        Job.phase.in_(("planned", "queued", "running", "stopping")),
    )
    if prepared.definition.overlap_policy.include_parent_scope and parent_id is not None:
        query = query.where(
            Job.parent_id == parent_id,
            Job.correlation_id == correlation_id,
        )
    active = list(
        (
            await session.scalars(
                query.order_by(Job.created_at.asc(), Job.id.asc()).with_for_update()
            )
        ).all()
    )
    equivalent = next(
        (
            job
            for job in active
            if job.request == prepared.request
            and (
                not prepared.definition.overlap_policy.include_configuration
                or job.configuration_version == configuration.version
            )
        ),
        None,
    )
    if equivalent is not None:
        return equivalent
    if (
        active
        and prepared.definition.overlap_policy.mode == ActiveOverlapMode.REJECT_CONFLICT
    ):
        raise ActiveOverlapConflictError(active[0].id)
    return None


async def _resolve_subject(
    session: AsyncSession, locator: SubjectLocator
) -> SubjectSnapshot:
    try:
        if locator.kind == "system_work":
            if locator.reference != "system_noop":
                raise SubjectNotFoundError("system work is not registered")
            return SystemWorkSnapshot(
                display_id="system:noop",
                display_name="System no-op",
                work="system_noop",
            )
        if locator.kind == "maintenance_scope":
            scopes = {
                "library-sync": (
                    "maintenance:library-sync",
                    "Library synchronization",
                    "radarr and sonarr",
                ),
                "backup-create": (
                    "maintenance:backup-create",
                    "Create system backup",
                    "database and application data",
                ),
                "poster-maintenance": (
                    "maintenance:poster-maintenance",
                    "Poster maintenance",
                    "orphaned poster cache entries",
                ),
                "pipeline-cache": (
                    "maintenance:pipeline-cache",
                    "Pipeline cache cleanup",
                    "unreferenced pipeline cache entries",
                ),
                "job-retention": (
                    "maintenance:job-retention",
                    "Job history retention",
                    "expired canonical job evidence",
                ),
                "system-metrics": (
                    "maintenance:system-metrics",
                    "System metrics retention",
                    "expired system metrics samples",
                ),
            }
            display_id, display_name, scope = scopes[locator.reference]
            return MaintenanceScopeSnapshot(
                display_id=display_id,
                display_name=display_name,
                scope=scope,
            )
        if locator.kind == "model_profile_training":
            family, library = locator.reference.split(":", 1)
            if family not in {
                "taste_profile",
                "taste_map",
                "ranking_residual",
            }:
                raise ValueError
            if library not in {"movies", "tv"}:
                raise ValueError
            labels = {
                "taste_profile": "Taste profile",
                "taste_map": "Taste map",
                "ranking_residual": "Bounded ranking residual",
            }
            return ModelProfileTrainingSnapshot(
                display_id=f"ml:{family}:{library}",
                display_name=f"{labels[family]} ({library})",
                subject_type="training",
                name=labels[family],
                model_name=family,
                profile_scope=library,
                dataset_label=f"{library} library snapshot",
            )
        if locator.kind == "poster_candidate_set" and locator.reference == "all":
            return PosterCandidateSetSnapshot(
                display_id="posters:all",
                display_name="All poster subjects",
                media_kind="movie",
                subject_id=0,
                title="All poster subjects",
                source_names=("filesystem",),
                artwork_key="all",
            )
        identifier = int(locator.reference)
        if identifier < 1:
            raise ValueError
        if locator.kind == "movie":
            return await build_movie_snapshot(session, identifier)
        if locator.kind == "series":
            return await build_series_snapshot(session, identifier)
        if locator.kind == "season":
            return await build_season_snapshot(session, identifier)
        if locator.kind == "episode":
            return await build_episode_snapshot(session, identifier)
        if locator.kind == "media_file":
            return await build_media_file_snapshot(session, identifier)
    except (KeyError, TypeError, ValueError, SubjectNotFoundError) as exc:
        raise SubmissionValidationError("job subject could not be resolved") from exc
    raise SubmissionValidationError("job subject kind is not available for submission")


async def _parent_scope(
    session: AsyncSession,
    prepared: _PreparedSubmission,
) -> tuple[str | None, str, str]:
    binding = prepared.intent.parent
    if binding is None:
        job_id = uuid4().hex
        return None, job_id, job_id
    if prepared.intent.trigger not in {TriggerKind.BATCH, TriggerKind.PARENT}:
        raise SubmissionValidationError("child submission requires batch or parent provenance")
    parent = await session.get(Job, binding.parent_id)
    if parent is None:
        raise SubmissionValidationError("canonical parent does not exist")
    try:
        parent_definition = JOB_DEFINITION_REGISTRY.get(parent.type)
    except JobDefinitionError as exc:
        raise SubmissionInvariantError("canonical parent definition is unavailable") from exc
    if (
        parent_definition.parent_policy is None
        or prepared.intent.job_type not in parent_definition.child_job_types
    ):
        raise SubmissionValidationError("child type is outside the parent policy")
    root_id = parent.root_id
    correlation_id = parent.correlation_id or parent.root_id
    if binding.root_id is not None and binding.root_id != root_id:
        raise SubmissionValidationError("parent root binding is stale")
    if binding.correlation_id is not None and binding.correlation_id != correlation_id:
        raise SubmissionValidationError("parent correlation binding is stale")
    return parent.id, root_id, correlation_id


def _validate_existing(
    existing: Job,
    prepared: _PreparedSubmission,
    *,
    parent_id: str | None,
    root_id: str,
    correlation_id: str,
) -> None:
    locator = prepared.intent.subject
    if (
        existing.type != prepared.intent.job_type
        or existing.payload_version != prepared.definition.request.current_version
        or existing.request != prepared.request
        or existing.subject_kind != locator.kind
        or existing.subject_reference != locator.reference
        or existing.trigger_kind != prepared.intent.trigger.value
        or existing.initiator != prepared.initiator
        or existing.parent_id != parent_id
        or existing.root_id != root_id
        or existing.correlation_id != correlation_id
    ):
        raise IdempotencyConflictError(
            "idempotency key already belongs to different semantic intent"
        )
    if existing.dispatch_generation < 1 or existing.pgq_job_id is None:
        raise SubmissionInvariantError("existing canonical job has no completed dispatch")


async def _create_rows(
    session: AsyncSession,
    prepared: _PreparedSubmission,
    configuration: ExecutionConfigurationSnapshot,
    *,
    parent_id: str | None,
    root_id: str,
    correlation_id: str,
    now: datetime,
) -> tuple[Job, EnqueueIntent]:
    try:
        subject = await _resolve_subject(session, prepared.intent.subject)
        subject_builder = prepared.definition.subject_builder
        if subject_builder is None:
            raise SubmissionInvariantError("enabled definition has no subject builder")
        subject = subject_builder(subject)
    except SubmissionError:
        raise
    except (TypeError, ValueError, ValidationError) as exc:
        raise SubmissionValidationError("job subject or configuration is invalid") from exc
    snapshot = subject.model_dump(mode="json")
    if snapshot.get("kind") != prepared.intent.subject.kind:
        raise SubmissionInvariantError("resolved subject kind does not match its locator")

    job_id = root_id if parent_id is None else uuid4().hex
    generation = 1
    dedupe_key = f"marquee:{job_id}:{generation}"
    delay = max(prepared.eligible_at - now, timedelta(0))
    definition = prepared.definition
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
            "progress_policy": _json_policy(definition.progress_policy),
            "action_policy": _json_policy(definition.action_policy),
            "overlap_policy": _json_policy(definition.overlap_policy),
        },
        phase="queued",
        desired_state="run",
        dispatch_generation=generation,
        priority=prepared.priority,
        eligible_at=prepared.eligible_at,
        idempotency_key=prepared.intent.idempotency_key,
        retry_policy=_json_policy(definition.retry_policy),
        execution_policy_id=f"definition:{definition.job_type}:v1",
        configuration_version=configuration.version,
        configuration_snapshot=configuration.values,
        parent_id=parent_id,
        root_id=root_id,
        correlation_id=correlation_id,
        trigger_kind=prepared.intent.trigger.value,
        initiator=prepared.initiator,
        feature_area=definition.feature_area.value,
        presentation_family=definition.presentation_family,
        subject_kind=prepared.intent.subject.kind,
        subject_reference=prepared.intent.subject.reference,
        subject_snapshot=snapshot,
        queued_at=now,
    )
    dispatch = JobDispatch(
        job_id=job_id,
        generation=generation,
        entrypoint=definition.entrypoint,
        dedupe_key=dedupe_key,
        priority=prepared.priority,
        eligible_at=prepared.eligible_at,
        disposition="active",
    )
    session.add_all([job, dispatch])
    await job_event_writer.append(
        session,
        job_id=job_id,
        event_key="job.queued",
        state="queued",
        message=f"{definition.job_type} queued",
        detail={"dispatch_generation": generation, "entrypoint": definition.entrypoint},
    )
    return job, EnqueueIntent(
        job_id=job_id,
        entrypoint=definition.entrypoint,
        payload_version=definition.request.current_version,
        dispatch_generation=generation,
        priority=prepared.priority,
        execute_after=delay,
        dedupe_key=dedupe_key,
    )


async def submit_job(
    session: AsyncSession,
    *,
    job_type: str,
    request: Mapping[str, object],
    subject: SubjectLocator,
    trigger: TriggerKind,
    initiator: Initiator | None,
    idempotency_key: str,
    priority: int | None = None,
    eligible_at: datetime | None = None,
    parent: ParentBinding | None = None,
) -> SubmissionResult:
    """Create or reuse one canonical job without ending the caller transaction."""
    if not session.in_transaction():
        raise SubmissionValidationError("caller must own an active transaction")
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
            eligible_at=eligible_at,
            parent=parent,
        ),
        now=now,
    )
    await _lock_idempotency(session, idempotency_key)
    parent_id, root_id, correlation_id = await _parent_scope(session, prepared)
    existing = await session.scalar(select(Job).where(Job.idempotency_key == idempotency_key))
    if existing is not None:
        _validate_existing(
            existing,
            prepared,
            parent_id=parent_id,
            root_id=existing.root_id if parent_id is None else root_id,
            correlation_id=existing.correlation_id if parent_id is None else correlation_id,
        )
        return _result(existing, "reused")

    configuration = configuration_provider.snapshot_for(
        prepared.definition.configuration_keys
    )
    active = await _resolve_active_overlap(
        session,
        prepared,
        configuration,
        parent_id=parent_id,
        correlation_id=correlation_id,
    )
    if active is not None:
        return _result(active, "reused")

    job, enqueue_intent = await _create_rows(
        session,
        prepared,
        configuration,
        parent_id=parent_id,
        root_id=root_id,
        correlation_id=correlation_id,
        now=now,
    )
    try:
        await pgqueuer_gateway.enqueue(
            session,
            job_id=enqueue_intent.job_id,
            entrypoint=enqueue_intent.entrypoint,
            payload_version=enqueue_intent.payload_version,
            dispatch_generation=enqueue_intent.dispatch_generation,
            priority=enqueue_intent.priority,
            execute_after=enqueue_intent.execute_after,
            dedupe_key=enqueue_intent.dedupe_key,
        )
    except PgQueuerInvariantError as exc:
        raise SubmissionInvariantError("canonical transport dispatch could not be linked") from exc
    return _result(job, "created")


async def submit_jobs(
    session: AsyncSession,
    *,
    intents: Sequence[SubmissionIntent],
    allow_empty: bool = False,
) -> tuple[SubmissionResult, ...]:
    """Create one ordered, common-parent child set and bulk-link new tickets."""
    if not session.in_transaction():
        raise SubmissionValidationError("caller must own an active transaction")
    ordered = tuple(intents)
    if not ordered and allow_empty:
        return ()
    if not 1 <= len(ordered) <= MAX_BULK_SUBMISSIONS:
        raise SubmissionValidationError(
            f"bulk submission requires 1..{MAX_BULK_SUBMISSIONS} child intents"
        )
    keys = [intent.idempotency_key for intent in ordered]
    if len(set(keys)) != len(keys):
        raise SubmissionValidationError("bulk submission contains duplicate idempotency keys")
    if any(intent.parent is None for intent in ordered):
        raise SubmissionValidationError("bulk submission requires one common parent binding")
    bindings = {intent.parent for intent in ordered}
    triggers = {intent.trigger for intent in ordered}
    initiators = {intent.initiator for intent in ordered}
    if len(bindings) != 1 or len(triggers) != 1 or len(initiators) != 1:
        raise SubmissionValidationError("bulk submission provenance is inconsistent")

    now = datetime.now(UTC)
    prepared_items = tuple(_prepare(intent, now=now) for intent in ordered)
    for key in sorted(keys):
        await _lock_idempotency(session, key)

    jobs: list[Job] = []
    dispositions: list[Literal["created", "reused"]] = []
    enqueue_intents: list[EnqueueIntent] = []
    for prepared in prepared_items:
        parent_id, root_id, correlation_id = await _parent_scope(session, prepared)
        existing = await session.scalar(
            select(Job).where(Job.idempotency_key == prepared.intent.idempotency_key)
        )
        if existing is not None:
            _validate_existing(
                existing,
                prepared,
                parent_id=parent_id,
                root_id=root_id,
                correlation_id=correlation_id,
            )
            jobs.append(existing)
            dispositions.append("reused")
            continue
        configuration = configuration_provider.snapshot_for(
            prepared.definition.configuration_keys
        )
        active = await _resolve_active_overlap(
            session,
            prepared,
            configuration,
            parent_id=parent_id,
            correlation_id=correlation_id,
        )
        if active is not None:
            jobs.append(active)
            dispositions.append("reused")
            continue
        job, enqueue_intent = await _create_rows(
            session,
            prepared,
            configuration,
            parent_id=parent_id,
            root_id=root_id,
            correlation_id=correlation_id,
            now=now,
        )
        jobs.append(job)
        dispositions.append("created")
        enqueue_intents.append(enqueue_intent)

    if len({job.root_id for job in jobs}) != 1 or len(
        {job.correlation_id for job in jobs}
    ) != 1:
        raise SubmissionValidationError("bulk submission hierarchy is inconsistent")
    if len({job.configuration_version for job in jobs}) != 1:
        raise SubmissionValidationError("bulk submission configuration is inconsistent")
    if enqueue_intents:
        try:
            await pgqueuer_gateway.enqueue_many(session, intents=enqueue_intents)
        except PgQueuerInvariantError as exc:
            raise SubmissionInvariantError(
                "canonical bulk transport dispatch could not be linked"
            ) from exc
    return tuple(
        _result(job, disposition)
        for job, disposition in zip(jobs, dispositions, strict=True)
    )
