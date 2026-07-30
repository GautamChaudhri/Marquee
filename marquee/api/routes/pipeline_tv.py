"""TV pipeline routes — run triggers, review queue, series history (design 04 §10).

Mirrors ``marquee/api/routes/pipeline.py``'s movie endpoints. TV producers seal
ticketless ``poster_pipeline_tv_batch`` control parents whose show and season
assets execute as canonical, non-deploying single or grouped analysis leaves.
"""

from __future__ import annotations

import json
import logging
import time
from collections import defaultdict
from datetime import UTC, datetime
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy import and_, exists, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from marquee.api.deps import enforce_rate_limit, get_rate_limiter
from marquee.api.job_submission import JobSubmissionResponse, submission_response
from marquee.api.results import poster_url
from marquee.api.routes.jobs import job_summary
from marquee.api.routes.pipeline import (
    _backup_stats,
    _repair_stale_batch_pipeline_runs,
    aggregate_run_metrics,
)
from marquee.config import settings
from marquee.core.configuration_cache import configuration_provider
from marquee.core.jobs.batches import BatchScope, create_fixed_batch
from marquee.core.jobs.contracts import TriggerKind
from marquee.core.jobs.poster_parents import create_poster_parent
from marquee.core.jobs.poster_submission import (
    PosterSelectionError,
    poster_child_idempotency_key,
    subject_artwork_selection,
    submit_poster_leaf,
)
from marquee.core.jobs.poster_summary import latest_poster_heal_summary
from marquee.core.jobs.submission import (
    IdempotencyConflictError,
    Initiator,
    SubjectLocator,
    SubmissionError,
    SubmissionIntent,
    SubmissionResult,
)
from marquee.core.pipeline_config import pipeline_settings
from marquee.core.rate_limit import RateLimiter
from marquee.core.tv_queries import season_downloaded, series_visible
from marquee.database import get_db
from marquee.models import ArtworkEvent, Job, JobArtifact, PipelineRun, Season, Series

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/pipeline/tv", tags=["pipeline-tv"])
series_router = APIRouter(prefix="/api/series", tags=["series"])

_REVIEW_QUEUE_STATUSES = {"completed", "flagged_manual"}
_ACTIVE_JOB_PHASES = {"planned", "queued", "running", "stopping"}
_MIN_DATETIME = datetime.min.replace(tzinfo=UTC)


def _clear_poster_columns(entity) -> None:
    """Reset every ``ArtworkMixin`` poster column (shared movie/series/season shape)."""
    entity.poster_path = None
    entity.poster_source = None
    entity.poster_source_url = None
    entity.poster_ai_selected = False
    entity.poster_embedding = None
    entity.poster_sha256 = None
    entity.poster_phash = None
    entity.poster_user_approved = False
    entity.poster_deployed_filename = None
    entity.poster_deployed_at = None
    entity.poster_local_backup_path = None


def _run_summary(run: PipelineRun) -> dict:
    return {
        "run_id": run.run_id,
        "status": run.status,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "completed_at": run.completed_at.isoformat() if run.completed_at else None,
        "scorer_name": run.scorer_name,
        "counts": json.loads(run.counts_json) if run.counts_json else None,
        "reviewed": False,
    }


def _latest_tv_review_runs(
    runs: list[PipelineRun], *, series_id: int | None = None
) -> dict[tuple[str, int | None, int | None], PipelineRun]:
    """Reduce candidate runs to the latest one per (media_type, series_id, season_id)."""
    latest: dict[tuple[str, int | None, int | None], PipelineRun] = {}
    for run in runs:
        if series_id is not None and run.series_id != series_id:
            continue
        key = (run.media_type, run.series_id, run.season_id)
        current = latest.get(key)
        if current is None or (run.started_at or _MIN_DATETIME) > (
            current.started_at or _MIN_DATETIME
        ):
            latest[key] = run
    return latest


def _subjects_awaiting_review(runs: list[PipelineRun]) -> set[tuple[str, int]]:
    """The (media_type, id) subjects whose completed analysis still needs a decision."""
    subjects: set[tuple[str, int]] = set()
    for run in runs:
        if run.media_type == "series" and run.series_id is not None:
            subjects.add(("series", run.series_id))
        elif run.media_type == "season" and run.season_id is not None:
            subjects.add(("season", run.season_id))
    return subjects


async def _tv_review_queue_candidates(db: AsyncSession) -> list[PipelineRun]:
    parent = aliased(Job)
    return (
        (
            await db.execute(
                select(PipelineRun)
                .join(Job, Job.id == PipelineRun.job_id)
                .outerjoin(parent, parent.id == Job.parent_id)
                .where(
                    PipelineRun.media_type.in_(("series", "season")),
                    PipelineRun.feedback_event_id.is_(None),
                    PipelineRun.status.in_(_REVIEW_QUEUE_STATUSES),
                    Job.phase == "terminal",
                    or_(Job.parent_id.is_(None), parent.phase == "terminal"),
                )
            )
        )
        .scalars()
        .all()
    )


def _asset_subject(asset: dict) -> tuple[str, int]:
    if asset["media_type"] == "series":
        return ("series", asset["series_id"])
    return ("season", asset["season_id"])


