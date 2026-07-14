"""Canonical bounded job read APIs and the narrow JMC2 control surface.

Queue/History lists, compact snapshots, curated presentations, and
cursor-paginated evidence replace the former unbounded job detail and per-job
polling SSE.  All list/detail responses use explicit response models; cursors
are opaque and bound to the exact view/filter/sort contract that issued them.
PgQueuer identifiers and rows remain private diagnostics.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Select, and_, case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from marquee.core.jobs.api_errors import (
    ERROR_INVALID_CURSOR,
    ERROR_INVALID_FILTER,
    ERROR_JOB_NOT_FOUND,
    JobApiErrorDetail,
)
from marquee.core.jobs.contracts import (
    AttentionLevel,
    FeatureArea,
    JobAction,
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
from marquee.core.jobs.definitions import JobDefinition
from marquee.core.jobs.labels import humanize_job_type
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
from marquee.core.jobs.presenters import load_context, resolve_presenter
from marquee.core.jobs.presenters.base import (
    PresentationIntegrityError,
    present_actions,
    present_attention,
    present_compact_progress,
    present_status,
)
from marquee.core.jobs.presenters.parents import PARENT_JOB_TYPES
from marquee.database import get_db
from marquee.models import (
    Episode,
    Job,
    JobArtifact,
    JobAttempt,
    JobEvent,
    MediaFile,
    Movie,
    Season,
    Series,
)

router = APIRouter(prefix="/api/jobs", tags=["jobs"])
logger = logging.getLogger(__name__)

DEFAULT_LIMIT = 50
MAX_LIMIT = 200
_QUEUE_PHASES = ("planned", "queued", "running", "stopping")
_QUEUE_SORTS = ("default",)
_HISTORY_SORTS = ("default", "created", "-created")
_RAW_KINDS = ("request", "plan", "result", "error")

def _error(status_code: int, code: str, message: str, **fields) -> HTTPException:
    detail = JobApiErrorDetail(code=code, message=message, **fields)
    return HTTPException(status_code=status_code, detail=detail.model_dump(mode="json"))


def _definition_for(job_type: str) -> JobDefinition:
    definition = JOB_DEFINITION_REGISTRY.find(job_type)
    if definition is None:
        raise _error(
            409,
            "unknown_job_definition",
            "The stored job type has no registered definition.",
        )
    return definition


def _presenter_for(definition: JobDefinition):
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


@router.get("", response_model=JobListResponse)
async def list_jobs(
    db: Annotated[AsyncSession, Depends(get_db)],
    view: Literal["queue", "history"] = "queue",
    cursor: str | None = None,
    limit: int = Query(DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
    sort: str = "default",
    q: Annotated[str | None, Query(max_length=100)] = None,
    feature_area: FeatureArea | None = None,
    type: str | None = None,
    subject_kind: str | None = None,
    subject_id: Annotated[str | None, Query(max_length=64)] = None,
    phase: str | None = None,
    outcome: str | None = None,
    attention: AttentionLevel | None = None,
    trigger: TriggerKind | None = None,
    parent_id: Annotated[str | None, Query(max_length=32)] = None,
    root_id: Annotated[str | None, Query(max_length=32)] = None,
    correlation_id: Annotated[str | None, Query(max_length=64)] = None,
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
        "q": q,
        "feature_area": feature_area.value if feature_area else None,
        "type": type,
        "subject_kind": subject_kind,
        "subject_id": subject_id,
        "phase": phase,
        "outcome": outcome,
        "attention": attention.value if attention else None,
        "trigger": trigger.value if trigger else None,
        "parent_id": parent_id,
        "root_id": root_id,
        "correlation_id": correlation_id,
        "created_after": created_after,
        "created_before": created_before,
    }

    if view == "queue":
        query = query.where(Job.phase.in_(_QUEUE_PHASES))
        order = _queue_order()
    else:
        query = query.where(Job.phase == "terminal")
        order = _history_order(sort)

    if q:
        escaped = q.replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_")
        query = query.where(
            Job.subject_snapshot.op("->>")("display_name").ilike(f"%{escaped}%", escape="\\")
        )
    if feature_area:
        query = query.where(Job.feature_area == feature_area.value)
    if type:
        query = query.where(Job.type == type)
    if subject_kind:
        query = query.where(Job.subject_kind == subject_kind)
    if subject_id:
        query = query.where(Job.subject_reference == subject_id)
    if phase:
        query = query.where(Job.phase == phase)
    if outcome:
        query = query.where(Job.outcome == outcome)
    if attention:
        if attention == AttentionLevel.NORMAL:
            query = query.where(
                or_(Job.attention.is_(None), _SEVERITY == 0)
            )
        else:
            query = query.where(
                Job.attention.op("->>")("level") == attention.value
            )
    if trigger:
        query = query.where(Job.trigger_kind == trigger.value)
    if parent_id:
        query = query.where(Job.parent_id == parent_id)
    if root_id:
        query = query.where(Job.root_id == root_id)
    if correlation_id:
        query = query.where(Job.correlation_id == correlation_id)
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

    items: list[JobRow] = []
    for job in rows:
        definition = _definition_for(job.type)
        presenter = _presenter_for(definition)
        items.append(presenter.present_row(load_context(job, definition)))

    if view == "queue":
        _attach_queue_ranks(items, rows)

    next_cursor = None
    if has_more and rows:
        last = rows[-1]
        key = []
        for expr, _direction in order:
            name = getattr(expr, "key", None)
            if name is not None:
                key.append(_cursor_value(getattr(last, name)))
            elif expr is _SEVERITY:
                level = (last.attention or {}).get("level") if isinstance(last.attention, dict) else None
                key.append({"error": 2, "warning": 1}.get(level, 0))
            elif expr is _RUNNING_FIRST:
                key.append(0 if last.phase in ("running", "stopping") else 1)
            else:  # pragma: no cover - defensive
                raise RuntimeError("unmapped ordering expression")
        next_cursor = encode_cursor(contract=contract, key=key)

    return JobListResponse(view=view, items=items, next_cursor=next_cursor, limit=limit)


def _attach_queue_ranks(items: list[JobRow], rows: list[Job]) -> None:
    """Approximate class-local rank for queued rows; never a global promise."""
    queued = [job for job in rows if job.phase == "queued"]
    if not queued:
        return
    by_class: dict[str, list[str]] = {}
    for job in queued:
        definition = _definition_for(job.type)
        by_class.setdefault(definition.execution_class, []).append(job.id)
    ranks: dict[str, int] = {}
    for execution_class in by_class:
        class_types = [
            definition.job_type
            for definition in JOB_DEFINITION_REGISTRY
            if definition.execution_class == execution_class
        ]
        ordered = [
            job.id
            for job in queued
            if job.type in class_types
        ]
        # Rank inside the page's own queued set; a full-table rank would scan
        # unbounded history for a number the UI labels approximate anyway.
        for position, job_id in enumerate(ordered, start=1):
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
    priority: int
    status: PresentationStatus
    attention: PresentationAttention
    allowed_actions: tuple[JobAction, ...]
    progress: CompactProgress | None
    progress_sequence: int
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


async def _snapshot_for_job(job: Job, db: AsyncSession) -> JobSnapshotResponse:
    definition = _definition_for(job.type)
    ctx = load_context(job, definition)
    last_event_id = await db.scalar(
        select(func.max(JobEvent.id)).where(JobEvent.job_id == job.id)
    )
    return JobSnapshotResponse(
        job_id=job.id,
        type=job.type,
        label=humanize_job_type(job.type),
        phase=job.phase,
        outcome=job.outcome,
        desired_state=job.desired_state,
        fence_token=job.fence_token,
        priority=job.priority,
        status=present_status(job),
        attention=present_attention(ctx),
        allowed_actions=present_actions(job, definition),
        progress=present_compact_progress(ctx),
        progress_sequence=job.progress_sequence,
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
    row = await db.execute(
        select(model.id, model.is_present).where(model.id == subject_id)
    )
    found = row.first()
    return found is None or not found.is_present


@router.get("/{job_id}/presentation", response_model=JobPresentation)
async def get_job_presentation(job_id: str, db: Annotated[AsyncSession, Depends(get_db)]):
    job = await _load_job(db, job_id)
    definition = _definition_for(job.type)
    presenter = _presenter_for(definition)
    live: dict[str, Any] = {}
    if job.type in PARENT_JOB_TYPES:
        live["children"] = {
            **(await _live_children_counts(db, job.id)),
            "sealed": True,
        }
    missing = await _live_subject_missing(db, job)
    try:
        ctx = load_context(job, definition, live=live, live_subject_missing=missing)
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
):
    await _load_job(db, job_id)
    contract = cursor_contract(view="attempts", filters={"job_id": job_id}, sort="number")
    query = (
        select(JobAttempt)
        .where(JobAttempt.job_id == job_id)
        .order_by(JobAttempt.number.asc())
    )
    if cursor:
        try:
            (after_number,) = decode_cursor(cursor, contract=contract)
        except (InvalidCursorError, ValueError) as exc:
            raise _error(422, ERROR_INVALID_CURSOR, str(exc)) from None
        query = query.where(JobAttempt.number > after_number)
    rows = list((await db.scalars(query.limit(limit + 1))).all())
    has_more = len(rows) > limit
    rows = rows[:limit]
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
        )
        for attempt in rows
    ]
    next_cursor = (
        encode_cursor(contract=contract, key=(rows[-1].number,)) if has_more and rows else None
    )
    return AttemptListResponse(items=items, next_cursor=next_cursor, limit=limit)


class EventItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int
    event_key: str
    state: str
    stage: str | None
    message: str | None
    detail: dict | None
    created_at: datetime | None


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
):
    await _load_job(db, job_id)
    contract = cursor_contract(view="events", filters={"job_id": job_id}, sort="id")
    query = select(JobEvent).where(JobEvent.job_id == job_id).order_by(JobEvent.id.asc())
    if cursor:
        try:
            (after_id,) = decode_cursor(cursor, contract=contract)
        except (InvalidCursorError, ValueError) as exc:
            raise _error(422, ERROR_INVALID_CURSOR, str(exc)) from None
        query = query.where(JobEvent.id > after_id)
    rows = list((await db.scalars(query.limit(limit + 1))).all())
    has_more = len(rows) > limit
    rows = rows[:limit]
    items = [
        EventItem(
            id=event.id,
            event_key=event.event_key,
            state=event.state,
            stage=event.stage,
            message=event.message,
            detail=event.detail,
            created_at=event.created_at,
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
):
    await _load_job(db, job_id)
    contract = cursor_contract(view="artifacts", filters={"job_id": job_id}, sort="id")
    query = (
        select(JobArtifact).where(JobArtifact.job_id == job_id).order_by(JobArtifact.id.asc())
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
    items = [
        ArtifactItemResponse(
            id=artifact.id,
            kind=artifact.kind,
            name=artifact.name,
            status=artifact.status,
            content_type=artifact.content_type,
            size_bytes=artifact.size_bytes,
            checksum=artifact.checksum,
            retention_class=artifact.retention_class,
            expires_at=artifact.expires_at,
            created_at=artifact.created_at,
            # Physical storage/streaming arrive in Chunk 3; availability stays
            # metadata-only until then.
            available=False,
        )
        for artifact in rows
    ]
    next_cursor = (
        encode_cursor(contract=contract, key=(rows[-1].id,)) if has_more and rows else None
    )
    return ArtifactListResponse(items=items, next_cursor=next_cursor, limit=limit)


class ChildListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[JobRow]
    next_cursor: str | None
    limit: int


@router.get("/{job_id}/children", response_model=ChildListResponse)
async def list_job_children(
    job_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    cursor: str | None = None,
    limit: int = Query(DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
    outcome: str | None = None,
):
    await _load_job(db, job_id)
    if outcome is not None:
        from marquee.models.job import JOB_OUTCOMES

        if outcome not in JOB_OUTCOMES:
            raise _error(422, ERROR_INVALID_FILTER, f"unknown outcome {outcome!r}")
    contract = cursor_contract(
        view="children", filters={"job_id": job_id, "outcome": outcome}, sort="created"
    )
    query = (
        select(Job)
        .where(Job.parent_id == job_id)
        .order_by(Job.created_at.asc(), Job.id.asc())
    )
    if outcome:
        query = query.where(Job.outcome == outcome)
    if cursor:
        try:
            created_raw, after_id = decode_cursor(cursor, contract=contract)
            created = _parse_cursor_datetime(created_raw, "created_at")
        except (InvalidCursorError, ValueError) as exc:
            raise _error(422, ERROR_INVALID_CURSOR, str(exc)) from None
        query = query.where(
            or_(
                Job.created_at > created,
                and_(Job.created_at == created, Job.id > after_id),
            )
        )
    rows = list((await db.scalars(query.limit(limit + 1))).all())
    has_more = len(rows) > limit
    rows = rows[:limit]
    items = []
    for child in rows:
        definition = _definition_for(child.type)
        presenter = _presenter_for(definition)
        items.append(presenter.present_row(load_context(child, definition)))
    next_cursor = (
        encode_cursor(
            contract=contract,
            key=(_cursor_value(rows[-1].created_at), rows[-1].id),
        )
        if has_more and rows
        else None
    )
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
        headers={
            "Content-Disposition": f'{disposition}; filename="{job.id}-{kind}.json"'
        },
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


async def _command_response(
    result: JobControlResult, db: AsyncSession
) -> CommandResponse:
    return CommandResponse(
        action=result.action,
        execution_class=result.execution_class,
        snapshot=await _snapshot_for_job(result.job, db),
        original_job_id=result.original_job_id,
        replacement_job_id=result.replacement_job_id,
    )


async def _run_control(
    item: BulkActionItem, db: AsyncSession
) -> CommandResponse:
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


@router.patch("/{job_id}/priority", response_model=CommandResponse)
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
