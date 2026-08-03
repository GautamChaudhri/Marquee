"""Canonical bounded job read APIs and the narrow JMC2 control surface.

Queue/History lists, compact snapshots, curated presentations, and
cursor-paginated evidence replace the former unbounded job detail and per-job
polling SSE.  All list/detail responses use explicit response models; cursors
are opaque and bound to the exact view/filter/sort contract that issued them.
PgQueuer identifiers and rows remain private diagnostics.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import Callable, Sequence
from dataclasses import replace
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Select, Text, and_, case, cast, func, or_, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from marquee.core.filesystem import FilesystemBoundaryError
from marquee.core.jobs.api_errors import (
    ERROR_INVALID_CURSOR,
    ERROR_INVALID_FILTER,
    ERROR_JOB_NOT_FOUND,
    JobApiErrorDetail,
)
from marquee.core.jobs.artifact_service import (
    ArtifactError,
    ArtifactMissingError,
    materialize_virtual_artifact,
    verify_physical_artifact,
)
from marquee.core.jobs.contracts import (
    AttentionLevel,
    FeatureArea,
    JobAction,
    MigrationState,
    TriggerKind,
)
from marquee.core.jobs.control import (
    JobControlError,
    JobControlResult,
)
from marquee.core.jobs.control import (
    cancel as control_cancel,
)
from marquee.core.jobs.control import (
    change_priority as control_change_priority,
)
from marquee.core.jobs.control import (
    retry as control_retry,
)
from marquee.core.jobs.control import (
    set_paused as control_set_paused,
)
from marquee.core.jobs.definitions import (
    ActivityPolicy,
    ActivityVisibility,
    ContainedWorkSource,
    JobDefinition,
)
from marquee.core.jobs.event_stream import EventClient, JobEventFrame, job_event_tailer
from marquee.core.jobs.labels import humanize_job_type
from marquee.core.jobs.log_capture import (
    LOG_LEVELS,
    LOG_SOURCES,
    AttemptLogError,
    AttemptLogFiles,
    AttemptLogLine,
)
from marquee.core.jobs.manifest import JOB_DEFINITION_REGISTRY
from marquee.core.jobs.pagination import (
    InvalidCursorError,
    cursor_contract,
    decode_cursor,
    encode_cursor,
)
from marquee.core.jobs.presentation import (
    CompactProgress,
    JobPresentation,
    JobRow,
    PresentationAttention,
    PresentationStatus,
    RowLinks,
)
from marquee.core.jobs.presenters import GENERIC_PRESENTER, load_context, resolve_presenter
from marquee.core.jobs.presenters.base import (
    BatchProgressProjection,
    PresentationIntegrityError,
    present_actions,
    present_attention,
    present_compact_progress,
    present_contained_work,
    present_status,
)
from marquee.core.jobs.work_item_documents import (
    ContainedWorkSummary,
    WorkItemPage,
    WorkItemProgress,
    WorkItemRow,
    WorkItemSummary,
)
from marquee.core.jobs.work_items import historical_work_item_fallback, work_item_summary
from marquee.core.runtime_settings import effective_settings as settings
from marquee.database import _get_session_factory, get_db
from marquee.models import (
    Episode,
    Job,
    JobArtifact,
    JobAttempt,
    JobBatch,
    JobEvent,
    JobLog,
    JobWorkItem,
    MediaFile,
    MediaOperationDetail,
    Movie,
    PipelineRun,
    Season,
    Series,
)

router = APIRouter(prefix="/api/jobs", tags=["jobs"])
logger = logging.getLogger(__name__)

DEFAULT_LIMIT = 50
MAX_LIMIT = 200
QUEUE_RANK_LIMIT = 1000


def _activity_visibility_predicate():
    child = aliased(Job)
    parent = aliased(Job)
    hidden: list[Any] = []
    for definition in JOB_DEFINITION_REGISTRY:
        policy = definition.activity_policy
        if policy.visibility == ActivityVisibility.PROMOTE_CHILDREN:
            hidden.append(
                and_(
                    Job.type == definition.job_type,
                    select(child.id)
                    .where(
                        child.parent_id == Job.id,
                        child.type.in_(policy.promoted_child_types),
                    )
                    .exists(),
                )
            )
        for child_type in policy.hidden_child_types:
            hidden.append(
                and_(
                    Job.type == child_type,
                    select(parent.id)
                    .where(parent.id == Job.parent_id, parent.type == definition.job_type)
                    .exists(),
                )
            )
    return ~or_(*hidden) if hidden else True


async def _batch_projections_for_jobs(
    db: AsyncSession, jobs: list[Job]
) -> dict[str, JobBatch | BatchProgressProjection]:
    """Batch-load stored coordinator projections and bounded historical fallbacks."""
    candidates = {
        job.id: (_definition_for(job.type), job)
        for job in jobs
        if _definition_for(job.type).activity_policy.contained_work
        == ContainedWorkSource.CHILD_JOBS
    }
    if not candidates:
        return {}

    stored = list(
        (
            await db.scalars(select(JobBatch).where(JobBatch.parent_job_id.in_(tuple(candidates))))
        ).all()
    )
    projections: dict[str, JobBatch | BatchProgressProjection] = {
        batch.parent_job_id: batch for batch in stored
    }
    missing = {job_id: value for job_id, value in candidates.items() if job_id not in projections}
    if not missing:
        return projections

    child_predicates = [
        and_(Job.parent_id == job_id, Job.type.in_(definition.activity_policy.hidden_child_types))
        for job_id, (definition, _job) in missing.items()
        if definition.activity_policy.hidden_child_types
    ]
    if not child_predicates:
        return projections
    count = func.count(Job.id)
    rows = (
        await db.execute(
            select(
                Job.parent_id,
                count.label("created_total"),
                count.filter(Job.phase == "terminal").label("terminal_total"),
                count.filter(Job.outcome == "succeeded").label("succeeded_total"),
                count.filter(Job.outcome == "partially_succeeded").label(
                    "partially_succeeded_total"
                ),
                count.filter(Job.outcome == "no_change").label("no_change_total"),
                count.filter(Job.outcome == "failed").label("failed_total"),
                count.filter(Job.outcome == "cancelled").label("cancelled_total"),
                count.filter(Job.outcome == "superseded").label("superseded_total"),
                count.filter(Job.outcome == "dead_letter").label("dead_letter_total"),
                count.filter(Job.outcome == "unsafe").label("unsafe_total"),
                func.coalesce(func.max(Job.progress_sequence), 0).label("projection_sequence"),
                func.max(Job.updated_at).label("updated_at"),
            )
            .where(or_(*child_predicates))
            .group_by(Job.parent_id)
        )
    ).all()
    for row in rows:
        parent = missing[row.parent_id][1]
        projections[row.parent_id] = BatchProgressProjection(
            created_total=row.created_total,
            terminal_total=row.terminal_total,
            succeeded_total=row.succeeded_total,
            partially_succeeded_total=row.partially_succeeded_total,
            no_change_total=row.no_change_total,
            failed_total=row.failed_total,
            cancelled_total=row.cancelled_total,
            superseded_total=row.superseded_total,
            dead_letter_total=row.dead_letter_total,
            unsafe_total=row.unsafe_total,
            projection_sequence=row.projection_sequence,
            updated_at=row.updated_at or parent.updated_at,
        )
    return projections


def _activity_related_match(predicate: Callable[[Any], Any]):
    parent = aliased(Job)
    child = aliased(Job)
    return or_(
        predicate(Job),
        select(parent.id).where(parent.id == Job.parent_id, predicate(parent)).exists(),
        select(child.id).where(child.parent_id == Job.id, predicate(child)).exists(),
    )


def _evidence_job_predicate(
    *, job: Job, definition: JobDefinition, scope: Literal["self", "contained"], column: Any
):
    policy = definition.activity_policy
    child_types = policy.hidden_child_types
    if not child_types and policy.contained_work == ContainedWorkSource.CHILD_JOBS:
        child_types = definition.child_job_types
    if scope == "self" or not child_types:
        return column == job.id
    child = aliased(Job)
    descendants = select(child.id).where(child.parent_id == job.id)
    descendants = descendants.where(child.type.in_(child_types))
    return or_(column == job.id, column.in_(descendants))


async def _origin_subjects(db: AsyncSession, job_ids: set[str]) -> dict[str, dict[str, Any]]:
    if not job_ids:
        return {}
    rows = (await db.execute(select(Job.id, Job.subject_snapshot).where(Job.id.in_(job_ids)))).all()
    return {job_id: snapshot if isinstance(snapshot, dict) else {} for job_id, snapshot in rows}


def _member_subject_contains(model, fragment: dict[str, Any]):
    return cast(model.subject_snapshot, JSONB).contains({"members": [{"subject": fragment}]})


def _activity_subject_kind_match(subject_kind: str):
    return _activity_related_match(
        lambda model: or_(
            model.subject_kind == subject_kind,
            _member_subject_contains(model, {"kind": subject_kind}),
        )
    )


def _activity_subject_reference_match(value: str):
    fragments: list[dict[str, Any]] = [{"display_id": value}]
    if value.isdecimal():
        identifier = int(value)
        fragments.extend({key: identifier} for key in ("movie_id", "series_id", "season_id"))
    return _activity_related_match(
        lambda model: or_(
            model.subject_reference == value,
            *(_member_subject_contains(model, fragment) for fragment in fragments),
        )
    )


def _activity_type_match(values: Sequence[str]):
    requested = tuple(values)
    related = _activity_related_match(lambda model: model.type.in_(requested))
    if "poster_pipeline" in requested:
        return or_(related, Job.type == "poster_pipeline_group")
    return related


_QUEUE_PHASES = ("planned", "queued", "running", "stopping")
_QUEUE_SORTS = ("default",)
_HISTORY_SORTS = ("default", "created", "-created")
_RAW_KINDS = ("request", "plan", "result", "error")


def _error(status_code: int, code: str, message: str, **fields) -> HTTPException:
    detail = JobApiErrorDetail(code=code, message=message, **fields)
    return HTTPException(status_code=status_code, detail=detail.model_dump(mode="json"))


def _definition_for(job_type: str) -> JobDefinition:
    definition = JOB_DEFINITION_REGISTRY.find(job_type)
    if definition is not None:
        return definition
    base = JOB_DEFINITION_REGISTRY.find("system_noop")
    if base is None:  # pragma: no cover - manifest coverage guarantees the fallback source
        raise RuntimeError("system_noop definition is required for historical presentation")
    return replace(
        base,
        job_type=job_type,
        label_key="jobs.unknown_historical",
        presentation_family="unknown_historical",
        presenter_key=GENERIC_PRESENTER.key,
        enabled=False,
        migration_state=MigrationState.DEFINED_DISABLED,
        disabled_reason="Retained historical job type is no longer registered.",
        activity_policy=ActivityPolicy(feature_label="Other", monogram="?"),
    )


def _presenter_for(definition: JobDefinition):
    if definition.presenter_key == GENERIC_PRESENTER.key:
        return GENERIC_PRESENTER
    return resolve_presenter(definition)


async def _load_job(db: AsyncSession, job_id: str) -> Job:
    job = await db.get(Job, job_id)
    if job is None:
        raise _error(404, ERROR_JOB_NOT_FOUND, "Job not found", job_id=job_id)
    return job


# ---------------------------------------------------------------------------
# Command-response summary reused by feature enqueue routes.
# ---------------------------------------------------------------------------


def job_summary(job: Job, *, subject_title: str | None = None) -> dict:
    """Compact command-response summary with canonical links."""
    snapshot = job.subject_snapshot if isinstance(job.subject_snapshot, dict) else {}
    title = subject_title or snapshot.get("display_name") or snapshot.get("title")
    subject = None
    if job.subject_kind or job.subject_reference:
        subject = {
            "type": job.subject_kind,
            "id": job.subject_reference,
            "title": title,
            "snapshot": snapshot,
        }
    return {
        "job_id": job.id,
        "type": job.type,
        "label": humanize_job_type(job.type),
        "phase": job.phase,
        "outcome": job.outcome,
        "status": job.outcome if job.phase == "terminal" and job.outcome else job.phase,
        "desired_state": job.desired_state,
        "priority": job.priority,
        "parent_id": job.parent_id,
        "root_id": job.root_id,
        "correlation_id": job.correlation_id,
        "retry_of_job_id": job.retry_of_job_id,
        "subject": subject,
        "stage": job.current_stage,
        "current_subject": job.current_subject,
        "progress": job.progress,
        "progress_sequence": job.progress_sequence,
        "attention": job.attention,
        "configuration_version": job.configuration_version,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "planned_at": job.planned_at.isoformat() if job.planned_at else None,
        "queued_at": job.queued_at.isoformat() if job.queued_at else None,
        "eligible_at": job.eligible_at.isoformat() if job.eligible_at else None,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "stopping_at": job.stopping_at.isoformat() if job.stopping_at else None,
        "terminal_at": job.terminal_at.isoformat() if job.terminal_at else None,
        "status_url": f"/api/jobs/{job.id}/snapshot",
        "presentation_url": f"/api/jobs/{job.id}/presentation",
        "detail_url": f"/projection-room/jobs/{job.id}",
    }


# ---------------------------------------------------------------------------
# Queue / History list
# ---------------------------------------------------------------------------


class JobListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    view: Literal["queue", "history"]
    items: list[JobRow]
    next_cursor: str | None
    limit: int


class ActivityAttentionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    running: int
    waiting_held: int
    retrying: int
    needs_attention: int
    warning: int
    error: int
    highest_severity: AttentionLevel


_SEVERITY = case(
    (Job.attention.op("->>")("level") == "error", 2),
    (Job.attention.op("->>")("level") == "warning", 1),
    else_=0,
)
_RUNNING_FIRST = case((Job.phase.in_(("running", "stopping")), 0), else_=1)


def _queue_order() -> list[tuple[Any, str]]:
    return [
        (_SEVERITY, "desc"),
        (_RUNNING_FIRST, "asc"),
        (Job.priority, "desc"),
        (Job.eligible_at, "asc"),
        (Job.created_at, "asc"),
        (Job.id, "asc"),
    ]


def _history_order(sort: str) -> list[tuple[Any, str]]:
    if sort == "created":
        return [(Job.created_at, "asc"), (Job.id, "asc")]
    if sort == "-created":
        return [(Job.created_at, "desc"), (Job.id, "desc")]
    return [(Job.terminal_at, "desc"), (Job.id, "desc")]


def _apply_order(query: Select, order: list[tuple[Any, str]]) -> Select:
    return query.order_by(
        *[expr.desc() if direction == "desc" else expr.asc() for expr, direction in order]
    )


def _keyset_filter(order: list[tuple[Any, str]], values: tuple) -> Any:
    if len(values) != len(order):
        raise InvalidCursorError("cursor key does not match the list contract")
    clauses = []
    for index, (expr, direction) in enumerate(order):
        equalities = [order[j][0] == values[j] for j in range(index)]
        bound = expr > values[index] if direction == "asc" else expr < values[index]
        clauses.append(and_(*equalities, bound) if equalities else bound)
    return or_(*clauses)


def _cursor_value(value: Any) -> str | int | float | None:
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    return value


def _parse_cursor_datetime(value: Any, field: str) -> datetime:
    if not isinstance(value, str):
        raise InvalidCursorError(f"cursor field {field} is malformed")
    try:
        return datetime.fromisoformat(value)
    except ValueError as exc:
        raise InvalidCursorError(f"cursor field {field} is malformed") from exc


def _event_cursor(value: str | None) -> tuple[int | None, bool]:
    if value is None:
        return None, False
    try:
        parsed = int(value, 10)
    except ValueError:
        return None, True
    return (parsed, False) if parsed >= 0 else (None, True)


def _sse_frame(client: EventClient, frame: JobEventFrame) -> str:
    del client
    event_id = f"id: {frame.cursor}\n" if frame.cursor > 0 else ""
    return f"{event_id}event: {frame.event_key}\ndata: {frame.model_dump_json()}\n\n"


@router.get(
    "/events/stream",
    response_model=JobEventFrame,
    response_class=StreamingResponse,
    responses={
        200: {
            "content": {
                "text/event-stream": {"schema": {"$ref": "#/components/schemas/JobEventFrame"}}
            }
        }
    },
)
async def stream_job_events(
    request: Request,
    after: Annotated[str | None, Query(max_length=20)] = None,
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
):
    """Replay the global durable cursor, then follow the API-instance tailer."""
    header_cursor, header_invalid = _event_cursor(last_event_id)
    query_cursor, query_invalid = _event_cursor(after)
    if (
        last_event_id is not None
        and after is not None
        and not header_invalid
        and not query_invalid
        and header_cursor != query_cursor
    ):
        raise HTTPException(status_code=400, detail="Last-Event-ID and after disagree")
    requested = header_cursor if last_event_id is not None else query_cursor
    invalid = header_invalid if last_event_id is not None else query_invalid
    if job_event_tailer.health()["status"] != "ok":
        raise HTTPException(status_code=503, detail="Job event stream is unavailable")
    client = await job_event_tailer.subscribe(requested, invalid_cursor=invalid)

    async def frames():
        try:
            while True:
                try:
                    frame = await asyncio.wait_for(
                        client.queue.get(), timeout=settings.JOB_EVENT_KEEPALIVE_SECONDS
                    )
                except TimeoutError:
                    if client.closed or await request.is_disconnected():
                        return
                    yield ": keepalive\n\n"
                    continue
                yield _sse_frame(client, frame)
                if frame.event_key == "stream.reset_required":
                    return
        finally:
            await job_event_tailer.unsubscribe(client)

    return StreamingResponse(
        frames(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("", response_model=JobListResponse)
async def list_jobs(
    db: Annotated[AsyncSession, Depends(get_db)],
    view: Literal["queue", "history"] = "queue",
    hierarchy: Literal["all", "activity"] = "all",
    cursor: str | None = None,
    limit: int = Query(DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
    sort: str = "default",
    q: Annotated[str | None, Query(max_length=100)] = None,
    feature_area: FeatureArea | None = None,
    type: str | None = None,
    types: Annotated[list[str] | None, Query()] = None,
    subject_kind: str | None = None,
    subject_id: Annotated[str | None, Query(max_length=64)] = None,
    subject_reference: Annotated[list[str] | None, Query()] = None,
    phase: str | None = None,
    outcome: str | None = None,
    attention: AttentionLevel | None = None,
    trigger: TriggerKind | None = None,
    parent_id: Annotated[str | None, Query(max_length=32)] = None,
    root_id: Annotated[str | None, Query(max_length=32)] = None,
    correlation_id: Annotated[str | None, Query(max_length=64)] = None,
    worker_id: Annotated[str | None, Query(max_length=100)] = None,
    execution_class: Annotated[str | None, Query(max_length=80)] = None,
    created_after: int | None = None,
    created_before: int | None = None,
):
    allowed_sorts = _QUEUE_SORTS if view == "queue" else _HISTORY_SORTS
    if sort not in allowed_sorts:
        raise _error(
            422,
            ERROR_INVALID_FILTER,
            f"sort must be one of {', '.join(allowed_sorts)} for view={view}",
        )
    if type is not None and type not in JOB_DEFINITION_REGISTRY.types:
        raise _error(422, ERROR_INVALID_FILTER, f"unknown job type {type!r}")
    if type is not None and types:
        raise _error(422, ERROR_INVALID_FILTER, "type and types cannot be combined")
    if types:
        if len(types) > 32 or len(set(types)) != len(types):
            raise _error(422, ERROR_INVALID_FILTER, "types must contain 1..32 unique values")
        unknown_types = sorted(set(types) - JOB_DEFINITION_REGISTRY.types)
        if unknown_types:
            raise _error(422, ERROR_INVALID_FILTER, f"unknown job type {unknown_types[0]!r}")
    if subject_id is not None and subject_reference:
        raise _error(
            422,
            ERROR_INVALID_FILTER,
            "subject_id and subject_reference cannot be combined",
        )
    if subject_reference and (
        len(subject_reference) > 32
        or len(set(subject_reference)) != len(subject_reference)
        or any(not value or len(value) > 64 for value in subject_reference)
    ):
        raise _error(
            422,
            ERROR_INVALID_FILTER,
            "subject_reference must contain 1..32 unique bounded values",
        )
    if phase is not None and (view == "history" or phase not in _QUEUE_PHASES):
        raise _error(422, ERROR_INVALID_FILTER, "phase filters apply to queue phases only")
    if outcome is not None:
        if view == "queue":
            raise _error(422, ERROR_INVALID_FILTER, "outcome filters apply to history only")
        from marquee.models.job import JOB_OUTCOMES

        if outcome not in JOB_OUTCOMES:
            raise _error(422, ERROR_INVALID_FILTER, f"unknown outcome {outcome!r}")

    query = select(Job)
    filters: dict[str, Any] = {
        "hierarchy": hierarchy,
        "q": q,
        "feature_area": feature_area.value if feature_area else None,
        "type": type,
        "types": sorted(types) if types else None,
        "subject_kind": subject_kind,
        "subject_id": subject_id,
        "subject_reference": sorted(subject_reference) if subject_reference else None,
        "phase": phase,
        "outcome": outcome,
        "attention": attention.value if attention else None,
        "trigger": trigger.value if trigger else None,
        "parent_id": parent_id,
        "root_id": root_id,
        "correlation_id": correlation_id,
        "worker_id": worker_id,
        "execution_class": execution_class,
        "created_after": created_after,
        "created_before": created_before,
    }

    if view == "queue":
        query = query.where(Job.phase.in_(_QUEUE_PHASES))
        order = _queue_order()
    else:
        query = query.where(Job.phase == "terminal")
        order = _history_order(sort)

    if hierarchy == "activity":
        query = query.where(_activity_visibility_predicate())

    if q:
        escaped = q.replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_")

        def text_match(model):
            return cast(model.subject_snapshot, Text).ilike(f"%{escaped}%", escape="\\")

        query = query.where(
            _activity_related_match(text_match)
            if hierarchy == "activity"
            else Job.subject_snapshot.op("->>")("display_name").ilike(f"%{escaped}%", escape="\\")
        )
    if feature_area:
        query = query.where(
            _activity_related_match(lambda model: model.feature_area == feature_area.value)
            if hierarchy == "activity"
            else Job.feature_area == feature_area.value
        )
    if type:
        query = query.where(
            _activity_type_match((type,)) if hierarchy == "activity" else Job.type == type
        )
    if types:
        query = query.where(
            _activity_type_match(types) if hierarchy == "activity" else Job.type.in_(types)
        )
    if subject_kind:
        query = query.where(
            _activity_subject_kind_match(subject_kind)
            if hierarchy == "activity"
            else Job.subject_kind == subject_kind
        )
    if subject_id:
        query = query.where(
            _activity_subject_reference_match(subject_id)
            if hierarchy == "activity"
            else Job.subject_reference == subject_id
        )
    if subject_reference:
        query = query.where(
            or_(*(_activity_subject_reference_match(value) for value in subject_reference))
            if hierarchy == "activity"
            else Job.subject_reference.in_(subject_reference)
        )
    if phase:
        query = query.where(Job.phase == phase)
    if outcome:
        query = query.where(Job.outcome == outcome)
    if attention:
        if attention == AttentionLevel.NORMAL:
            query = query.where(or_(Job.attention.is_(None), _SEVERITY == 0))
        else:
            query = query.where(Job.attention.op("->>")("level") == attention.value)
    if trigger:
        query = query.where(Job.trigger_kind == trigger.value)
    if parent_id:
        query = query.where(Job.parent_id == parent_id)
    if root_id:
        query = query.where(Job.root_id == root_id)
    if correlation_id:
        query = query.where(Job.correlation_id == correlation_id)
    if worker_id:
        query = query.where(
            select(JobAttempt.id)
            .where(JobAttempt.job_id == Job.id, JobAttempt.worker_node_id == worker_id)
            .exists()
        )
    if execution_class:
        query = query.where(Job.execution_policy_id == execution_class)
    if created_after:
        query = query.where(Job.created_at >= datetime.fromtimestamp(created_after, UTC))
    if created_before:
        query = query.where(Job.created_at < datetime.fromtimestamp(created_before, UTC))

    contract = cursor_contract(view=view, filters=filters, sort=sort)
    if cursor:
        try:
            values = decode_cursor(cursor, contract=contract)
            parsed: list[Any] = []
            for (expr, _direction), value in zip(order, values, strict=True):
                name = getattr(expr, "key", None)
                if name in {"eligible_at", "created_at", "terminal_at"}:
                    parsed.append(_parse_cursor_datetime(value, name))
                else:
                    parsed.append(value)
            query = query.where(_keyset_filter(order, tuple(parsed)))
        except (InvalidCursorError, ValueError) as exc:
            raise _error(422, ERROR_INVALID_CURSOR, str(exc)) from None

    query = _apply_order(query, order).limit(limit + 1)
    rows = list((await db.scalars(query)).all())
    has_more = len(rows) > limit
    rows = rows[:limit]

    batches = await _batch_projections_for_jobs(db, rows)

    items: list[JobRow] = []
    for job in rows:
        definition = _definition_for(job.type)
        presenter = _presenter_for(definition)
        items.append(
            presenter.present_row(load_context(job, definition, batch=batches.get(job.id)))
        )

    if view == "queue":
        await _attach_queue_ranks(db, items, rows)

    next_cursor = None
    if has_more and rows:
        last = rows[-1]
        key = []
        for expr, _direction in order:
            name = getattr(expr, "key", None)
            if name is not None:
                key.append(_cursor_value(getattr(last, name)))
            elif expr is _SEVERITY:
                level = (
                    (last.attention or {}).get("level")
                    if isinstance(last.attention, dict)
                    else None
                )
                key.append(
                    {"error": 2, "warning": 1}.get(level if isinstance(level, str) else "", 0)
                )
            elif expr is _RUNNING_FIRST:
                key.append(0 if last.phase in ("running", "stopping") else 1)
            else:  # pragma: no cover - defensive
                raise RuntimeError("unmapped ordering expression")
        next_cursor = encode_cursor(contract=contract, key=key)

    return JobListResponse(view=view, items=items, next_cursor=next_cursor, limit=limit)


@router.get("/attention", response_model=ActivityAttentionResponse)
async def activity_attention(
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ActivityAttentionResponse:
    """Return one bounded aggregate for the Activity strip and navigation badge."""
    retrying = func.coalesce(Job.progress["wait"]["kind"].as_string() == "retry", False)
    warning = func.coalesce(
        Job.attention["level"].as_string() == AttentionLevel.WARNING.value,
        False,
    )
    error = func.coalesce(
        Job.attention["level"].as_string() == AttentionLevel.ERROR.value,
        False,
    )
    active = Job.phase.in_(_QUEUE_PHASES)
    review_required = (
        func.coalesce(Job.work_item_summary["counts"]["review_required"].as_integer(), 0) > 0
    )
    failed_work_item = func.coalesce(Job.work_item_summary["counts"]["failed"].as_integer(), 0) > 0
    error_attention = or_(error, failed_work_item)
    needs_attention = or_(warning, error_attention, review_required)
    row = (
        await db.execute(
            select(
                func.count().filter(Job.phase.in_(("running", "stopping"))),
                func.count().filter(
                    Job.phase.in_(("planned", "queued")),
                    ~retrying,
                ),
                func.count().filter(active, retrying),
                # This is intentionally not limited to the Queue: a completed poster
                # group can still require a choice or retry. Count visible cards, not
                # hidden parents/children or individual poster subjects.
                func.count().filter(needs_attention),
                func.count().filter(needs_attention, ~error_attention),
                func.count().filter(error_attention),
            ).where(_activity_visibility_predicate())
        )
    ).one()
    error_count = int(row[5] or 0)
    warning_count = int(row[4] or 0)
    highest = (
        AttentionLevel.ERROR
        if error_count
        else AttentionLevel.WARNING
        if warning_count
        else AttentionLevel.NORMAL
    )
    return ActivityAttentionResponse(
        running=int(row[0] or 0),
        waiting_held=int(row[1] or 0),
        retrying=int(row[2] or 0),
        needs_attention=int(row[3] or 0),
        warning=warning_count,
        error=error_count,
        highest_severity=highest,
    )


async def _attach_queue_ranks(db: AsyncSession, items: list[JobRow], rows: list[Job]) -> None:
    """Attach exact class-local rank when it is inside the bounded active window."""
    queued = [job for job in rows if job.phase == "queued"]
    if not queued:
        return
    by_class: dict[str, set[str]] = {}
    for job in queued:
        definition = _definition_for(job.type)
        by_class.setdefault(definition.execution_class, set()).add(job.id)
    ranks: dict[str, int] = {}
    for execution_class, visible_ids in by_class.items():
        class_types = [
            definition.job_type
            for definition in JOB_DEFINITION_REGISTRY
            if definition.execution_class == execution_class
        ]
        ordered = list(
            await db.scalars(
                select(Job.id)
                .where(Job.phase == "queued", Job.type.in_(class_types))
                .order_by(Job.eligible_at, Job.created_at, Job.id)
                .limit(QUEUE_RANK_LIMIT + 1)
            )
        )
        for position, job_id in enumerate(ordered[:QUEUE_RANK_LIMIT], start=1):
            if job_id in visible_ids:
                ranks[job_id] = position
    for index, item in enumerate(items):
        if item.job_id in ranks:
            items[index] = item.model_copy(update={"queue_rank": ranks[item.job_id]})


# ---------------------------------------------------------------------------
# Snapshot and presentation
# ---------------------------------------------------------------------------


class JobSnapshotResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1
    job_id: str
    type: str
    label: str
    phase: str
    outcome: str | None
    desired_state: str
    fence_token: int
    execution_class: str
    priority: int
    status: PresentationStatus
    attention: PresentationAttention
    allowed_actions: tuple[JobAction, ...]
    progress: CompactProgress | None
    progress_sequence: int
    work_items: WorkItemSummary | None = Field(default=None, exclude_if=lambda value: value is None)
    contained_work: ContainedWorkSummary | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    parent_id: str | None
    root_id: str | None
    retry_of_job_id: str | None
    configuration_version: int | None
    created_at: datetime | None
    eligible_at: datetime | None
    started_at: datetime | None
    terminal_at: datetime | None
    updated_at: datetime | None
    last_event_id: int | None
    links: RowLinks


ContainedWorkState = Literal[
    "pending",
    "running",
    "retrying",
    "succeeded",
    "no_change",
    "review_required",
    "failed",
    "cancelled",
]


class ContainedWorkItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1
    key: str
    ordinal: int = Field(ge=0, le=499)
    subject: dict[str, Any]
    status: ContainedWorkState
    status_label: str
    status_tone: Literal["neutral", "active", "positive", "warning", "negative"]
    stage_key: str | None = None
    stage_name: str | None = None
    stage_number: int | None = Field(default=None, ge=1, le=100)
    stage_total: int | None = Field(default=None, ge=1, le=100)
    progress: WorkItemProgress | None = None
    source_count: int | None = Field(default=None, ge=0)
    message: str | None = Field(default=None, max_length=2_000)
    sequence: int = Field(ge=0)
    updated_at: datetime
    detail_href: str | None = None


class ContainedWorkPage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1
    job_id: str
    summary: ContainedWorkSummary
    items: tuple[ContainedWorkItem, ...]
    next_cursor: str | None = None
    limit: int = Field(ge=1, le=100)
    historical_fallback: bool = False


class ActivityCatalogFeature(BaseModel):
    value: FeatureArea
    label: str


class ActivityCatalogJobType(BaseModel):
    value: str
    label: str
    feature_area: FeatureArea
    feature_label: str
    visibility: str
    contained_work: str


class ActivityCatalogResponse(BaseModel):
    version: Literal[1] = 1
    features: tuple[ActivityCatalogFeature, ...]
    job_types: tuple[ActivityCatalogJobType, ...]


@router.get("/activity-catalog", response_model=ActivityCatalogResponse)
async def activity_catalog() -> ActivityCatalogResponse:
    features: dict[FeatureArea, str] = {}
    job_types: list[ActivityCatalogJobType] = []
    for definition in JOB_DEFINITION_REGISTRY:
        policy = definition.activity_policy
        features.setdefault(definition.feature_area, policy.feature_label)
        job_types.append(
            ActivityCatalogJobType(
                value=definition.job_type,
                label=humanize_job_type(definition.job_type),
                feature_area=definition.feature_area,
                feature_label=policy.feature_label,
                visibility=policy.visibility.value,
                contained_work=policy.contained_work.value,
            )
        )
    return ActivityCatalogResponse(
        features=tuple(
            ActivityCatalogFeature(value=value, label=label)
            for value, label in sorted(features.items(), key=lambda item: item[1])
        ),
        job_types=tuple(sorted(job_types, key=lambda item: item.label)),
    )


async def _snapshot_for_job(job: Job, db: AsyncSession) -> JobSnapshotResponse:
    definition = _definition_for(job.type)
    batch = (await _batch_projections_for_jobs(db, [job])).get(job.id)
    ctx = load_context(job, definition, batch=batch)
    last_event_id = await db.scalar(select(func.max(JobEvent.id)).where(JobEvent.job_id == job.id))
    return JobSnapshotResponse(
        job_id=job.id,
        type=job.type,
        label=humanize_job_type(job.type),
        phase=job.phase,
        outcome=job.outcome,
        desired_state=job.desired_state,
        fence_token=job.fence_token,
        execution_class=definition.execution_class.value,
        priority=job.priority,
        status=present_status(job),
        attention=present_attention(ctx),
        allowed_actions=present_actions(job, definition),
        progress=present_compact_progress(ctx),
        progress_sequence=job.progress_sequence,
        work_items=work_item_summary(job),
        contained_work=present_contained_work(ctx),
        parent_id=job.parent_id,
        root_id=job.root_id,
        retry_of_job_id=job.retry_of_job_id,
        configuration_version=job.configuration_version,
        created_at=job.created_at,
        eligible_at=job.eligible_at,
        started_at=job.started_at,
        terminal_at=job.terminal_at,
        updated_at=job.updated_at,
        last_event_id=last_event_id,
        links=RowLinks(
            detail=f"/projection-room/jobs/{job.id}",
            snapshot=f"/api/jobs/{job.id}/snapshot",
            presentation=f"/api/jobs/{job.id}/presentation",
        ),
    )


@router.get("/{job_id}/snapshot", response_model=JobSnapshotResponse)
async def get_job_snapshot(job_id: str, db: Annotated[AsyncSession, Depends(get_db)]):
    return await _snapshot_for_job(await _load_job(db, job_id), db)


def _work_item_stage_name(stage_key: str | None) -> str | None:
    return stage_key.replace("_", " ").title() if stage_key else None


def _stored_work_item(row: JobWorkItem) -> WorkItemRow:
    progress = (
        WorkItemProgress(completed=row.completed, total=row.total, unit=row.unit)
        if row.completed is not None and row.total is not None
        else None
    )
    return WorkItemRow(
        subject_key=row.subject_key,
        ordinal=row.ordinal,
        subject_kind=row.subject_kind,
        subject_reference=row.subject_reference,
        subject=row.subject_snapshot,
        status=row.status,
        stage_key=row.stage_key,
        stage_name=_work_item_stage_name(row.stage_key),
        stage_number=row.stage_number,
        stage_total=row.stage_total,
        progress=progress,
        source_count=row.source_count,
        message=row.message,
        sequence=row.update_sequence,
        updated_at=row.updated_at,
    )


def _fallback_work_item(
    *,
    job: Job,
    wrapper: dict[str, Any],
    ordinal: int,
    run: PipelineRun | None,
) -> WorkItemRow:
    raw_subject = wrapper.get("subject")
    subject = raw_subject if isinstance(raw_subject, dict) else {}
    subject_key = str(wrapper.get("subject_key") or f"legacy:{ordinal}")[:200]
    if job.outcome == "cancelled":
        status = "cancelled"
        message = "Poster analysis was cancelled; temporary results were removed."
    elif run is not None and run.status == "failed":
        status = "failed"
        message = run.error or "Poster analysis failed."
    elif run is not None and run.status == "no_candidates":
        status = "no_change"
        message = "No viable poster change was found."
    elif run is not None and run.status == "flagged_manual":
        status = "review_required"
        message = "No candidate passed the configured filters."
    elif run is not None and run.status == "completed" and run.selected_artifact_id is not None:
        status = "succeeded"
        message = "Poster analysis completed."
    elif run is not None:
        status = "review_required"
        message = "Poster candidates are ready for review."
    else:
        status, message = historical_work_item_fallback(job)
    terminal = status not in {"pending", "running"}
    stage_key = "finalizing" if terminal else job.current_stage
    stage_total = 9
    stage_number = stage_total if terminal else None
    progress_document = job.progress if isinstance(job.progress, dict) else {}
    raw_overall = progress_document.get("overall")
    overall = raw_overall if isinstance(raw_overall, dict) else {}
    completed = overall.get("completed")
    if not terminal and isinstance(completed, int | float):
        stage_number = max(1, min(stage_total, int(completed)))
    updated_at = (
        run.completed_at if run is not None and run.completed_at is not None else job.updated_at
    ) or job.created_at
    subject_kind = str(subject.get("kind") or "poster")
    reference = subject.get(f"{subject_kind}_id")
    return WorkItemRow(
        subject_key=subject_key,
        ordinal=ordinal,
        subject_kind=subject_kind[:40],
        subject_reference=str(reference)[:64] if reference is not None else None,
        subject=subject,
        status=status,
        stage_key=stage_key,
        stage_name=_work_item_stage_name(stage_key),
        stage_number=stage_number,
        stage_total=stage_total,
        progress=None,
        message=message,
        sequence=job.work_item_sequence,
        updated_at=updated_at,
    )


@router.get("/{job_id}/work-items", response_model=WorkItemPage)
async def list_job_work_items(
    job_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    cursor: int | None = Query(default=None, ge=0, le=499),
    limit: int = Query(default=50, ge=1, le=100),
) -> WorkItemPage:
    """Return stable ordinal pages and an authoritative grouped-poster summary."""
    job = await _load_job(db, job_id)
    summary = work_item_summary(job) or WorkItemSummary(
        total=0,
        sequence=0,
        updated_at=job.updated_at,
        href=f"/api/jobs/{job.id}/work-items",
    )
    if job.work_item_summary is not None:
        query = select(JobWorkItem).where(JobWorkItem.job_id == job_id)
        if cursor is not None:
            query = query.where(JobWorkItem.ordinal > cursor)
        rows = list(
            (await db.scalars(query.order_by(JobWorkItem.ordinal.asc()).limit(limit + 1))).all()
        )
        has_more = len(rows) > limit
        rows = rows[:limit]
        return WorkItemPage(
            job_id=job.id,
            summary=summary,
            items=tuple(_stored_work_item(row) for row in rows),
            next_cursor=rows[-1].ordinal if has_more and rows else None,
            limit=limit,
        )

    snapshot = job.subject_snapshot if isinstance(job.subject_snapshot, dict) else {}
    raw_members = snapshot.get("members")
    members = list(raw_members) if isinstance(raw_members, list | tuple) else []
    runs = {
        run.subject_key: run
        for run in (await db.scalars(select(PipelineRun).where(PipelineRun.job_id == job.id))).all()
    }
    start = cursor + 1 if cursor is not None else 0
    bounded = members[start : start + limit + 1]
    has_more = len(bounded) > limit
    bounded = bounded[:limit]
    items = tuple(
        _fallback_work_item(
            job=job,
            wrapper=wrapper if isinstance(wrapper, dict) else {},
            ordinal=start + offset,
            run=runs.get(str(wrapper.get("subject_key"))) if isinstance(wrapper, dict) else None,
        )
        for offset, wrapper in enumerate(bounded)
    )
    return WorkItemPage(
        job_id=job.id,
        summary=summary,
        items=items,
        next_cursor=items[-1].ordinal if has_more and items else None,
        limit=limit,
        historical_fallback=True,
    )


_CONTAINED_STATUS_COPY: dict[str, tuple[ContainedWorkState, str, str]] = {
    "pending": ("pending", "In queue", "neutral"),
    "running": ("running", "Running", "active"),
    "retrying": ("retrying", "Retrying", "warning"),
    "succeeded": ("succeeded", "Succeeded", "positive"),
    "no_change": ("no_change", "No change needed", "positive"),
    "review_required": ("review_required", "Needs attention", "warning"),
    "failed": ("failed", "Failed", "negative"),
    "cancelled": ("cancelled", "Cancelled", "neutral"),
}


def _contained_work_item(row: WorkItemRow) -> ContainedWorkItem:
    state, label, tone = _CONTAINED_STATUS_COPY[row.status]
    return ContainedWorkItem(
        key=row.subject_key,
        ordinal=row.ordinal,
        subject=row.subject,
        status=state,
        status_label=label,
        status_tone=tone,
        stage_key=row.stage_key,
        stage_name=row.stage_name,
        stage_number=row.stage_number,
        stage_total=row.stage_total,
        progress=row.progress,
        source_count=row.source_count,
        message=row.message,
        sequence=row.sequence,
        updated_at=row.updated_at,
    )


def _child_contained_state(job: Job, row: JobRow) -> tuple[ContainedWorkState, str, str]:
    if job.phase != "terminal":
        if row.progress is not None and row.progress.wait is not None:
            return _CONTAINED_STATUS_COPY["retrying"]
        return _CONTAINED_STATUS_COPY[
            "running" if job.phase in {"running", "stopping"} else "pending"
        ]
    state = {
        "succeeded": "succeeded",
        "no_change": "no_change",
        "partially_succeeded": "review_required",
        "cancelled": "cancelled",
        "superseded": "cancelled",
    }.get(job.outcome or "", "failed")
    return _CONTAINED_STATUS_COPY[state]


def _child_contained_item(*, job: Job, row: JobRow, ordinal: int) -> ContainedWorkItem:
    state, label, tone = _child_contained_state(job, row)
    compact = row.progress
    measurement = None
    if compact is not None:
        candidate = compact.current or compact.overall
        if (
            candidate is not None
            and candidate.completed is not None
            and candidate.total is not None
        ):
            measurement = WorkItemProgress(
                completed=int(candidate.completed),
                total=int(candidate.total),
                unit=candidate.unit,
            )
    return ContainedWorkItem(
        key=job.id,
        ordinal=ordinal,
        subject=row.subject.model_dump(mode="json", exclude_none=True),
        status=state,
        status_label=label,
        status_tone=tone,
        stage_key=compact.stage_key if compact is not None else None,
        stage_name=compact.stage_label if compact is not None else None,
        progress=measurement,
        message=row.attention.message if row.attention.message else None,
        sequence=(compact.sequence or job.progress_sequence)
        if compact is not None
        else job.progress_sequence,
        updated_at=(compact.updated_at if compact is not None else None) or job.updated_at,
        detail_href=row.links.detail,
    )


@router.get("/{job_id}/contained-work", response_model=ContainedWorkPage)
async def list_contained_work(
    job_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    cursor: str | None = None,
    limit: int = Query(default=50, ge=1, le=100),
) -> ContainedWorkPage:
    """Return one source-neutral, stable page for an Activity disclosure."""
    job = await _load_job(db, job_id)
    definition = _definition_for(job.type)
    policy = definition.activity_policy
    batch = (await _batch_projections_for_jobs(db, [job])).get(job.id)
    ctx = load_context(job, definition, batch=batch)
    summary = present_contained_work(ctx)
    if summary is None:
        raise HTTPException(404, "Job does not expose contained Activity work")
    contract = cursor_contract(
        view="contained_work",
        filters={"job_id": job.id, "source": summary.source},
        sort="ordinal",
    )

    if summary.source == "work_items":
        after = None
        if cursor:
            try:
                (after_raw,) = decode_cursor(cursor, contract=contract)
                if after_raw is None or isinstance(after_raw, bool):
                    raise ValueError("contained-work cursor ordinal is invalid")
                after = int(after_raw)
            except (InvalidCursorError, ValueError) as exc:
                raise _error(422, ERROR_INVALID_CURSOR, str(exc)) from None
        page = await list_job_work_items(job.id, db, cursor=after, limit=limit)
        work_items = tuple(_contained_work_item(item) for item in page.items)
        next_cursor = (
            encode_cursor(contract=contract, key=(page.next_cursor,))
            if page.next_cursor is not None
            else None
        )
        return ContainedWorkPage(
            job_id=job.id,
            summary=summary,
            items=work_items,
            next_cursor=next_cursor,
            limit=limit,
            historical_fallback=page.historical_fallback,
        )

    offset = 0
    after_created: datetime | None = None
    after_id: str | None = None
    if cursor:
        try:
            offset_raw, created_raw, after_id_raw = decode_cursor(cursor, contract=contract)
            if offset_raw is None or isinstance(offset_raw, bool):
                raise ValueError("contained-work cursor ordinal is invalid")
            if not isinstance(after_id_raw, str):
                raise ValueError("contained-work cursor job ID is invalid")
            offset = int(offset_raw) + 1
            after_created = _parse_cursor_datetime(created_raw, "created_at")
            after_id = after_id_raw
        except (InvalidCursorError, ValueError) as exc:
            raise _error(422, ERROR_INVALID_CURSOR, str(exc)) from None
    child_types = policy.hidden_child_types or definition.child_job_types
    query = select(Job).where(Job.parent_id == job.id)
    if child_types:
        query = query.where(Job.type.in_(child_types))
    if after_created is not None and after_id is not None:
        query = query.where(
            or_(
                Job.created_at > after_created,
                and_(Job.created_at == after_created, Job.id > after_id),
            )
        )
    rows = list(
        (
            await db.scalars(query.order_by(Job.created_at.asc(), Job.id.asc()).limit(limit + 1))
        ).all()
    )
    has_more = len(rows) > limit
    rows = rows[:limit]
    items: list[ContainedWorkItem] = []
    for index, child_job in enumerate(rows):
        child_definition = _definition_for(child_job.type)
        presented = _presenter_for(child_definition).present_row(
            load_context(child_job, child_definition)
        )
        items.append(_child_contained_item(job=child_job, row=presented, ordinal=offset + index))
    next_cursor = None
    if has_more and rows:
        last = rows[-1]
        next_cursor = encode_cursor(
            contract=contract,
            key=(offset + len(rows) - 1, _cursor_value(last.created_at), last.id),
        )
    return ContainedWorkPage(
        job_id=job.id,
        summary=summary,
        items=tuple(items),
        next_cursor=next_cursor,
        limit=limit,
    )


async def _live_children_counts(db: AsyncSession, job_id: str) -> dict[str, int]:
    rows = (
        await db.execute(
            select(Job.phase, Job.outcome, func.count())
            .where(Job.parent_id == job_id)
            .group_by(Job.phase, Job.outcome)
        )
    ).all()
    counts = {
        "total": 0,
        "queued": 0,
        "running": 0,
        "succeeded": 0,
        "no_change": 0,
        "failed": 0,
        "cancelled": 0,
    }
    for phase, outcome, count in rows:
        counts["total"] += count
        if phase == "terminal":
            if outcome in ("failed", "dead_letter", "unsafe"):
                counts["failed"] += count
            elif outcome in ("cancelled", "superseded"):
                counts["cancelled"] += count
            elif outcome == "no_change":
                counts["no_change"] += count
            else:
                counts["succeeded"] += count
        elif phase in ("running", "stopping"):
            counts["running"] += count
        else:
            counts["queued"] += count
    return counts


_LIVE_SUBJECT_MODELS = {
    "movie": Movie,
    "series": Series,
    "season": Season,
    "episode": Episode,
    "media_file": MediaFile,
}


async def _live_subject_missing(db: AsyncSession, job: Job) -> bool:
    model = _LIVE_SUBJECT_MODELS.get(job.subject_kind)
    if model is None or not job.subject_reference:
        return False
    try:
        subject_id = int(job.subject_reference)
    except ValueError:
        return False
    row = await db.execute(select(model.id, model.is_present).where(model.id == subject_id))
    found = row.first()
    return found is None or not found.is_present


@router.get("/{job_id}/presentation", response_model=JobPresentation)
async def get_job_presentation(job_id: str, db: Annotated[AsyncSession, Depends(get_db)]):
    evidence = (
        await db.execute(
            select(
                Job,
                select(JobLog.id)
                .where(JobLog.job_id == job_id, JobLog.seal_status != "expired")
                .exists()
                .label("logs_available"),
                select(JobArtifact.id)
                .where(
                    JobArtifact.job_id == job_id,
                    JobArtifact.status == "available",
                    or_(JobArtifact.expires_at.is_(None), JobArtifact.expires_at > func.now()),
                )
                .exists()
                .label("artifacts_available"),
                MediaOperationDetail,
            )
            .outerjoin(MediaOperationDetail, MediaOperationDetail.job_id == Job.id)
            .where(Job.id == job_id)
        )
    ).one_or_none()
    if evidence is None:
        raise _error(404, ERROR_JOB_NOT_FOUND, "Job was not found.", job_id=job_id)
    job, logs_available, artifacts_available, mutation_detail = evidence
    definition = _definition_for(job.type)
    presenter = _presenter_for(definition)
    batch = (await _batch_projections_for_jobs(db, [job])).get(job.id)
    live: dict[str, Any] = {}
    if definition.child_job_types:
        live["children"] = {
            **(await _live_children_counts(db, job.id)),
            "sealed": True,
        }
    missing = await _live_subject_missing(db, job)
    try:
        ctx = load_context(
            job,
            definition,
            live=live,
            live_subject_missing=missing,
            logs_available=logs_available,
            artifacts_available=artifacts_available,
            mutation_detail=mutation_detail,
            batch=batch,
        )
    except PresentationIntegrityError as exc:
        logger.error("presentation integrity failure for job %s: %s", job_id, exc)
        raise HTTPException(500, "The stored canonical job evidence is invalid.") from exc
    return presenter.present(ctx)


# ---------------------------------------------------------------------------
# Paginated evidence
# ---------------------------------------------------------------------------


class AttemptItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    number: int
    phase: str
    outcome: str | None
    failure_class: str | None
    worker_node_id: str | None
    worker_build: str | None
    admitted_at: datetime | None
    started_at: datetime | None
    stopping_at: datetime | None
    finished_at: datetime | None
    exit_code: int | None
    exit_signal: int | None
    metrics: dict | None
    error: dict | None
    origin_job_id: str
    origin_subject: dict[str, Any]


class AttemptListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[AttemptItem]
    next_cursor: str | None
    limit: int


@router.get("/{job_id}/attempts", response_model=AttemptListResponse)
async def list_job_attempts(
    job_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    cursor: str | None = None,
    limit: int = Query(DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
    scope: Literal["self", "contained"] = "self",
):
    job = await _load_job(db, job_id)
    definition = _definition_for(job.type)
    contract = cursor_contract(
        view="attempts", filters={"job_id": job_id, "scope": scope}, sort="origin_number"
    )
    query = select(JobAttempt).where(
        _evidence_job_predicate(
            job=job, definition=definition, scope=scope, column=JobAttempt.job_id
        )
    )
    if cursor:
        try:
            after_job_id, after_number = decode_cursor(cursor, contract=contract)
        except (InvalidCursorError, ValueError) as exc:
            raise _error(422, ERROR_INVALID_CURSOR, str(exc)) from None
        query = query.where(
            or_(
                JobAttempt.job_id > after_job_id,
                and_(JobAttempt.job_id == after_job_id, JobAttempt.number > after_number),
            )
        )
    rows = list(
        (
            await db.scalars(
                query.order_by(JobAttempt.job_id.asc(), JobAttempt.number.asc()).limit(limit + 1)
            )
        ).all()
    )
    has_more = len(rows) > limit
    rows = rows[:limit]
    origins = await _origin_subjects(db, {attempt.job_id for attempt in rows})
    items = [
        AttemptItem(
            number=attempt.number,
            phase=attempt.phase,
            outcome=attempt.outcome,
            failure_class=attempt.failure_class,
            worker_node_id=attempt.worker_node_id,
            worker_build=attempt.worker_build,
            admitted_at=attempt.admitted_at,
            started_at=attempt.started_at,
            stopping_at=attempt.stopping_at,
            finished_at=attempt.finished_at,
            exit_code=attempt.exit_code,
            exit_signal=attempt.exit_signal,
            metrics=attempt.metrics,
            error=attempt.error,
            origin_job_id=attempt.job_id,
            origin_subject=origins.get(attempt.job_id, {}),
        )
        for attempt in rows
    ]
    next_cursor = (
        encode_cursor(contract=contract, key=(rows[-1].job_id, rows[-1].number))
        if has_more and rows
        else None
    )
    return AttemptListResponse(items=items, next_cursor=next_cursor, limit=limit)


class AttemptLogPage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[AttemptLogLine]
    next_cursor: int | None
    limit: int
    freshness: Literal["active", "sealed", "degraded", "expired"]
    sealed: bool
    truncated: bool
    compression: Literal["none", "gzip", "zstd"]
    byte_count: int = Field(ge=0)
    stored_byte_count: int = Field(ge=0)
    last_cursor: int = Field(ge=0)
    opened_at: datetime
    closed_at: datetime | None
    expires_at: datetime | None


async def _attempt_log(
    db: AsyncSession, *, job_id: str, attempt_id: int
) -> tuple[JobAttempt, JobLog]:
    await _load_job(db, job_id)
    attempt = await db.scalar(
        select(JobAttempt).where(JobAttempt.id == attempt_id, JobAttempt.job_id == job_id)
    )
    if attempt is None:
        raise HTTPException(status_code=404, detail="Attempt not found")
    row = await db.scalar(
        select(JobLog)
        .where(JobLog.job_id == job_id, JobLog.attempt_id == attempt_id)
        .order_by(JobLog.segment.desc())
        .limit(1)
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Attempt log not found")
    return attempt, row


def _log_freshness(row: JobLog) -> Literal["active", "sealed", "degraded", "expired"]:
    if row.seal_status == "expired":
        return "expired"
    if row.seal_status == "sealed":
        return "sealed"
    if row.seal_status in {"failed", "recovering"}:
        return "degraded"
    return "active"


@router.get(
    "/{job_id}/attempts/{attempt_id}/logs",
    response_model=AttemptLogPage,
)
async def list_attempt_logs(
    job_id: str,
    attempt_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    after: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    source: Literal["python", "stdout", "stderr", "system"] | None = None,
    level: Literal["debug", "info", "warning", "error"] | None = None,
):
    _, row = await _attempt_log(db, job_id=job_id, attempt_id=attempt_id)
    if source is not None and source not in LOG_SOURCES:
        raise HTTPException(status_code=422, detail="Log source is not allowlisted")
    if level is not None and level not in LOG_LEVELS:
        raise HTTPException(status_code=422, detail="Log level is not allowlisted")
    files = AttemptLogFiles.for_data_dir(settings.DATA_DIR)
    try:
        items, next_cursor, physical_gzip = await asyncio.to_thread(
            files.read_lines,
            row,
            after=after,
            limit=limit,
            source=source,
            level=level,
        )
    except (AttemptLogError, FilesystemBoundaryError, OSError) as exc:
        raise HTTPException(status_code=409, detail="Attempt log storage is unavailable") from exc
    compression = "gzip" if physical_gzip else row.compression
    return AttemptLogPage(
        items=items,
        next_cursor=next_cursor,
        limit=limit,
        freshness=_log_freshness(row),
        sealed=row.seal_status == "sealed",
        truncated=row.truncated,
        compression=compression,
        byte_count=row.byte_count,
        stored_byte_count=row.stored_byte_count,
        last_cursor=row.last_cursor,
        opened_at=row.opened_at,
        closed_at=row.closed_at,
        expires_at=row.expires_at,
    )


@router.get(
    "/{job_id}/attempts/{attempt_id}/logs/stream",
    response_model=AttemptLogLine,
    response_class=StreamingResponse,
    responses={200: {"content": {"text/event-stream": {}}}},
)
async def stream_attempt_logs(
    request: Request,
    job_id: str,
    attempt_id: int,
    after: int = Query(0, ge=0),
):
    factory = _get_session_factory()
    async with factory() as db:
        _, row = await _attempt_log(db, job_id=job_id, attempt_id=attempt_id)
    files = AttemptLogFiles.for_data_dir(settings.DATA_DIR)
    queue: asyncio.Queue[tuple[str, int, str]] = asyncio.Queue(
        maxsize=settings.JOB_LOG_STREAM_QUEUE_SIZE
    )

    async def produce() -> None:
        cursor = after
        while True:
            try:
                lines, _, compressed = await asyncio.to_thread(
                    files.read_lines, row, after=cursor, limit=64
                )
            except (AttemptLogError, FilesystemBoundaryError, OSError):
                await queue.put(
                    (
                        "log.unavailable",
                        cursor,
                        json.dumps({"cursor": cursor, "reconcile": "attempt_logs"}),
                    )
                )
                return
            for line in lines:
                if queue.full():
                    while not queue.empty():
                        queue.get_nowait()
                    await queue.put(
                        (
                            "log.reset_required",
                            cursor,
                            json.dumps({"cursor": cursor, "reconcile": "attempt_logs"}),
                        )
                    )
                    return
                cursor = line.cursor
                queue.put_nowait(("log.line", cursor, line.model_dump_json()))
            if compressed:
                await queue.put(
                    ("log.sealed", cursor, json.dumps({"cursor": cursor, "sealed": True}))
                )
                return
            await asyncio.sleep(settings.JOB_LOG_STREAM_POLL_SECONDS)

    producer = asyncio.create_task(produce(), name=f"attempt-log-stream-{attempt_id}")

    async def frames():
        try:
            while True:
                try:
                    event, cursor, data = await asyncio.wait_for(
                        queue.get(), timeout=settings.JOB_EVENT_KEEPALIVE_SECONDS
                    )
                except TimeoutError:
                    if await request.is_disconnected():
                        return
                    yield ": keepalive\n\n"
                    continue
                event_id = f"id: {cursor}\n" if cursor > 0 else ""
                yield f"{event_id}event: {event}\ndata: {data}\n\n"
                if event != "log.line":
                    return
        finally:
            producer.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await producer

    return StreamingResponse(
        frames(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/{job_id}/attempts/{attempt_id}/logs/download")
async def download_attempt_log(
    job_id: str,
    attempt_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    _, row = await _attempt_log(db, job_id=job_id, attempt_id=attempt_id)
    now = datetime.now(UTC)
    if row.seal_status == "expired" or (row.expires_at is not None and row.expires_at <= now):
        raise HTTPException(status_code=410, detail="Attempt log has expired")
    if row.seal_status == "open":
        raise HTTPException(status_code=409, detail="Attempt log is still active")
    if row.seal_status != "sealed" or row.checksum is None:
        raise HTTPException(status_code=503, detail="Attempt log is unavailable")
    files = AttemptLogFiles.for_data_dir(settings.DATA_DIR)
    try:
        checksum, stored_bytes, compressed = await asyncio.to_thread(files.checksum, row)
    except (AttemptLogError, FilesystemBoundaryError, OSError) as exc:
        raise HTTPException(status_code=404, detail="Attempt log storage is missing") from exc
    if checksum != row.checksum or stored_bytes != row.stored_byte_count:
        raise HTTPException(status_code=409, detail="Attempt log storage is corrupt")
    if row.compression == "gzip" and not compressed:
        raise HTTPException(status_code=409, detail="Attempt log compression is corrupt")
    return files.download_response(row)


class EventItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int
    canonical_version: int = Field(ge=0)
    event_key: str
    state: str
    stage: str | None
    message: str | None
    detail: dict | None
    created_at: datetime | None
    origin_job_id: str
    origin_subject: dict[str, Any]


class EventListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[EventItem]
    next_cursor: str | None
    limit: int


@router.get("/{job_id}/events", response_model=EventListResponse)
async def list_job_events(
    job_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    cursor: str | None = None,
    limit: int = Query(DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
    scope: Literal["self", "contained"] = "self",
):
    job = await _load_job(db, job_id)
    definition = _definition_for(job.type)
    contract = cursor_contract(view="events", filters={"job_id": job_id, "scope": scope}, sort="id")
    query = (
        select(JobEvent)
        .where(
            _evidence_job_predicate(
                job=job, definition=definition, scope=scope, column=JobEvent.job_id
            )
        )
        .order_by(JobEvent.id.asc())
    )
    if cursor:
        try:
            (after_id,) = decode_cursor(cursor, contract=contract)
        except (InvalidCursorError, ValueError) as exc:
            raise _error(422, ERROR_INVALID_CURSOR, str(exc)) from None
        query = query.where(JobEvent.id > after_id)
    rows = list((await db.scalars(query.limit(limit + 1))).all())
    has_more = len(rows) > limit
    rows = rows[:limit]
    origins = await _origin_subjects(db, {event.job_id for event in rows})
    items = [
        EventItem(
            id=event.id,
            canonical_version=(event.detail or {}).get("_canonical_version", 0),
            event_key=event.event_key,
            state=event.state,
            stage=event.stage,
            message=event.message,
            detail={
                key: value for key, value in (event.detail or {}).items() if not key.startswith("_")
            }
            or None,
            created_at=event.created_at,
            origin_job_id=event.job_id,
            origin_subject=origins.get(event.job_id, {}),
        )
        for event in rows
    ]
    next_cursor = (
        encode_cursor(contract=contract, key=(rows[-1].id,)) if has_more and rows else None
    )
    return EventListResponse(items=items, next_cursor=next_cursor, limit=limit)


class ArtifactItemResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int
    attempt_id: int | None
    kind: str
    name: str
    status: str
    content_type: str | None
    size_bytes: int | None
    checksum: str | None
    retention_class: str
    expires_at: datetime | None
    created_at: datetime | None
    available: bool
    virtual: bool
    download_url: str | None
    origin_job_id: str
    origin_subject: dict[str, Any]


class ArtifactListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[ArtifactItemResponse]
    next_cursor: str | None
    limit: int


@router.get("/{job_id}/artifacts", response_model=ArtifactListResponse)
async def list_job_artifacts(
    job_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    cursor: str | None = None,
    limit: int = Query(DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
    scope: Literal["self", "contained"] = "self",
):
    job = await _load_job(db, job_id)
    definition = _definition_for(job.type)
    contract = cursor_contract(
        view="artifacts", filters={"job_id": job_id, "scope": scope}, sort="id"
    )
    query = (
        select(JobArtifact)
        .where(
            _evidence_job_predicate(
                job=job, definition=definition, scope=scope, column=JobArtifact.job_id
            )
        )
        .order_by(JobArtifact.id.asc())
    )
    if cursor:
        try:
            (after_id,) = decode_cursor(cursor, contract=contract)
        except (InvalidCursorError, ValueError) as exc:
            raise _error(422, ERROR_INVALID_CURSOR, str(exc)) from None
        query = query.where(JobArtifact.id > after_id)
    rows = list((await db.scalars(query.limit(limit + 1))).all())
    has_more = len(rows) > limit
    rows = rows[:limit]
    origins = await _origin_subjects(db, {artifact.job_id for artifact in rows})
    items = [
        ArtifactItemResponse(
            id=artifact.id,
            attempt_id=artifact.attempt_id,
            kind=artifact.kind,
            name=artifact.name,
            status=artifact.status,
            content_type=artifact.content_type,
            size_bytes=artifact.size_bytes,
            checksum=artifact.checksum,
            retention_class=artifact.retention_class,
            expires_at=artifact.expires_at,
            created_at=artifact.created_at,
            available=artifact.status == "available"
            and (artifact.expires_at is None or artifact.expires_at > datetime.now(UTC)),
            virtual=artifact.virtual_source is not None,
            download_url=(
                f"/api/jobs/{artifact.job_id}/artifacts/{artifact.id}/download"
                if artifact.status == "available"
                else None
            ),
            origin_job_id=artifact.job_id,
            origin_subject=origins.get(artifact.job_id, {}),
        )
        for artifact in rows
    ]
    next_cursor = (
        encode_cursor(contract=contract, key=(rows[-1].id,)) if has_more and rows else None
    )
    return ArtifactListResponse(items=items, next_cursor=next_cursor, limit=limit)


@router.get("/{job_id}/artifacts/{artifact_id}/download")
async def download_job_artifact(
    job_id: str,
    artifact_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    artifact = await db.scalar(
        select(JobArtifact).where(JobArtifact.id == artifact_id, JobArtifact.job_id == job_id)
    )
    if artifact is None:
        raise HTTPException(404, "Job artifact was not found")
    now = datetime.now(UTC)
    if artifact.status == "expired" or (
        artifact.expires_at is not None and artifact.expires_at <= now
    ):
        raise HTTPException(410, "Job artifact has expired")
    if artifact.status != "available":
        raise HTTPException(503, "Job artifact is unavailable")
    if artifact.virtual_source is not None:
        try:
            encoded = await materialize_virtual_artifact(artifact)
        except ArtifactError as exc:
            raise HTTPException(409, "Virtual artifact integrity check failed") from exc
        return Response(
            encoded,
            media_type="application/json",
            headers={
                "Content-Disposition": f'attachment; filename="artifact-{artifact.id}.json"',
                "X-Content-Type-Options": "nosniff",
            },
        )
    try:
        boundary, classified = await verify_physical_artifact(artifact)
    except ArtifactMissingError as exc:
        raise HTTPException(404, "Job artifact storage is missing") from exc
    except (ArtifactError, FilesystemBoundaryError, OSError) as exc:
        raise HTTPException(409, "Job artifact storage is corrupt") from exc
    response = boundary.response(
        classified,
        media_type=artifact.content_type,
        filename=artifact.name,
    )
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


class ChildListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[JobRow]
    next_cursor: str | None
    limit: int


class BatchSummaryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_id: str
    mode: Literal["fixed", "dynamic"]
    sealed: bool
    sealed_at: datetime | None
    sealed_child_total: int | None
    created_total: int
    terminal_total: int
    outcomes: dict[str, int]
    failure_summary: dict | None
    attention_summary: dict | None
    projection_sequence: int
    updated_at: datetime


@router.get("/{job_id}/batch", response_model=BatchSummaryResponse)
async def get_job_batch(
    job_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    await _load_job(db, job_id)
    batch = await db.get(JobBatch, job_id)
    if batch is None:
        raise HTTPException(404, "Job is not a canonical batch parent")
    return BatchSummaryResponse(
        job_id=batch.parent_job_id,
        mode=batch.mode,
        sealed=batch.sealed,
        sealed_at=batch.sealed_at,
        sealed_child_total=batch.sealed_child_total,
        created_total=batch.created_total,
        terminal_total=batch.terminal_total,
        outcomes={
            "succeeded": batch.succeeded_total,
            "partially_succeeded": batch.partially_succeeded_total,
            "no_change": batch.no_change_total,
            "failed": batch.failed_total,
            "cancelled": batch.cancelled_total,
            "superseded": batch.superseded_total,
            "dead_letter": batch.dead_letter_total,
            "unsafe": batch.unsafe_total,
        },
        failure_summary=batch.failure_summary,
        attention_summary=batch.attention_summary,
        projection_sequence=batch.projection_sequence,
        updated_at=batch.updated_at,
    )


@router.get("/{job_id}/children", response_model=ChildListResponse)
async def list_job_children(
    job_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    cursor: str | None = None,
    limit: int = Query(DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
    outcome: str | None = None,
    sort: Literal["created", "failed_first"] = "failed_first",
):
    await _load_job(db, job_id)
    if outcome is not None:
        from marquee.models.job import JOB_OUTCOMES

        if outcome not in JOB_OUTCOMES:
            raise _error(422, ERROR_INVALID_FILTER, f"unknown outcome {outcome!r}")
    contract = cursor_contract(
        view="children", filters={"job_id": job_id, "outcome": outcome}, sort=sort
    )
    failure_rank = case(
        (Job.outcome.in_(("failed", "partially_succeeded", "unsafe", "dead_letter")), 0),
        else_=1,
    )
    query = select(Job).where(Job.parent_id == job_id)
    if sort == "failed_first":
        query = query.order_by(failure_rank.asc(), Job.created_at.asc(), Job.id.asc())
    else:
        query = query.order_by(Job.created_at.asc(), Job.id.asc())
    if outcome:
        query = query.where(Job.outcome == outcome)
    if cursor:
        try:
            values = decode_cursor(cursor, contract=contract)
            if sort == "failed_first":
                after_rank, created_raw, after_id = values
                if not isinstance(after_rank, str | int | float):
                    raise ValueError("invalid failure rank cursor")
                after_rank = int(after_rank)
            else:
                created_raw, after_id = values
                after_rank = None
            created = _parse_cursor_datetime(created_raw, "created_at")
        except (InvalidCursorError, ValueError) as exc:
            raise _error(422, ERROR_INVALID_CURSOR, str(exc)) from None
        created_after = or_(
            Job.created_at > created,
            and_(Job.created_at == created, Job.id > after_id),
        )
        if after_rank is None:
            query = query.where(created_after)
        else:
            query = query.where(
                or_(failure_rank > after_rank, and_(failure_rank == after_rank, created_after))
            )
    rows = list((await db.scalars(query.limit(limit + 1))).all())
    has_more = len(rows) > limit
    rows = rows[:limit]
    items = []
    for child in rows:
        definition = _definition_for(child.type)
        presenter = _presenter_for(definition)
        items.append(presenter.present_row(load_context(child, definition)))
    next_cursor = None
    if has_more and rows:
        last = rows[-1]
        key: tuple[Any, ...] = (_cursor_value(last.created_at), last.id)
        if sort == "failed_first":
            key = (
                0
                if last.outcome in {"failed", "partially_succeeded", "unsafe", "dead_letter"}
                else 1,
                *key,
            )
        next_cursor = encode_cursor(contract=contract, key=key)
    return ChildListResponse(items=items, next_cursor=next_cursor, limit=limit)


# ---------------------------------------------------------------------------
# Bounded raw documents
# ---------------------------------------------------------------------------


@router.get("/{job_id}/raw/{kind}")
async def get_job_raw_document(
    job_id: str,
    kind: Literal["request", "plan", "result", "error"],
    db: Annotated[AsyncSession, Depends(get_db)],
    download: bool = False,
):
    job = await _load_job(db, job_id)
    definition = _definition_for(job.type)
    stored = getattr(job, kind)
    if stored is None:
        raise _error(
            404, ERROR_JOB_NOT_FOUND, f"This job has no stored {kind} document", job_id=job_id
        )
    if kind == "plan":
        # Plans have no registry adapter yet; expose the bounded stored JSON.
        document = stored
        version = None
    else:
        adapter = getattr(definition, kind)
        version = getattr(job, f"{'payload' if kind == 'request' else kind}_version")
        try:
            document = adapter.validate(stored, version=version).model_dump(mode="json")
        except Exception:
            raise _error(
                409,
                "malformed_evidence",
                f"The stored {kind} document does not validate against its schema.",
                job_id=job_id,
            ) from None
    disposition = "attachment" if download else "inline"
    return JSONResponse(
        {"job_id": job.id, "kind": kind, "version": version, "document": document},
        headers={"Content-Disposition": f'{disposition}; filename="{job.id}-{kind}.json"'},
    )


# ---------------------------------------------------------------------------
# Canonical optimistic commands
# ---------------------------------------------------------------------------


class CommandRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_fence_token: int = Field(ge=0)


class PriorityUpdateRequest(CommandRequest):
    priority: int = Field(ge=0, le=100)


class CommandResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: JobAction
    execution_class: str
    snapshot: JobSnapshotResponse
    original_job_id: str | None = None
    replacement_job_id: str | None = None


CommandAction = Literal["cancel", "pause", "resume", "change_priority", "retry"]


class BulkActionItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str = Field(min_length=1, max_length=80)
    job_id: str = Field(min_length=1, max_length=32)
    action: CommandAction
    expected_fence_token: int = Field(ge=0)
    priority: int | None = Field(default=None, ge=0, le=100)


class BulkActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[BulkActionItem] = Field(min_length=1, max_length=100)


class BulkActionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str
    job_id: str
    action: CommandAction
    success: bool
    response: CommandResponse | None = None
    error: JobApiErrorDetail | None = None


class BulkActionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[BulkActionResult]


def _control_http_error(exc: JobControlError) -> HTTPException:
    detail = JobApiErrorDetail(
        code=exc.code,
        message=exc.message,
        job_id=exc.job_id,
        current_phase=exc.current_phase,
        current_outcome=exc.current_outcome,
        current_desired_state=exc.current_desired_state,
        current_version=exc.current_version,
        context=exc.context or {},
    )
    return HTTPException(
        status_code=404 if exc.code == ERROR_JOB_NOT_FOUND else 409,
        detail=detail.model_dump(mode="json"),
    )


async def _command_response(result: JobControlResult, db: AsyncSession) -> CommandResponse:
    return CommandResponse(
        action=result.action,
        execution_class=result.execution_class,
        snapshot=await _snapshot_for_job(result.job, db),
        original_job_id=result.original_job_id,
        replacement_job_id=result.replacement_job_id,
    )


async def _run_control(item: BulkActionItem, db: AsyncSession) -> CommandResponse:
    if item.action == "cancel":
        result = await control_cancel(
            db, job_id=item.job_id, expected_fence_token=item.expected_fence_token
        )
    elif item.action == "pause":
        result = await control_set_paused(
            db,
            job_id=item.job_id,
            expected_fence_token=item.expected_fence_token,
            paused=True,
        )
    elif item.action == "resume":
        result = await control_set_paused(
            db,
            job_id=item.job_id,
            expected_fence_token=item.expected_fence_token,
            paused=False,
        )
    elif item.action == "change_priority":
        if item.priority is None:
            raise JobControlError(
                ERROR_INVALID_FILTER,
                "priority is required for change_priority",
                item.job_id,
            )
        result = await control_change_priority(
            db,
            job_id=item.job_id,
            expected_fence_token=item.expected_fence_token,
            priority=item.priority,
        )
    else:
        result = await control_retry(
            db, job_id=item.job_id, expected_fence_token=item.expected_fence_token
        )
    return await _command_response(result, db)


async def _single_control(
    *,
    job_id: str,
    action: CommandAction,
    expected_fence_token: int,
    db: AsyncSession,
    priority: int | None = None,
) -> CommandResponse:
    try:
        return await _run_control(
            BulkActionItem(
                request_id="single",
                job_id=job_id,
                action=action,
                expected_fence_token=expected_fence_token,
                priority=priority,
            ),
            db,
        )
    except JobControlError as exc:
        raise _control_http_error(exc) from exc


@router.post("/{job_id}/cancel", response_model=CommandResponse)
async def cancel_job(
    job_id: str,
    body: CommandRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    return await _single_control(
        job_id=job_id,
        action="cancel",
        expected_fence_token=body.expected_fence_token,
        db=db,
    )


@router.post("/{job_id}/pause", response_model=CommandResponse)
async def pause_job(
    job_id: str,
    body: CommandRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    return await _single_control(
        job_id=job_id,
        action="pause",
        expected_fence_token=body.expected_fence_token,
        db=db,
    )


@router.post("/{job_id}/resume", response_model=CommandResponse)
async def resume_job(
    job_id: str,
    body: CommandRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    return await _single_control(
        job_id=job_id,
        action="resume",
        expected_fence_token=body.expected_fence_token,
        db=db,
    )


@router.post("/{job_id}/priority", response_model=CommandResponse)
async def update_job_priority(
    job_id: str,
    body: PriorityUpdateRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    return await _single_control(
        job_id=job_id,
        action="change_priority",
        expected_fence_token=body.expected_fence_token,
        priority=body.priority,
        db=db,
    )


@router.post("/{job_id}/retry", response_model=CommandResponse)
async def retry_job(
    job_id: str,
    body: CommandRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    return await _single_control(
        job_id=job_id,
        action="retry",
        expected_fence_token=body.expected_fence_token,
        db=db,
    )


@router.post("/actions", response_model=BulkActionResponse)
async def bulk_job_actions(
    body: BulkActionRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> BulkActionResponse:
    cache: dict[tuple[str, str, int, int | None], CommandResponse | JobApiErrorDetail] = {}
    results: list[BulkActionResult] = []
    for item in body.items:
        key = (item.job_id, item.action, item.expected_fence_token, item.priority)
        cached = cache.get(key)
        if cached is None:
            try:
                cached = await _run_control(item, db)
                await db.rollback()
            except JobControlError as exc:
                await db.rollback()
                cached = JobApiErrorDetail(
                    code=exc.code,
                    message=exc.message,
                    job_id=exc.job_id,
                    current_phase=exc.current_phase,
                    current_outcome=exc.current_outcome,
                    current_desired_state=exc.current_desired_state,
                    current_version=exc.current_version,
                    context=exc.context or {},
                )
            cache[key] = cached
        if isinstance(cached, CommandResponse):
            results.append(
                BulkActionResult(
                    request_id=item.request_id,
                    job_id=item.job_id,
                    action=item.action,
                    success=True,
                    response=cached,
                )
            )
        else:
            results.append(
                BulkActionResult(
                    request_id=item.request_id,
                    job_id=item.job_id,
                    action=item.action,
                    success=False,
                    error=cached,
                )
            )
    return BulkActionResponse(items=results)