async def _active_tv_asset_jobs(db: AsyncSession) -> dict[tuple[str, int], Job]:
    """Return TV subjects covered by active single or grouped canonical work."""
    parent = aliased(Job)
    rows = (
        await db.execute(
            select(Job)
            .outerjoin(parent, parent.id == Job.parent_id)
            .where(
                Job.type.in_(("poster_pipeline", "poster_pipeline_group")),
                or_(
                    Job.phase.in_(_ACTIVE_JOB_PHASES),
                    and_(
                        parent.type == "poster_pipeline_tv_batch",
                        parent.phase.in_(_ACTIVE_JOB_PHASES),
                    ),
                ),
            )
            .order_by(Job.created_at.desc())
        )
    ).scalars()
    active: dict[tuple[str, int], Job] = {}
    for job in rows:
        if job.type == "poster_pipeline":
            if job.subject_kind not in ("series", "season") or job.subject_reference is None:
                continue
            try:
                subject_id = int(job.subject_reference)
            except ValueError:
                continue
            active.setdefault((job.subject_kind, subject_id), job)
            continue

        snapshot = job.subject_snapshot if isinstance(job.subject_snapshot, dict) else {}
        members = snapshot.get("members")
        if not isinstance(members, list):
            continue
        for member in members:
            if not isinstance(member, dict):
                continue
            subject_key = member.get("subject_key")
            if isinstance(subject_key, str):
                kind, separator, raw_id = subject_key.partition(":")
                if separator and kind in ("series", "season"):
                    try:
                        subject_id = int(raw_id)
                    except ValueError:
                        continue
                    active.setdefault((kind, subject_id), job)
                    continue
            subject = member.get("subject")
            if not isinstance(subject, dict):
                continue
            kind = subject.get("kind")
            raw_id = subject.get(f"{kind}_id") if kind in ("series", "season") else None
            if isinstance(raw_id, int):
                active.setdefault((kind, raw_id), job)
    return active


def _reused_submission(job: Job) -> SubmissionResult:
    return SubmissionResult(
        job_id=job.id,
        disposition="reused",
        phase=job.phase,
        snapshot_link=f"/api/jobs/{job.id}/snapshot",
        detail_link=f"/projection-room/jobs/{job.id}",
        activity_link=f"/projection-room?view=queue&job={job.id}",
        idempotent=True,
    )


async def _select_uncovered_tv_assets(
    db: AsyncSession, assets: list[dict]
) -> tuple[list[dict], Job | None, set[tuple[str, int]], set[tuple[str, int]]]:
    """Filter active/review-pending subjects and identify an exact active batch reuse."""
    awaiting_review = _subjects_awaiting_review(await _tv_review_queue_candidates(db))
    active_jobs = await _active_tv_asset_jobs(db)
    requested = {_asset_subject(asset) for asset in assets}
    active = requested.intersection(active_jobs)
    review = requested.intersection(awaiting_review)
    eligible = [
        asset
        for asset in assets
        if _asset_subject(asset) not in active and _asset_subject(asset) not in review
    ]

    reused_parent: Job | None = None
    if requested and requested == active:
        parent_ids = {active_jobs[key].parent_id for key in requested}
        if len(parent_ids) == 1 and None not in parent_ids:
            reused_parent = await db.get(Job, parent_ids.pop())
            if reused_parent is not None and (
                reused_parent.type != "poster_pipeline_tv_batch"
                or reused_parent.phase not in _ACTIVE_JOB_PHASES
            ):
                reused_parent = None
    return eligible, reused_parent, active, review


# ---------------------------------------------------------------------------
# Summary + run-queue
# ---------------------------------------------------------------------------


