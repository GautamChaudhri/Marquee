"""TV pipeline routes — run triggers, review queue, series history (design 04 §10).

Mirrors ``marquee/api/routes/pipeline.py``'s movie endpoints. Every TV
execution goes through ``poster_pipeline_tv_batch`` (batch_runner); there is
no single-asset TV run type, since even one show or one season is "a batch
of one" through the shared batch engine.
"""

from __future__ import annotations

import json
import logging
import time
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import exists, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from marquee.api.deps import enforce_rate_limit, get_rate_limiter
from marquee.api.results import poster_url
from marquee.api.routes.jobs import job_summary
from marquee.api.routes.pipeline import (
    _backup_stats,
    _repair_stale_batch_pipeline_runs,
    aggregate_run_metrics,
)
from marquee.config import settings
from marquee.core.heal import latest_heal_summary
from marquee.core.jobs import job_manager
from marquee.core.jobs.manager import ACTIVE
from marquee.core.pipeline_config import pipeline_settings
from marquee.core.rate_limit import RateLimiter
from marquee.core.tv_queries import season_downloaded, series_visible
from marquee.database import get_db
from marquee.models import ArtworkEvent, Job, JobSchedule, PipelineRun, Season, Series
from marquee.pipeline.run_manager import run_manager

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
                .where(Job.type == "poster_pipeline_tv_batch", Job.status.in_(ACTIVE))
                .order_by(Job.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    running_jobs = []
    for job in active_jobs:
        summary = job_summary(job)
        payload = job.payload if isinstance(job.payload, dict) else {}
        summary["asset_count"] = len(payload.get("assets") or [])
        running_jobs.append(summary)

    schedule = await db.get(JobSchedule, "poster-heal")
    heal_schedule = (
        {
            "enabled": schedule.enabled,
            "interval_minutes": schedule.interval_seconds // 60,
            "next_run_at": schedule.next_run_at.isoformat() if schedule.next_run_at else None,
        }
        if schedule
        else None
    )

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
        "last_heal": await latest_heal_summary(db),
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


def _expand_assets(series_rows: list[Series], seasons_by_series: dict[int, list[Season]], scope: str) -> list[dict]:
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


@router.post("/batch", status_code=202)
async def run_tv_pipeline_batch(
    body: TVBatchRunRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Enqueue one stage-batched TV run.

    ``scope="missing"``: series with a null show poster get a series asset,
    and each downloaded season with a null poster gets a season asset.
    ``scope="all"``: every asset (show + all downloaded seasons) for every
    visible series with a TMDB id. ``scope="selected"``: the same full
    expansion as "all", restricted to ``series_ids`` — the operator explicitly
    picked these shows, so re-running one refreshes everything, not just
    what's missing.
    """
    scope = body.scope
    if scope not in ("missing", "all", "selected"):
        raise HTTPException(status_code=400, detail=f"unknown scope {scope!r}")
    if scope == "selected" and not body.series_ids:
        raise HTTPException(status_code=400, detail="scope=selected requires series_ids")

    query = select(Series).where(series_visible(), Series.tmdb_id.is_not(None))
    if scope == "selected":
        query = query.where(Series.id.in_(body.series_ids))
    series_rows = (await db.execute(query.order_by(Series.id))).scalars().all()
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

    assets = _expand_assets(series_rows, seasons_by_series, scope)
    if not assets:
        raise HTTPException(status_code=404, detail=f"no assets for scope={scope!r}")
    cap = pipeline_settings.PIPELINE_BATCH_MAX_MOVIES
    if len(assets) > cap:
        raise HTTPException(
            status_code=400,
            detail=f"batch of {len(assets)} assets exceeds PIPELINE_BATCH_MAX_MOVIES={cap}",
        )

    job = await job_manager.create(
        db,
        job_type="poster_pipeline_tv_batch",
        payload={"assets": assets, "scope": scope},
        priority=80,
        resources={"gpu": 1, "network_external": 1},
        subject_type="pipeline_tv_batch",
        subject_id=scope,
        max_attempts=1,
        idempotency_key=f"poster-tv-batch:{scope}:{int(time.time() // 30)}",
    )
    response = job_summary(job)
    response["asset_count"] = len(assets)
    response["series_count"] = len({a["series_id"] for a in assets})
    return response


class SeriesRunRequest(BaseModel):
    include: str = "all_missing"  # all_missing | show | seasons
    season_ids: list[int] | None = None


@router.post("/series/{series_id}/run", status_code=202)
async def run_series_pipeline(
    series_id: int,
    body: SeriesRunRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    limiter: Annotated[RateLimiter, Depends(get_rate_limiter)],
):
    """Enqueue a stage-batched run for one series (show and/or its seasons)."""
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

    downloaded_seasons = (
        (await db.execute(select(Season).where(Season.series_id == series_id, season_downloaded())))
        .scalars()
        .all()
    )
    downloaded_by_id = {s.id: s for s in downloaded_seasons}

    assets: list[dict] = []
    if body.include == "all_missing":
        if series.poster_path is None:
            assets.append({"media_type": "series", "series_id": series.id})
    elif body.include == "show":
        assets.append({"media_type": "series", "series_id": series.id})
    if body.include in ("all_missing", "seasons"):
        if body.season_ids:
            requested = set(body.season_ids)
            unknown = requested - downloaded_by_id.keys()
            if unknown:
                raise HTTPException(
                    status_code=400,
                    detail=f"season ids not downloaded/known: {sorted(unknown)}",
                )
            target_seasons = [downloaded_by_id[sid] for sid in requested]
        else:
            target_seasons = list(downloaded_seasons)
        for season in target_seasons:
            if body.include == "all_missing" and season.poster_path is not None:
                continue
            assets.append({"media_type": "season", "series_id": series.id, "season_id": season.id})

    if not assets:
        raise HTTPException(status_code=400, detail="no assets to run")

    enforce_rate_limit(limiter, f"pipeline:tv:{series_id}", settings.RATE_PIPELINE_RUN_SECONDS)
    limiter.record(f"pipeline:tv:{series_id}")
    job = await job_manager.create(
        db,
        job_type="poster_pipeline_tv_batch",
        payload={"assets": assets, "scope": f"series:{series_id}"},
        priority=85,
        resources={"gpu": 1, "network_external": 1},
        subject_type="pipeline_tv_batch",
        subject_id=str(series_id),
        max_attempts=1,
        idempotency_key=f"poster-tv-series:{series_id}:{int(time.time() // settings.RATE_PIPELINE_RUN_SECONDS)}",
    )
    response = job_summary(job)
    response["asset_count"] = len(assets)
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
            archive = run_manager.load_archive(run.run_id, run.archive_path)
            official_pick = archive.get("official_pick") if archive is not None else None
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
                    "official_pick": official_pick,
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
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Approve every approvable run in the current TV review queue."""
    from marquee.api.routes.feedback import FeedbackRequest, apply_feedback_request  # noqa: PLC0415

    await _repair_stale_batch_pipeline_runs(db)
    candidates = await _tv_review_queue_candidates(db)
    latest_by_subject = _latest_tv_review_runs(candidates, series_id=body.series_id)

    approved = skipped_no_auto = failed = 0
    errors: list[dict[str, object]] = []
    for run in latest_by_subject.values():
        if run.status != "completed" or not run.auto_pick_filename:
            skipped_no_auto += 1
            continue
        try:
            await apply_feedback_request(
                FeedbackRequest(run_id=run.run_id, action="approve", deploy=body.deploy),
                request,
                db,
            )
            approved += 1
        except HTTPException as exc:
            await db.rollback()
            failed += 1
            errors.append({"run_id": run.run_id, "error": exc.detail})
        except Exception as exc:  # noqa: BLE001
            await db.rollback()
            failed += 1
            errors.append({"run_id": run.run_id, "error": str(exc)})

    return {
        "total": len(latest_by_subject),
        "approved": approved,
        "skipped_no_auto": skipped_no_auto,
        "failed": failed,
        "errors": errors,
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


@router.post("/seasons/{season_id}/use-show-poster")
async def use_show_poster_for_season(
    season_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    from marquee.core.poster_service import poster_service  # noqa: PLC0415
    from marquee.core.poster_subjects import PosterSubject  # noqa: PLC0415

    season = (await db.execute(select(Season).where(Season.id == season_id))).scalar_one_or_none()
    if season is None:
        raise HTTPException(status_code=404, detail=f"Season id={season_id} not found")
    if not await db.scalar(
        select(exists(select(Season.id).where(Season.id == season_id, season_downloaded())))
    ):
        raise HTTPException(status_code=400, detail="Season has no downloaded episodes")

    series = (
        await db.execute(select(Series).where(Series.id == season.series_id))
    ).scalar_one()
    if series.poster_path is None:
        raise HTTPException(
            status_code=409, detail=f"Series {series.title!r} has no deployed show poster"
        )

    show_subject = PosterSubject.from_series(series)
    source_file: Path | None = None
    backup = show_subject.backup_file()
    if backup.is_file():
        source_file = backup
    else:
        cache_paths = show_subject.cache_paths()
        if cache_paths and cache_paths[0].is_file():
            source_file = cache_paths[0]
        elif Path(series.poster_path).is_file():
            source_file = Path(series.poster_path)
    if source_file is None:
        raise HTTPException(status_code=404, detail="No source bytes available for the show poster")

    season_subject = PosterSubject.from_season(season, series)
    result = await poster_service.deploy(
        db,
        season_subject,
        source_file,
        source="show_poster_fallback",
        ai_selected=False,
        user_approved=True,
        poster_source=series.poster_source,
        poster_source_url=series.poster_source_url,
    )

    latest_run = (
        (
            await db.execute(
                select(PipelineRun)
                .where(
                    PipelineRun.media_type == "season",
                    PipelineRun.season_id == season_id,
                    PipelineRun.feedback_event_id.is_(None),
                    PipelineRun.status.in_(_REVIEW_QUEUE_STATUSES),
                )
                .order_by(PipelineRun.started_at.desc())
            )
        )
        .scalars()
        .first()
    )
    if latest_run is not None:
        latest_run.feedback_event_id = f"show_poster_fallback_{int(time.time())}"
        await db.commit()

    return {
        "deployed_path": result.deployed_path,
        "cache_path": result.cache_path,
        "sha256": result.sha256,
        "backup_path": result.backup_path,
    }


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
        (await db.execute(select(Season.id).where(Season.series_id == series_id)))
        .scalars()
        .all()
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
