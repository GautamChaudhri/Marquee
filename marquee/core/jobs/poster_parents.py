"""Bounded discovery and sealed parent construction for poster mutations."""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from marquee.config import settings
from marquee.core.jobs.batches import (
    MAX_FIXED_CHILDREN,
    BatchScope,
    FixedBatchResult,
    create_fixed_batch,
)
from marquee.core.jobs.contracts import TriggerKind
from marquee.core.jobs.submission import Initiator, SubjectLocator, SubmissionIntent
from marquee.core.path_utils import safe_translate_and_validate
from marquee.core.poster_subjects import PosterSubject
from marquee.models import Movie, Season, Series

PosterParentOperation = Literal["reset", "backup", "heal"]


@dataclass(frozen=True, slots=True)
class PosterParentDiscovery:
    subjects: tuple[tuple[str, int], ...]
    unchanged_count: int = 0
    unsupported_count: int = 0


async def _poster_exists(subject: PosterSubject) -> bool | None:
    if not subject.folder_raw:
        return None
    try:
        folder = safe_translate_and_validate(subject.folder_raw, source=subject.path_source)
    except ValueError:
        return None
    destination = folder / subject.render_filename()
    return await asyncio.to_thread(Path(destination).is_file)


async def discover_poster_parent_subjects(
    session: AsyncSession,
    *,
    operation: PosterParentOperation,
) -> PosterParentDiscovery:
    """Resolve at most the canonical fixed-child cap with three bounded queries."""
    cutoff = datetime.now(UTC) - timedelta(minutes=settings.HEAL_RECENT_DEPLOY_GRACE_MINUTES)
    selected: list[tuple[str, int]] = []
    unchanged = unsupported = 0

    def eligible(model):
        predicates = [model.poster_path.is_not(None)]
        if operation == "heal":
            predicates.append(
                or_(model.poster_deployed_at.is_(None), model.poster_deployed_at < cutoff)
            )
        return predicates

    movie_rows = tuple(
        (
            await session.scalars(
                select(Movie)
                .where(*eligible(Movie))
                .order_by(Movie.id)
                .limit(MAX_FIXED_CHILDREN + 1)
            )
        ).all()
    )
    series_rows = tuple(
        (
            await session.scalars(
                select(Series)
                .where(*eligible(Series))
                .order_by(Series.id)
                .limit(MAX_FIXED_CHILDREN + 1)
            )
        ).all()
    )
    season_rows = tuple(
        (
            await session.execute(
                select(Season, Series)
                .join(Series, Series.id == Season.series_id)
                .where(*eligible(Season))
                .order_by(Season.id)
                .limit(MAX_FIXED_CHILDREN + 1)
            )
        ).all()
    )
    if len(movie_rows) + len(series_rows) + len(season_rows) > MAX_FIXED_CHILDREN:
        raise ValueError(f"poster parent scope exceeds {MAX_FIXED_CHILDREN} subjects")

    candidates = [
        *(PosterSubject.from_movie(row) for row in movie_rows),
        *(PosterSubject.from_series(row) for row in series_rows),
        *(PosterSubject.from_season(season, series) for season, series in season_rows),
    ]
    for subject in candidates:
        if operation != "heal":
            selected.append((subject.media_type, subject.id))
            continue
        exists = await _poster_exists(subject)
        if exists is None:
            unsupported += 1
        elif exists:
            unchanged += 1
        else:
            selected.append((subject.media_type, subject.id))
    return PosterParentDiscovery(
        subjects=tuple(selected),
        unchanged_count=unchanged,
        unsupported_count=unsupported,
    )


def _child_key(parent_key: str, job_type: str, kind: str, subject_id: int) -> str:
    digest = hashlib.sha256(f"{parent_key}:{kind}:{subject_id}".encode()).hexdigest()
    return f"{job_type}:{digest}"


async def create_poster_parent(
    session: AsyncSession,
    *,
    parent_job_type: Literal["poster_deploy_reset", "poster_backup_all", "poster_heal"],
    idempotency_key: str,
    trigger: TriggerKind,
    initiator: Initiator,
    priority: int = 35,
    subjects: Sequence[tuple[str, int]] | None = None,
    scope_name: str | None = None,
) -> FixedBatchResult:
    """Seal a parent with one isolated child per poster subject.

    ``subjects`` names the subjects explicitly instead of discovering them, for
    callers that already know their scope — resetting one series, say, rather
    than the whole library. Discovery's unchanged/unsupported tallies do not
    apply to an explicit scope, so they report zero.
    """
    operation: PosterParentOperation
    child_type: str
    if parent_job_type == "poster_deploy_reset":
        operation, child_type = "reset", "poster_reset"
    elif parent_job_type == "poster_backup_all":
        operation, child_type = "backup", "poster_backup_subject"
    else:
        operation, child_type = "heal", "poster_restore"
    discovery = (
        PosterParentDiscovery(subjects=tuple(subjects))
        if subjects is not None
        else await discover_poster_parent_subjects(session, operation=operation)
    )
    children = []
    for kind, subject_id in discovery.subjects:
        request: dict[str, object] = {"target_kind": kind, "target_id": subject_id}
        if child_type == "poster_reset":
            request["preserve_cache"] = True
        elif child_type == "poster_backup_subject":
            request["retention"] = "recoverable_artwork"
        else:
            request["allowed_sources"] = ["backup", "cache"]
        children.append(
            SubmissionIntent(
                job_type=child_type,
                request=request,
                subject=SubjectLocator(kind=kind, reference=str(subject_id)),
                trigger=TriggerKind.BATCH,
                initiator=initiator,
                idempotency_key=_child_key(idempotency_key, child_type, kind, subject_id),
                priority=priority,
            )
        )
    scope_name = (
        scope_name
        or {
            "reset": "Reset deployed posters",
            "backup": "Back up deployed posters",
            "heal": "Heal missing posters",
        }[operation]
    )
    return await create_fixed_batch(
        session,
        parent_job_type=parent_job_type,
        parent_request={
            "operation": operation,
            "scope": "missing" if operation == "heal" else "all",
            "selection_count": len(children),
            "unchanged_count": discovery.unchanged_count,
            "unsupported_count": discovery.unsupported_count,
        },
        scope=BatchScope(
            reference=hashlib.sha256(idempotency_key.encode()).hexdigest()[:32],
            display_name=scope_name,
            summary=(
                f"{len(children)} selected, {discovery.unchanged_count} unchanged, "
                f"{discovery.unsupported_count} unsupported"
            ),
        ),
        trigger=trigger,
        initiator=initiator,
        idempotency_key=idempotency_key,
        children=children,
        priority=priority,
    )