@router.get("/summary")
async def tv_pipeline_summary(db: Annotated[AsyncSession, Depends(get_db)]):
    await _repair_stale_batch_pipeline_runs(db)

    series_rows = (await db.execute(select(Series).where(series_visible()))).scalars().all()
    series_ids = [s.id for s in series_rows]
    season_rows = (
        (
            await db.execute(
                select(Season).where(Season.series_id.in_(series_ids), season_downloaded())
            )
        )
        .scalars()
        .all()
        if series_ids
        else []
    )
    seasons_by_series: dict[int, list[Season]] = defaultdict(list)
    for season in season_rows:
        seasons_by_series[season.series_id].append(season)

    shows_total = len(series_rows)
    shows_with_show_poster = sum(1 for s in series_rows if s.poster_path is not None)
    seasons_total = len(season_rows)
    seasons_with_poster = sum(1 for s in season_rows if s.poster_path is not None)
    shows_fully_covered = sum(
        1
        for s in series_rows
        if s.poster_path is not None
        and all(season.poster_path is not None for season in seasons_by_series.get(s.id, []))
    )

    review_candidates = await _tv_review_queue_candidates(db)
    shows_in_review = len({run.series_id for run in review_candidates if run.series_id is not None})
    awaiting_review = _subjects_awaiting_review(review_candidates)
    seasons_in_review = sum(1 for kind, _ in awaiting_review if kind == "season")
    assets_in_run = len(await _active_tv_asset_jobs(db))
    shows_no_tmdb = sum(1 for s in series_rows if s.tmdb_id is None)

    active_jobs = (
        (
            await db.execute(
                select(Job)
                .where(
                    Job.type == "poster_pipeline_tv_batch",
                    Job.phase.in_(("queued", "running", "stopping")),
                )
                .order_by(Job.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    running_jobs = []
    for job in active_jobs:
        summary = job_summary(job)
        payload = job.request if isinstance(job.request, dict) else {}
        summary["asset_count"] = len(payload.get("assets") or [])
        running_jobs.append(summary)

    heal_schedule = None

    return {
        "shows_total": shows_total,
        "shows_with_show_poster": shows_with_show_poster,
        "shows_missing_show_poster": max(shows_total - shows_with_show_poster, 0),
        "seasons_total": seasons_total,
        "seasons_with_poster": seasons_with_poster,
        "seasons_missing_poster": max(seasons_total - seasons_with_poster, 0),
        "shows_fully_covered": shows_fully_covered,
        "shows_in_review": shows_in_review,
        "seasons_in_review": seasons_in_review,
        "assets_in_review": len(awaiting_review),
        "assets_in_run": assets_in_run,
        "shows_no_tmdb": shows_no_tmdb,
        "running_jobs": running_jobs,
        "last_heal": await latest_poster_heal_summary(db),
        "heal_schedule": heal_schedule,
        "backups": _backup_stats(),
    }


@router.get("/run-queue")
async def tv_run_queue(db: Annotated[AsyncSession, Depends(get_db)]):
    """One row per visible series with at least one asset that still needs a run.

    An asset whose analysis already completed and is sitting in the review queue is
    not waiting on a run — it is waiting on a decision. Listing it here too would
    invite re-running work that is already done, so those assets are withheld until
    the review is resolved.
    """
    awaiting_review = _subjects_awaiting_review(await _tv_review_queue_candidates(db))
    active = set(await _active_tv_asset_jobs(db))
    unavailable = awaiting_review | active
    series_rows = (
        (await db.execute(select(Series).where(series_visible()).order_by(Series.title)))
        .scalars()
        .all()
    )
    series_ids = [s.id for s in series_rows]
    season_rows = (
        (
            await db.execute(
                select(Season).where(Season.series_id.in_(series_ids), season_downloaded())
            )
        )
        .scalars()
        .all()
        if series_ids
        else []
    )
    seasons_by_series: dict[int, list[Season]] = defaultdict(list)
    for season in season_rows:
        seasons_by_series[season.series_id].append(season)

    items = []
    for series in series_rows:
        downloaded_seasons = sorted(
            seasons_by_series.get(series.id, []), key=lambda s: s.season_number
        )
        missing_seasons = [
            s
            for s in downloaded_seasons
            if s.poster_path is None and ("season", s.id) not in unavailable
        ]
        show_poster_missing = (
            series.poster_path is None and ("series", series.id) not in unavailable
        )
        if not show_poster_missing and not missing_seasons:
            continue

        assets_to_run: list[dict] = []
        if show_poster_missing:
            assets_to_run.append({"media_type": "series"})
        for season in missing_seasons:
            assets_to_run.append(
                {"media_type": "season", "season_id": season.id, "number": season.season_number}
            )

        items.append(
            {
                "series": {
                    "id": series.id,
                    "title": series.title,
                    "year": series.year,
                    "tmdb_id": series.tmdb_id,
                    "poster_url": (
                        f"/api/library/series/{series.id}/poster" if series.poster_path else None
                    ),
                },
                "show_poster_missing": show_poster_missing,
                "missing_seasons": [
                    {
                        "season_id": s.id,
                        "number": s.season_number,
                        "episode_file_count": s.episode_file_count,
                    }
                    for s in missing_seasons
                ],
                "assets_to_run": assets_to_run,
                "no_tmdb": series.tmdb_id is None,
            }
        )

    return {"items": items, "total": len(items)}


# ---------------------------------------------------------------------------
# Run triggers
# ---------------------------------------------------------------------------


class TVBatchRunRequest(BaseModel):
    # "missing" (shows/seasons with no poster yet) | "all" | "selected" (series_ids).
    scope: str = "missing"
    series_ids: list[int] | None = None


def _expand_assets(
    series_rows: list[Series], seasons_by_series: dict[int, list[Season]], scope: str
) -> list[dict]:
    assets: list[dict] = []
    for series in series_rows:
        downloaded_seasons = seasons_by_series.get(series.id, [])
        if scope == "missing":
            if series.poster_path is None:
                assets.append({"media_type": "series", "series_id": series.id})
            for season in downloaded_seasons:
                if season.poster_path is None:
                    assets.append(
                        {"media_type": "season", "series_id": series.id, "season_id": season.id}
                    )
        else:  # "all" or "selected" — full expansion (show + every downloaded season)
            assets.append({"media_type": "series", "series_id": series.id})
            for season in downloaded_seasons:
                assets.append(
                    {"media_type": "season", "series_id": series.id, "season_id": season.id}
                )
    return assets


def _poster_member_request(
    asset: dict,
    *,
    series_by_id: dict[int, Series],
    season_by_id: dict[int, Season],
) -> tuple[dict[str, object], str, int]:
    """Freeze one TV member request and its canonical subject identity."""
    series = series_by_id[asset["series_id"]]
    if asset["media_type"] == "series":
        subject_kind = "series"
        subject_id = series.id
        title = series.title
        request_subject: dict[str, int] = {"series_id": series.id}
        reference = f"tv:{series.tmdb_id}"
    else:
        season = season_by_id[asset["season_id"]]
        subject_kind = "season"
        subject_id = season.id
        title = f"{series.title} · Season {season.season_number}"
        request_subject = {"season_id": season.id}
        reference = f"tv:{series.tmdb_id}:season:{season.season_number}"
    return (
        {
            **request_subject,
            "tmdb_id": series.tmdb_id,
            "title": title,
            "source_descriptors": [{"provider": "tmdb", "reference": reference}],
        },
        subject_kind,
        subject_id,
    )


def _tv_group_chunks(
    assets: list[dict],
    *,
    season_by_id: dict[int, Season],
    target_size: int,
) -> list[list[dict]]:
    """Greedily pack whole-show groups, splitting only above the hard limit."""
    by_series: dict[int, list[dict]] = {}
    for asset in assets:
        by_series.setdefault(asset["series_id"], []).append(asset)

    target_size = min(16, max(1, target_size))
    chunks: list[list[dict]] = []
    current: list[dict] = []
    for group in by_series.values():
        ordered = sorted(
            group,
            key=lambda asset: (
                0 if asset["media_type"] == "series" else 1,
                (
                    season_by_id[asset["season_id"]].season_number
                    if asset["media_type"] == "season"
                    else -1
                ),
                asset.get("season_id", -1),
            ),
        )
        if len(ordered) > 16:
            if current:
                chunks.append(current)
                current = []
            chunks.extend(ordered[offset : offset + 16] for offset in range(0, len(ordered), 16))
            continue
        if len(ordered) > target_size:
            if current:
                chunks.append(current)
                current = []
            chunks.append(ordered)
            continue
        if current and len(current) + len(ordered) > target_size:
            chunks.append(current)
            current = []
        current.extend(ordered)
    if current:
        chunks.append(current)
    return chunks


def _poster_child_intents(
    *,
    assets: list[dict],
    series_by_id: dict[int, Series],
    season_by_id: dict[int, Season],
    nonce: str,
    initiator: Initiator,
    priority: int,
    group_enabled: bool = False,
    chunk_size: int = 8,
) -> list[SubmissionIntent]:
    """Freeze TV asset identity and labels before creating canonical children."""
    if group_enabled:
        children: list[SubmissionIntent] = []
        for chunk_index, chunk in enumerate(
            _tv_group_chunks(
                assets,
                season_by_id=season_by_id,
                target_size=chunk_size,
            )
        ):
            members = [
                _poster_member_request(
                    asset,
                    series_by_id=series_by_id,
                    season_by_id=season_by_id,
                )[0]
                for asset in chunk
            ]
            children.append(
                SubmissionIntent(
                    job_type="poster_pipeline_group",
                    request={
                        "library": "tv",
                        "chunk_index": chunk_index,
                        "members": members,
                    },
                    subject=SubjectLocator(
                        kind="poster_subject_group",
                        reference=f"tv-{nonce[:20]}-{chunk_index}",
                    ),
                    trigger=TriggerKind.BATCH,
                    initiator=initiator,
                    idempotency_key=(
                        f"poster_pipeline_group:batch-{nonce}-chunk-{chunk_index}"
                    ),
                    priority=priority,
                )
            )
        return children

    children = []
    for asset in assets:
        request, subject_kind, subject_id = _poster_member_request(
            asset,
            series_by_id=series_by_id,
            season_by_id=season_by_id,
        )
        children.append(
            SubmissionIntent(
                job_type="poster_pipeline",
                request=request,
                subject=SubjectLocator(kind=subject_kind, reference=str(subject_id)),
                trigger=TriggerKind.BATCH,
                initiator=initiator,
                idempotency_key=f"poster_pipeline:batch-{nonce}-{subject_kind}-{subject_id}",
                priority=priority,
            )
        )
    return children


@router.post("/batch", status_code=202)
async def run_tv_pipeline_batch(
    body: TVBatchRunRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> JobSubmissionResponse:
    """Create a ticketless TV poster parent over immutable asset work."""
    scope = body.scope
    if scope not in ("missing", "all", "selected"):
        raise HTTPException(status_code=400, detail=f"unknown scope {scope!r}")
    if scope == "selected" and not body.series_ids:
        raise HTTPException(status_code=400, detail="scope=selected requires series_ids")

    if db.in_transaction():
        await db.commit()
    try:
        async with db.begin():
            query = (
                select(Series)
                .where(series_visible(), Series.tmdb_id.is_not(None))
                .order_by(Series.id)
                .with_for_update()
            )
            if scope == "selected":
                query = query.where(Series.id.in_(body.series_ids))
            series_rows = list((await db.execute(query)).scalars().all())
            series_ids = [series.id for series in series_rows]
            season_rows = (
                list(
                    (
                        await db.execute(
                            select(Season)
                            .where(
                                Season.series_id.in_(series_ids),
                                season_downloaded(),
                            )
                            .order_by(Season.id)
                        )
                    )
                    .scalars()
                    .all()
                )
                if series_ids
                else []
            )
            seasons_by_series: dict[int, list[Season]] = defaultdict(list)
            for season in season_rows:
                seasons_by_series[season.series_id].append(season)

            requested_assets = _expand_assets(series_rows, seasons_by_series, scope)
            if not requested_assets:
                raise HTTPException(status_code=404, detail=f"no assets for scope={scope!r}")
            assets, reused_parent, active, review = await _select_uncovered_tv_assets(
                db, requested_assets
            )
            if reused_parent is not None:
                return submission_response(_reused_submission(reused_parent))
            if not assets:
                reason = "awaiting review" if review and not active else "already active"
                raise HTTPException(status_code=409, detail=f"all selected assets are {reason}")

            cap = pipeline_settings.PIPELINE_BATCH_MAX_MOVIES
            if len(assets) > cap:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"batch of {len(assets)} assets exceeds PIPELINE_BATCH_MAX_MOVIES={cap}"
                    ),
                )

            nonce = uuid4().hex
            initiator = Initiator(kind="system", identifier="pipeline-tv-api")
            effective = configuration_provider.effective("pipeline")
            children = _poster_child_intents(
                assets=assets,
                series_by_id={series.id: series for series in series_rows},
                season_by_id={season.id: season for season in season_rows},
                nonce=nonce,
                initiator=initiator,
                priority=80,
                group_enabled=bool(effective.get("POSTER_GROUP_ENABLED", False)),
                chunk_size=int(effective.get("POSTER_GROUP_CHUNK_SIZE", 8)),
            )
            result = await create_fixed_batch(
                db,
                parent_job_type="poster_pipeline_tv_batch",
                parent_request={"scope": scope, "selection_count": len(assets)},
                scope=BatchScope(
                    reference=nonce,
                    display_name="Poster analysis · television",
                    summary=f"{scope} · {len(assets)} show and season assets",
                ),
                trigger=TriggerKind.BATCH,
                initiator=initiator,
                idempotency_key=f"poster_pipeline_tv_batch:manual-{nonce}",
                children=children,
            )
    except SubmissionError as exc:
        raise HTTPException(status_code=422, detail=exc.code) from exc
    return submission_response(result.parent)


class SeriesRunRequest(BaseModel):
    include: str = "all_missing"  # all_missing | show | seasons
    season_ids: list[int] | None = None


@router.post("/series/{series_id}/run", status_code=202)
async def run_series_pipeline(
    series_id: int,
    body: SeriesRunRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    limiter: Annotated[RateLimiter, Depends(get_rate_limiter)],
) -> JobSubmissionResponse:
    """Create a ticketless parent for one show's selected poster assets."""
    if body.include not in ("all_missing", "show", "seasons"):
        raise HTTPException(status_code=400, detail=f"unknown include {body.include!r}")
    if db.in_transaction():
        await db.commit()
    created = False
    try:
        async with db.begin():
            series = (
                await db.execute(select(Series).where(Series.id == series_id).with_for_update())
            ).scalar_one_or_none()
            if series is None:
                raise HTTPException(status_code=404, detail=f"Series id={series_id} not found")
            if series.tmdb_id is None:
                raise HTTPException(
                    status_code=400,
                    detail=f"Series {series.title!r} has no TMDB ID — run sync",
                )

            downloaded_seasons = list(
                (
                    await db.execute(
                        select(Season)
                        .where(Season.series_id == series_id, season_downloaded())
                        .order_by(Season.id)
                    )
                )
                .scalars()
                .all()
            )
            if not downloaded_seasons:
                raise HTTPException(
                    status_code=400,
                    detail=f"Series {series.title!r} has no downloaded seasons",
                )
            downloaded_by_id = {season.id: season for season in downloaded_seasons}
            requested_assets: list[dict] = []
            if body.include == "all_missing":
                if series.poster_path is None:
                    requested_assets.append({"media_type": "series", "series_id": series.id})
            elif body.include == "show":
                requested_assets.append({"media_type": "series", "series_id": series.id})
            if body.include in ("all_missing", "seasons"):
                requested = set(body.season_ids or ())
                unknown = requested - downloaded_by_id.keys()
                if unknown:
                    raise HTTPException(
                        status_code=400,
                        detail=f"season ids not downloaded/known: {sorted(unknown)}",
                    )
                target_seasons = [
                    season
                    for season in downloaded_seasons
                    if not requested or season.id in requested
                ]
                for season in target_seasons:
                    if body.include == "all_missing" and season.poster_path is not None:
                        continue
                    requested_assets.append(
                        {
                            "media_type": "season",
                            "series_id": series.id,
                            "season_id": season.id,
                        }
                    )
            if not requested_assets:
                raise HTTPException(status_code=400, detail="no assets to run")

            assets, reused_parent, active, review = await _select_uncovered_tv_assets(
                db, requested_assets
            )
            if reused_parent is not None:
                response = submission_response(_reused_submission(reused_parent))
            elif not assets:
                reason = "awaiting review" if review and not active else "already active"
                raise HTTPException(status_code=409, detail=f"all selected assets are {reason}")
            else:
                enforce_rate_limit(
                    limiter,
                    f"pipeline:tv:{series_id}",
                    settings.RATE_PIPELINE_RUN_SECONDS,
                )
                nonce = uuid4().hex
                initiator = Initiator(kind="system", identifier="pipeline-tv-api")
                effective = configuration_provider.effective("pipeline")
                children = _poster_child_intents(
                    assets=assets,
                    series_by_id={series.id: series},
                    season_by_id=downloaded_by_id,
                    nonce=nonce,
                    initiator=initiator,
                    priority=85,
                    group_enabled=bool(effective.get("POSTER_GROUP_ENABLED", False)),
                    chunk_size=int(effective.get("POSTER_GROUP_CHUNK_SIZE", 8)),
                )
                result = await create_fixed_batch(
                    db,
                    parent_job_type="poster_pipeline_tv_batch",
                    parent_request={"scope": "series", "selection_count": len(assets)},
                    scope=BatchScope(
                        reference=nonce,
                        display_name=f"Poster analysis · {series.title}",
                        summary=f"{len(assets)} show and season assets",
                    ),
                    trigger=TriggerKind.BATCH,
                    initiator=initiator,
                    idempotency_key=f"poster_pipeline_tv_batch:series-{series_id}-{nonce}",
                    children=children,
                )
                response = submission_response(result.parent)
                created = True
    except SubmissionError as exc:
        raise HTTPException(status_code=422, detail=exc.code) from exc
    if created:
        limiter.record(f"pipeline:tv:{series_id}")
    return response


# ---------------------------------------------------------------------------
# Review queue
# ---------------------------------------------------------------------------


@router.get("/review-queue")
async def tv_review_queue(
    db: Annotated[AsyncSession, Depends(get_db)],
    page: int = 1,
    page_size: int = 50,
):
    """Latest unreviewed run per TV subject, grouped by series."""
    await _repair_stale_batch_pipeline_runs(db)
    page = max(page, 1)
    page_size = min(max(page_size, 1), 200)

    candidates = await _tv_review_queue_candidates(db)
    latest_by_subject = _latest_tv_review_runs(candidates)

    by_series: dict[int, dict] = defaultdict(lambda: {"show_run": None, "season_runs": []})
    for (media_type, series_id, season_id), run in latest_by_subject.items():
        if series_id is None:
            continue
        if media_type == "series":
            by_series[series_id]["show_run"] = run
        else:
            by_series[series_id]["season_runs"].append((season_id, run))

    series_ids = list(by_series.keys())
    series_rows = (
        (await db.execute(select(Series).where(Series.id.in_(series_ids)))).scalars().all()
        if series_ids
        else []
    )
    series_by_id = {s.id: s for s in series_rows}
    season_ids = [sid for data in by_series.values() for sid, _ in data["season_runs"]]
    season_rows = (
        (await db.execute(select(Season).where(Season.id.in_(season_ids)))).scalars().all()
        if season_ids
        else []
    )
    season_by_id = {s.id: s for s in season_rows}

    items = []
    for series_id, data in by_series.items():
        series = series_by_id.get(series_id)
        if series is None:
            continue
        show_run = data["show_run"]
        season_entries = []
        for season_id, run in sorted(
            data["season_runs"],
            key=lambda t: season_by_id[t[0]].season_number if t[0] in season_by_id else 0,
        ):
            season = season_by_id.get(season_id)
            if season is None:
                continue
            season_entries.append(
                {
                    "season_number": season.season_number,
                    "season_id": season.id,
                    "run": _run_summary(run),
                    "auto_pick_poster_url": (
                        poster_url(run.run_id, run.auto_pick_filename)
                        if run.auto_pick_filename
                        else None
                    ),
                    "flagged_no_candidates": run.status == "flagged_manual",
                }
            )

        show_entry = None
        show_auto_pick_url = None
        if show_run is not None:
            show_auto_pick_url = (
                poster_url(show_run.run_id, show_run.auto_pick_filename)
                if show_run.auto_pick_filename
                else None
            )
            show_entry = {**_run_summary(show_run), "auto_pick_poster_url": show_auto_pick_url}

        display_poster_url = show_auto_pick_url or (
            f"/api/library/series/{series.id}/poster" if series.poster_path else None
        )
        items.append(
            {
                "series": {
                    "id": series.id,
                    "title": series.title,
                    "year": series.year,
                    "tmdb_id": series.tmdb_id,
                },
                "show_run": show_entry,
                "season_runs": season_entries,
                "seasons_only": show_run is None,
                "display_poster_url": display_poster_url,
            }
        )

    items.sort(key=lambda item: item["series"]["title"])
    total_series = len(items)
    start = (page - 1) * page_size
    page_items = items[start : start + page_size]
    return {"total_series": total_series, "items": page_items, "page": page, "page_size": page_size}


class TVReviewApproveAutoRequest(BaseModel):
    deploy: bool = True
    series_id: int | None = None


@router.post("/review-queue/approve-auto")
async def approve_tv_review_queue_auto(
    body: TVReviewApproveAutoRequest,
    request: Request,
    response: Response,
    db: Annotated[AsyncSession, Depends(get_db)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
):
    """Approve every approvable run in the current TV review queue."""
    from marquee.api.routes.feedback import FeedbackRequest, apply_feedback_request  # noqa: PLC0415

    if body.deploy and idempotency_key is None:
        raise HTTPException(status_code=422, detail="Idempotency-Key is required for deploy")
    await _repair_stale_batch_pipeline_runs(db)
    candidates = await _tv_review_queue_candidates(db)
    latest_by_subject = _latest_tv_review_runs(candidates, series_id=body.series_id)

    approved = skipped_no_auto = failed = 0
    errors: list[dict[str, object]] = []
    deployment_jobs: list[dict[str, object]] = []
    for run in latest_by_subject.values():
        if run.status != "completed" or not run.auto_pick_filename:
            skipped_no_auto += 1
            continue
        try:
            result = await apply_feedback_request(
                FeedbackRequest(
                    run_id=run.run_id,
                    action="approve",
                    deploy=body.deploy,
                    idempotency_key=(
                        poster_child_idempotency_key(idempotency_key, run.run_id)
                        if idempotency_key is not None
                        else None
                    ),
                ),
                request,
                db,
            )
            if result.get("deployment_job") is not None:
                deployment_jobs.append(result["deployment_job"])
            approved += 1
        except HTTPException as exc:
            await db.rollback()
            failed += 1
            errors.append({"run_id": run.run_id, "error": exc.detail})
        except Exception:  # noqa: BLE001
            await db.rollback()
            failed += 1
            errors.append({"run_id": run.run_id, "error": "feedback_apply_failed"})

    if body.deploy:
        response.status_code = 202
    return {
        "total": len(latest_by_subject),
        "approved": approved,
        "skipped_no_auto": skipped_no_auto,
        "failed": failed,
        "errors": errors,
        "deployment_jobs": deployment_jobs,
    }


@router.post("/review/reset", status_code=200)
async def reset_tv_review_queue(db: Annotated[AsyncSession, Depends(get_db)]):
    """TV mirror of the movie review reset — marks reviewed + clears poster state."""
    result = await db.execute(
        select(PipelineRun).where(
            PipelineRun.media_type.in_(("series", "season")),
            PipelineRun.feedback_event_id.is_(None),
            PipelineRun.status.in_(_REVIEW_QUEUE_STATUSES),
        )
    )
    review_runs = result.scalars().all()
    if not review_runs:
        return {"reset": 0}

    reset_key = f"review_reset_tv_{int(time.time())}"
    series_ids_to_clear: set[int] = set()
    season_ids_to_clear: set[int] = set()
    for run in review_runs:
        run.feedback_event_id = reset_key
        db.add(
            ArtworkEvent(
                media_type=run.media_type,
                series_id=run.series_id,
                season_id=run.season_id,
                action="review_reset",
                source="manual",
                detail=json.dumps({"run_id": run.run_id, "status": run.status}),
            )
        )
        if run.media_type == "series" and run.series_id is not None:
            series_ids_to_clear.add(run.series_id)
        elif run.media_type == "season" and run.season_id is not None:
            season_ids_to_clear.add(run.season_id)

    series_rows = (
        (
            await db.execute(
                select(Series).where(
                    Series.id.in_(series_ids_to_clear), Series.poster_path.is_not(None)
                )
            )
        )
        .scalars()
        .all()
        if series_ids_to_clear
        else []
    )
    for series in series_rows:
        _clear_poster_columns(series)

    season_rows = (
        (
            await db.execute(
                select(Season).where(
                    Season.id.in_(season_ids_to_clear), Season.poster_path.is_not(None)
                )
            )
        )
        .scalars()
        .all()
        if season_ids_to_clear
        else []
    )
    for season in season_rows:
        _clear_poster_columns(season)

    await db.commit()
    logger.info(
        "TV REVIEW RESET | runs_cleared=%d | posters_reset=%d",
        len(review_runs),
        len(series_rows) + len(season_rows),
    )
    return {
        "reset": len(review_runs),
        "runs_cleared": len(review_runs),
        "posters_reset": len(series_rows) + len(season_rows),
    }


async def _expire_series_run_artifacts(db: AsyncSession, run_ids: list[str]) -> int:
    """Mark this series' pipeline evidence due for collection.

    Scoped by the ``run_id`` stamped into each artifact's metadata, never by
    ``job_id`` — one TV group job carries many series, and expiring by job would
    take other shows' evidence with it. The bytes are removed by the nightly
    ``job_retention_purge``; this only brings their expiry forward.
    """
    if not run_ids:
        return 0
    now = datetime.now(UTC)
    artifacts = (
        (
            await db.execute(
                select(JobArtifact).where(
                    JobArtifact.status == "available",
                    JobArtifact.kind.in_(("evidence_image", "command_report")),
                    JobArtifact.artifact_metadata["run_id"].as_string().in_(run_ids),
                )
            )
        )
        .scalars()
        .all()
    )
    for artifact in artifacts:
        artifact.expires_at = now
    return len(artifacts)


async def _reset_series_scope(
    db: AsyncSession,
    series_list: list[Series],
    *,
    idempotency_key: str,
    scope_name: str,
) -> JobSubmissionResponse:
    """Start these shows over: drop their posters and runs so they re-enter the run queue.

    Clearing the poster columns alone would not hold — the next library sync
    re-detects the file on disk and fills them straight back in. So this goes
    through ``poster_reset``, which deletes the deployed file (keeping the
    backup), clears the columns, and closes the pending review runs that were
    judging it.
    """
    series_ids = [series.id for series in series_list]
    seasons = (
        (
            await db.execute(
                select(Season)
                .where(Season.series_id.in_(series_ids), season_downloaded())
                .order_by(Season.series_id, Season.season_number)
            )
        )
        .scalars()
        .all()
        if series_ids
        else []
    )
    subjects: list[tuple[str, int]] = [("series", series_id) for series_id in series_ids]
    subjects.extend(("season", season.id) for season in seasons)

    runs = (
        (
            await db.execute(
                select(PipelineRun).where(
                    or_(
                        and_(
                            PipelineRun.media_type == "series",
                            PipelineRun.series_id.in_(series_ids),
                        ),
                        and_(
                            PipelineRun.media_type == "season",
                            PipelineRun.season_id.in_([season.id for season in seasons]),
                        ),
                    ),
                    PipelineRun.status != "running",
                )
            )
        )
        .scalars()
        .all()
        if series_ids
        else []
    )

    if db.in_transaction():
        await db.commit()
    try:
        async with db.begin():
            result = await create_poster_parent(
                db,
                parent_job_type="poster_deploy_reset",
                idempotency_key=idempotency_key,
                trigger=TriggerKind.BATCH,
                initiator=Initiator(kind="system", identifier="pipeline-tv-api"),
                priority=40,
                subjects=subjects,
                scope_name=scope_name[:200],
            )
            expired = await _expire_series_run_artifacts(db, [run.run_id for run in runs])
            # Closing the undecided runs is this endpoint's job, not the child's.
            # ``poster_reset`` only retires reviews on the path where it actually
            # deletes a file, and a show awaiting review usually has no deployed
            # poster at all — nothing is deployed until someone approves. Leaving
            # it to the child would strand exactly those shows: no poster to
            # delete, so the review stays open, so the run queue keeps
            # withholding them and they sit in Review forever.
            reset_key = f"poster_reset_{int(time.time())}"
            closed = 0
            for run in runs:
                if run.feedback_event_id is None:
                    run.feedback_event_id = reset_key
                    closed += 1
                db.add(
                    ArtworkEvent(
                        media_type=run.media_type,
                        series_id=run.series_id,
                        season_id=run.season_id,
                        action="series_reset",
                        source="manual",
                        detail=json.dumps({"run_id": run.run_id, "status": run.status}),
                    )
                )
    except (SubmissionError, ValueError) as exc:
        logger.warning("TV RESET REJECTED | scope=%s | %s", scope_name, exc)
        raise HTTPException(status_code=422, detail="poster_reset_scope_invalid") from exc
    logger.info(
        "TV RESET | scope=%s | series=%d | subjects=%d | runs=%d | closed=%d | artifacts_expired=%d",
        scope_name,
        len(series_ids),
        len(subjects),
        len(runs),
        closed,
        expired,
    )
    return submission_response(result.parent)


@router.post("/series/{series_id}/reset", status_code=202)
async def reset_series_posters(
    series_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
) -> JobSubmissionResponse:
    """Start one show over, returning it to the run queue."""
    series = (await db.execute(select(Series).where(Series.id == series_id))).scalar_one_or_none()
    if series is None:
        raise HTTPException(status_code=404, detail=f"Series id={series_id} not found")
    return await _reset_series_scope(
        db,
        [series],
        idempotency_key=idempotency_key,
        scope_name=f"Reset posters — {series.title}",
    )


@router.post("/review-queue/reset", status_code=202)
async def reset_tv_review_queue_posters(
    db: Annotated[AsyncSession, Depends(get_db)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
) -> JobSubmissionResponse:
    """Start every show awaiting review over — the counterpart to approve-auto.

    Scoped to the shows on the review queue, so a show that was already decided
    keeps the poster its decision deployed.
    """
    candidates = await _tv_review_queue_candidates(db)
    series_ids = sorted(
        {run.series_id for run in _latest_tv_review_runs(list(candidates)).values() if run.series_id}
    )
    if not series_ids:
        raise HTTPException(status_code=409, detail="No TV runs are awaiting review")
    series_list = list(
        (
            await db.execute(select(Series).where(Series.id.in_(series_ids)).order_by(Series.title))
        )
        .scalars()
        .all()
    )
    return await _reset_series_scope(
        db,
        series_list,
        idempotency_key=idempotency_key,
        scope_name=f"Reset posters — {len(series_list)} shows in review",
    )


# ---------------------------------------------------------------------------
# D9 — "use show poster" fallback for a season with no rankable candidates
# ---------------------------------------------------------------------------


@router.post("/seasons/{season_id}/use-show-poster", status_code=202)
async def use_show_poster_for_season(
    season_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
):
    from marquee.core.poster_subjects import PosterSubject  # noqa: PLC0415

    season = (await db.execute(select(Season).where(Season.id == season_id))).scalar_one_or_none()
    if season is None:
        raise HTTPException(status_code=404, detail=f"Season id={season_id} not found")
    if not await db.scalar(
        select(exists(select(Season.id).where(Season.id == season_id, season_downloaded())))
    ):
        raise HTTPException(status_code=400, detail="Season has no downloaded episodes")

    series = (await db.execute(select(Series).where(Series.id == season.series_id))).scalar_one()
    if series.poster_path is None:
        raise HTTPException(
            status_code=409, detail=f"Series {series.title!r} has no deployed show poster"
        )

    show_subject = PosterSubject.from_series(series)
    try:
        candidate = subject_artwork_selection(
            show_subject,
            selection_facts={"reason": "show_poster_fallback", "season_id": season_id},
        )
        result = await submit_poster_leaf(
            db,
            job_type="poster_deploy",
            target_kind="season",
            target_id=season_id,
            request={
                "target_kind": "season",
                "target_id": season_id,
                "candidate": candidate.model_dump(mode="json"),
                "ai_selected": False,
                "user_approved": True,
            },
            idempotency_key=idempotency_key,
            initiator="season-show-poster",
        )
        await db.commit()
    except IdempotencyConflictError as exc:
        raise HTTPException(status_code=409, detail=exc.api_detail) from exc
    except SubmissionError as exc:
        raise HTTPException(status_code=422, detail=exc.code) from exc
    except PosterSelectionError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return submission_response(result)


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------


@router.get("/metrics")
async def tv_pipeline_metrics(
    db: Annotated[AsyncSession, Depends(get_db)],
    limit: int = 500,
):
    limit = min(max(limit, 1), 5000)
    runs = (
        (
            await db.execute(
                select(PipelineRun)
                .where(PipelineRun.media_type != "movie")
                .order_by(PipelineRun.started_at.desc())
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    return aggregate_run_metrics(runs)


# ---------------------------------------------------------------------------
# Per-series history (shared /api/series prefix)
# ---------------------------------------------------------------------------


@series_router.get("/{series_id}/runs")
async def list_series_runs(
    series_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Run history for a series (show + season runs), newest first."""
    await _repair_stale_batch_pipeline_runs(db)
    runs = (
        (
            await db.execute(
                select(PipelineRun)
                .where(PipelineRun.series_id == series_id)
                .order_by(PipelineRun.started_at.desc())
            )
        )
        .scalars()
        .all()
    )
    season_ids = [r.season_id for r in runs if r.season_id is not None]
    season_rows = (
        (await db.execute(select(Season).where(Season.id.in_(season_ids)))).scalars().all()
        if season_ids
        else []
    )
    season_number_by_id = {s.id: s.season_number for s in season_rows}
    return {
        "series_id": series_id,
        "runs": [
            {
                "run_id": r.run_id,
                "status": r.status,
                "media_type": r.media_type,
                "season_id": r.season_id,
                "season_number": season_number_by_id.get(r.season_id) if r.season_id else None,
                "started_at": r.started_at.isoformat() if r.started_at else None,
                "completed_at": r.completed_at.isoformat() if r.completed_at else None,
                "scorer_name": r.scorer_name,
                "counts": json.loads(r.counts_json) if r.counts_json else None,
                "reviewed": r.feedback_event_id is not None,
            }
            for r in runs
        ],
    }


@series_router.get("/{series_id}/artwork-events")
async def list_series_artwork_events(
    series_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Deploy/restore history for a series and its seasons, newest first."""
    season_ids = (
        (await db.execute(select(Season.id).where(Season.series_id == series_id))).scalars().all()
    )
    conditions = [ArtworkEvent.series_id == series_id]
    if season_ids:
        conditions.append(ArtworkEvent.season_id.in_(season_ids))
    events = (
        (
            await db.execute(
                select(ArtworkEvent)
                .where(or_(*conditions))
                .order_by(ArtworkEvent.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    return {
        "series_id": series_id,
        "events": [
            {
                "id": e.id,
                "action": e.action,
                "source": e.source,
                "media_type": e.media_type,
                "season_id": e.season_id,
                "detail": json.loads(e.detail) if e.detail else None,
                "created_at": e.created_at.isoformat() if e.created_at else None,
            }
            for e in events
        ],
    }
