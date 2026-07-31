"""Observation-only durable progress for grouped poster members."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import delete, select

from marquee.core.jobs.event_service import job_event_writer
from marquee.core.jobs.work_item_documents import (
    WorkItemStatus,
    WorkItemStatusCounts,
    WorkItemSummary,
)
from marquee.database import _get_session_factory
from marquee.models import Job, JobWorkItem
from marquee.models.job import JOB_WORK_ITEM_STATUSES

if TYPE_CHECKING:
    from marquee.core.jobs.delivery import ExecutionContext
    from marquee.core.jobs.runner_progress import RunnerProgressFrame

logger = logging.getLogger(__name__)

_SCOPE = re.compile(r"^m(?P<ordinal>\d{2,3})$")
_TERMINAL = frozenset({"succeeded", "no_change", "review_required", "failed", "cancelled"})


def _subject_reference(snapshot: Mapping[str, Any]) -> str | None:
    kind = snapshot.get("kind")
    value = snapshot.get(f"{kind}_id") if isinstance(kind, str) else None
    return str(value)[:64] if isinstance(value, int | str) else None


def _summary(states: Sequence[str]) -> dict[str, Any]:
    counts = Counter(states)
    return {
        "version": 1,
        "total": len(states),
        "counts": {status: counts.get(status, 0) for status in JOB_WORK_ITEM_STATUSES},
    }


def historical_work_item_fallback(job: Job) -> tuple[WorkItemStatus, str | None]:
    """Map a retained aggregate outcome without inventing member-level failures."""
    if job.outcome == "succeeded":
        return "succeeded", "Poster analysis completed."
    if job.outcome == "no_change":
        return "no_change", "No viable poster change was found."
    if job.outcome == "partially_succeeded":
        return (
            "review_required",
            "This older run needs review; no poster-specific reason was recorded.",
        )
    if job.outcome in {"cancelled", "superseded"}:
        return "cancelled", "Poster analysis was cancelled."
    if job.outcome in {"failed", "dead_letter", "unsafe"} or job.phase == "terminal":
        return "failed", "Poster processing did not produce a successful result."
    if job.phase in {"running", "stopping"}:
        return "running", None
    return "pending", None


def work_item_summary(job: Job) -> WorkItemSummary | None:
    """Return stored summary or a no-backfill historical group fallback."""
    if job.type != "poster_pipeline_group" and job.work_item_summary is None:
        return None
    raw = job.work_item_summary if isinstance(job.work_item_summary, dict) else None
    if raw is not None:
        counts = WorkItemStatusCounts.model_validate(raw.get("counts") or {})
        total = int(raw.get("total") or sum(counts.model_dump().values()))
    else:
        snapshot = job.subject_snapshot if isinstance(job.subject_snapshot, dict) else {}
        members = snapshot.get("members")
        total = len(members) if isinstance(members, list | tuple) else 0
        result = job.result if isinstance(job.result, dict) else {}
        result_counts = (
            WorkItemStatusCounts(
                succeeded=int(result.get("succeeded_count") or 0),
                no_change=int(result.get("no_change_count") or 0),
                review_required=int(result.get("review_required_count") or 0),
                failed=int(result.get("failed_count") or 0),
            )
            if result
            else None
        )
        if result_counts is not None and sum(result_counts.model_dump().values()) == total:
            counts = result_counts
        else:
            status, _message = historical_work_item_fallback(job)
            counts = WorkItemStatusCounts(**{status: total})
    return WorkItemSummary(
        total=total,
        counts=counts,
        sequence=int(job.work_item_sequence or 0),
        updated_at=job.work_item_updated_at or job.updated_at,
        href=f"/api/jobs/{job.id}/work-items",
    )


@dataclass(slots=True)
class _ObservedItem:
    subject_key: str
    ordinal: int
    subject_kind: str
    subject_reference: str | None
    subject_snapshot: dict[str, Any]
    status: str = "pending"
    stage_key: str | None = None
    stage_number: int | None = None
    completed: int | None = None
    total: int | None = None
    unit: str | None = None
    message: str | None = None


class PosterWorkItemTracker:
    """Coalesce runner observations without taking execution authority."""

    def __init__(
        self,
        context: ExecutionContext,
        members: Sequence[tuple[str, Mapping[str, Any]]],
    ) -> None:
        self._context = context
        self._job_id = context.delivery.canonical_job_id
        self._attempt_id = context.attempt.attempt_id
        self._fence_token = context.attempt.fence_token
        definition = getattr(context, "definition", None)
        policy = getattr(definition, "progress_policy", None)
        declared_stages = getattr(policy, "stages", ())
        self._stages = tuple(key for key, _label in declared_stages) or (
            "resolving",
            "enumerating",
            "downloading",
            "validating",
            "deduplicating",
            "extracting",
            "scoring",
            "rendering",
            "finalizing",
        )
        self._stage_numbers = {key: index + 1 for index, key in enumerate(self._stages)}
        self._items = [
            _ObservedItem(
                subject_key=key,
                ordinal=ordinal,
                subject_kind=str(snapshot.get("kind") or "poster"),
                subject_reference=_subject_reference(snapshot),
                subject_snapshot=dict(snapshot),
            )
            for ordinal, (key, snapshot) in enumerate(members)
        ]
        self._by_key = {item.subject_key: item for item in self._items}
        self._dirty: set[str] = set()
        self._lock = asyncio.Lock()
        self._task: asyncio.Task[None] | None = None
        self._cadence = float(policy.persistence_cadence_seconds if policy is not None else 2)
        self._enabled = True

    @classmethod
    async def create(
        cls,
        context: ExecutionContext,
        members: Sequence[tuple[str, Mapping[str, Any]]],
    ) -> PosterWorkItemTracker:
        tracker = cls(context, members)
        await tracker._safe_initialize()
        return tracker

    async def _safe_initialize(self) -> None:
        try:
            async with self._context.session_factory() as session, session.begin():
                if not await self._context.writer.owns_current_attempt(session):
                    self._enabled = False
                    return
                job = await session.scalar(
                    select(Job).where(Job.id == self._job_id).with_for_update()
                )
                if job is None:
                    self._enabled = False
                    return
                await session.execute(delete(JobWorkItem).where(JobWorkItem.job_id == self._job_id))
                session.add_all(
                    [
                        JobWorkItem(
                            job_id=self._job_id,
                            subject_key=item.subject_key,
                            ordinal=item.ordinal,
                            subject_kind=item.subject_kind,
                            subject_reference=item.subject_reference,
                            subject_snapshot=item.subject_snapshot,
                            attempt_id=self._attempt_id,
                            fence_token=self._fence_token,
                        )
                        for item in self._items
                    ]
                )
                now = datetime.now(UTC)
                job.work_item_sequence += 1
                job.work_item_summary = _summary([item.status for item in self._items])
                job.work_item_updated_at = now
                await job_event_writer.append(
                    session,
                    job_id=self._job_id,
                    attempt_id=self._attempt_id,
                    event_key="work_items.updated",
                    state=job.phase,
                    message="Poster progress initialized",
                    detail={
                        "work_item_sequence": job.work_item_sequence,
                        "work_item_total": len(self._items),
                    },
                    canonical_version=self._fence_token,
                )
        except Exception:  # noqa: BLE001 - telemetry must not fail poster processing
            self._enabled = False
            logger.exception("poster work-item initialization could not be persisted")

    async def observe(self, frame: RunnerProgressFrame, mapped_stage: str) -> None:
        if not self._enabled:
            return
        async with self._lock:
            targets: list[_ObservedItem]
            if frame.scope is None:
                targets = [item for item in self._items if item.status not in _TERMINAL]
            else:
                match = _SCOPE.fullmatch(frame.scope)
                ordinal = int(match.group("ordinal")) if match is not None else -1
                targets = [self._items[ordinal]] if 0 <= ordinal < len(self._items) else []
            for item in targets:
                if item.status in _TERMINAL:
                    continue
                if item.stage_key != mapped_stage:
                    item.completed = None
                    item.total = None
                    item.unit = None
                item.status = "running"
                item.stage_key = mapped_stage
                item.stage_number = self._stage_numbers[mapped_stage]
                if frame.done is not None and frame.total is not None and frame.total > 0:
                    total = max(1, int(frame.total))
                    item.completed = min(total, max(0, int(frame.done)))
                    item.total = total
                    item.unit = frame.unit
                item.message = frame.message
                self._dirty.add(item.subject_key)
            self._ensure_flush_task()

    async def stage(self, stage_key: str) -> None:
        if not self._enabled or stage_key not in self._stage_numbers:
            return
        async with self._lock:
            for item in self._items:
                if item.status in _TERMINAL:
                    continue
                item.status = "running"
                item.stage_key = stage_key
                item.stage_number = self._stage_numbers[stage_key]
                item.completed = None
                item.total = None
                item.unit = None
                self._dirty.add(item.subject_key)
            self._ensure_flush_task()

    async def reconcile(
        self,
        outcomes: Mapping[str, tuple[str, str | None]],
    ) -> None:
        if not self._enabled:
            return
        async with self._lock:
            for key, item in self._by_key.items():
                status, message = outcomes.get(
                    key, ("failed", "Poster processing did not produce a terminal result.")
                )
                if status not in _TERMINAL:
                    status = "failed"
                item.status = status
                item.stage_key = "finalizing"
                item.stage_number = self._stage_numbers.get("finalizing", len(self._stages))
                item.completed = None
                item.total = None
                item.unit = None
                item.message = message
                self._dirty.add(key)
        await self.flush()

    async def close(self) -> None:
        task = self._task
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self.flush(), timeout=2)

    def _ensure_flush_task(self) -> None:
        if self._dirty and (self._task is None or self._task.done()):
            self._task = asyncio.create_task(self._delayed_flush())

    async def _delayed_flush(self) -> None:
        try:
            await asyncio.sleep(self._cadence)
            await self.flush()
        except asyncio.CancelledError:
            raise

    async def flush(self) -> None:
        if not self._enabled:
            return
        async with self._lock:
            dirty = set(self._dirty)
            if not dirty:
                return
            persisted = await self._safe_persist(dirty)
            if persisted:
                self._dirty.difference_update(dirty)
            elif self._enabled:
                self._ensure_flush_task()

    async def _safe_persist(self, dirty: set[str]) -> bool:
        try:
            async with self._context.session_factory() as session, session.begin():
                if not await self._context.writer.owns_current_attempt(session):
                    self._enabled = False
                    return False
                job = await session.scalar(
                    select(Job).where(Job.id == self._job_id).with_for_update()
                )
                if job is None:
                    self._enabled = False
                    return False
                rows = list(
                    (
                        await session.scalars(
                            select(JobWorkItem).where(
                                JobWorkItem.job_id == self._job_id,
                                JobWorkItem.subject_key.in_(dirty),
                                JobWorkItem.attempt_id == self._attempt_id,
                                JobWorkItem.fence_token == self._fence_token,
                            )
                        )
                    ).all()
                )
                if len(rows) != len(dirty):
                    self._enabled = False
                    return False
                sequence = job.work_item_sequence + 1
                now = datetime.now(UTC)
                for row in rows:
                    item = self._by_key[row.subject_key]
                    row.status = item.status
                    row.stage_key = item.stage_key
                    row.stage_number = item.stage_number
                    row.completed = item.completed
                    row.total = item.total
                    row.unit = item.unit
                    row.message = item.message
                    row.update_sequence = sequence
                    row.updated_at = now
                job.work_item_sequence = sequence
                job.work_item_summary = _summary([item.status for item in self._items])
                job.work_item_updated_at = now
                await job_event_writer.append(
                    session,
                    job_id=self._job_id,
                    attempt_id=self._attempt_id,
                    event_key="work_items.updated",
                    state=job.phase,
                    message="Poster progress updated",
                    detail={
                        "work_item_sequence": sequence,
                        "work_item_total": len(self._items),
                    },
                    canonical_version=self._fence_token,
                )
            return True
        except Exception:  # noqa: BLE001 - telemetry must not fail poster processing
            logger.exception("poster work-item progress could not be persisted")
            return False


async def terminalize_work_items(
    *,
    job_id: str,
    attempt_id: int,
    fence_token: int,
    status: str,
    message: str,
) -> bool:
    """Idempotently close unfinished rows after canonical terminalization."""
    if status not in {"failed", "cancelled"}:
        raise ValueError("unfinished work items may only terminalize as failed or cancelled")
    try:
        factory = _get_session_factory()
        async with factory() as session, session.begin():
            job = await session.scalar(select(Job).where(Job.id == job_id).with_for_update())
            if job is None:
                return True
            rows = list(
                (
                    await session.scalars(
                        select(JobWorkItem).where(
                            JobWorkItem.job_id == job_id,
                            JobWorkItem.attempt_id == attempt_id,
                            JobWorkItem.fence_token == fence_token,
                        )
                    )
                ).all()
            )
            unfinished = [row for row in rows if row.status not in _TERMINAL]
            if not unfinished:
                return True
            sequence = job.work_item_sequence + 1
            now = datetime.now(UTC)
            for row in unfinished:
                row.status = status
                row.message = message[:2_000]
                row.update_sequence = sequence
                row.updated_at = now
            job.work_item_sequence = sequence
            job.work_item_summary = _summary([row.status for row in rows])
            job.work_item_updated_at = now
            await job_event_writer.append(
                session,
                job_id=job_id,
                attempt_id=attempt_id,
                event_key="work_items.updated",
                state=job.outcome or job.phase,
                message="Poster progress finalized",
                detail={
                    "work_item_sequence": sequence,
                    "work_item_total": len(rows),
                },
                canonical_version=job.fence_token,
            )
        return True
    except Exception:  # noqa: BLE001 - cleanup reports its own degraded state
        logger.exception("poster work-item terminalization could not be persisted")
        return False
