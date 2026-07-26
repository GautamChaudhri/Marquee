"""TV pipeline routes — run triggers, review queue, series history (design 04 §10).

Mirrors ``marquee/api/routes/pipeline.py``'s movie endpoints. TV producers
seal ticketless ``poster_pipeline_tv_batch`` control parents whose show and
season assets execute as canonical, non-deploying ``poster_pipeline`` children.
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
from sqlalchemy import exists, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

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
from marquee.core.jobs.batches import BatchScope, create_fixed_batch
from marquee.core.jobs.contracts import TriggerKind
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
)
from marquee.core.pipeline_config import pipeline_settings
from marquee.core.rate_limit import RateLimiter
from marquee.core.tv_queries import season_downloaded, series_visible
from marquee.database import get_db
from marquee.models import ArtworkEvent, Job, PipelineRun, Season, Series

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/pipeline/tv", tags=["pipeline-tv"])
series_router = APIRouter(prefix="/api/series", tags=["series"])

_REVIEW_QUEUE_STATUSES = {"completed", "flagged_manual"}
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


async def _tv_review_queue_candidates(db: AsyncSession) -> list[PipelineRun]:
    return (
        (
            await db.execute(
                select(PipelineRun).where(
                    PipelineRun.media_type.in_(("series", "season")),
                    PipelineRun.feedback_event_id.is_(None),
                    PipelineRun.status.in_(_REVIEW_QUEUE_STATUSES),
                )
            )
        )
        .scalars()
        .all()
    )


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
        "running_jobs": running_jobs,
        "last_heal": await latest_poster_heal_summary(db),
        "heal_schedule": heal_schedule,
        "backups": _backup_stats(),
    }


@router.get("/run-queue")
async def tv_run_queue(db: Annotated[AsyncSession, Depends(get_db)]):
    """One row per visible series with at least one missing asset."""
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
        missing_seasons = [s for s in downloaded_seasons if s.poster_path is None]
        show_poster_missing = series.poster_path is None
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


def _poster_child_intents(
    *,
    assets: list[dict],
    series_by_id: dict[int, Series],
    season_by_id: dict[int, Season],
    nonce: str,
    initiator: Initiator,
    priority: int,
) -> list[SubmissionIntent]:
    """Freeze TV asset identity and labels before creating canonical children."""
    children: list[SubmissionIntent] = []
    for asset in assets:
        series = series_by_id[asset["series_id"]]
        if asset["media_type"] == "series":
            subject_kind = "series"
            subject_id = series.id
            title = series.title
            request_subject = {"series_id": series.id}
            reference = f"tv:{series.tmdb_id}"
        else:
            season = season_by_id[asset["season_id"]]
            subject_kind = "season"
            subject_id = season.id
            title = f"{series.title} · Season {season.season_number}"
            request_subject = {"season_id": season.id}
            reference = f"tv:{series.tmdb_id}:season:{season.season_number}"
        children.append(
            SubmissionIntent(
                job_type="poster_pipeline",
                request={
                    **request_subject,
                    "tmdb_id": series.tmdb_id,
                    "title": title,
                    "source_descriptors": [{"provider": "tmdb", "reference": reference}],
                },
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
    """Create a ticketless TV poster parent with one immutable child per asset."""
    scope = body.scope
    if scope not in ("missing", "all", "selected"):
        raise HTTPException(status_code=400, detail=f"unknown scope {scope!r}")
    if scope == "selected" and not body.series_ids:
        raise HTTPException(status_code=400, detail="scope=selected requires series_ids")

    query = select(Series).where(series_visible(), Series.tmdb_id.is_not(None))
    if scope == "selected":
        query = query.where(Series.id.in_(body.series_ids))
    series_rows = list((await db.execute(query.order_by(Series.id))).scalars().all())
    series_ids = [series.id for series in series_rows]
    season_rows = (
        list(
            (
                await db.execute(
                    select(Season).where(
                        Season.series_id.in_(series_ids),
                        season_downloaded(),
                    )
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

    assets = _expand_assets(series_rows, seasons_by_series, scope)
    if not assets:
        raise HTTPException(status_code=404, detail=f"no assets for scope={scope!r}")
    cap = pipeline_settings.PIPELINE_BATCH_MAX_MOVIES
    if len(assets) > cap:
        raise HTTPException(
            status_code=400,
            detail=f"batch of {len(assets)} assets exceeds PIPELINE_BATCH_MAX_MOVIES={cap}",
        )

    nonce = uuid4().hex
    initiator = Initiator(kind="system", identifier="pipeline-tv-api")
    children = _poster_child_intents(
        assets=assets,
        series_by_id={series.id: series for series in series_rows},
        season_by_id={season.id: season for season in season_rows},
        nonce=nonce,
        initiator=initiator,
        priority=80,
    )
    if db.in_transaction():
        await db.commit()
    try:
        async with db.begin():
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
    series = (await db.execute(select(Series).where(Series.id == series_id))).scalar_one_or_none()
    if series is None:
        raise HTTPException(status_code=404, detail=f"Series id={series_id} not found")
    if not await db.scalar(
        select(exists(select(Series.id).where(Series.id == series_id, series_visible())))
    ):
        raise HTTPException(
            status_code=400, detail=f"Series {series.title!r} has no downloaded seasons"
        )
    if series.tmdb_id is None:
        raise HTTPException(
            status_code=400, detail=f"Series {series.title!r} has no TMDB ID — run sync"
        )
    if body.include not in ("all_missing", "show", "seasons"):
        raise HTTPException(status_code=400, detail=f"unknown include {body.include!r}")

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
    downloaded_by_id = {season.id: season for season in downloaded_seasons}
    assets: list[dict] = []
    if body.include == "all_missing":
        if series.poster_path is None:
            assets.append({"media_type": "series", "series_id": series.id})
    elif body.include == "show":
        assets.append({"media_type": "series", "series_id": series.id})
    if body.include in ("all_missing", "seasons"):
        requested = set(body.season_ids or ())
        unknown = requested - downloaded_by_id.keys()
        if unknown:
            raise HTTPException(
                status_code=400,
                detail=f"season ids not downloaded/known: {sorted(unknown)}",
            )
        target_seasons = [
            season for season in downloaded_seasons if not requested or season.id in requested
        ]
        for season in target_seasons:
            if body.include == "all_missing" and season.poster_path is not None:
                continue
            assets.append({"media_type": "season", "series_id": series.id, "season_id": season.id})
    if not assets:
        raise HTTPException(status_code=400, detail="no assets to run")

    enforce_rate_limit(limiter, f"pipeline:tv:{series_id}", settings.RATE_PIPELINE_RUN_SECONDS)
    nonce = uuid4().hex
    initiator = Initiator(kind="system", identifier="pipeline-tv-api")
    children = _poster_child_intents(
        assets=assets,
        series_by_id={series.id: series},
        season_by_id=downloaded_by_id,
        nonce=nonce,
        initiator=initiator,
        priority=85,
    )
    if db.in_transaction():
        await db.commit()
    try:
        async with db.begin():
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
    except SubmissionError as exc:
        raise HTTPException(status_code=422, detail=exc.code) from exc
    limiter.record(f"pipeline:tv:{series_id}")
    return submission_response(result.parent)


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
