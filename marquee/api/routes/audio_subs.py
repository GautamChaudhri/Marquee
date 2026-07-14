"""Audio/subtitle landing + TV coverage API."""

from __future__ import annotations

import json
from collections import defaultdict
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from marquee.api.job_submission import submission_response
from marquee.api.routes.subtitle_generators import list_generators as list_generators_route
from marquee.core.audio_subs_rollups import (
    EpisodeCoverage,
    episode_status,
    resolve_episode_coverage,
    season_rollup,
    show_rollup,
)
from marquee.core.configuration import (
    ConfigurationError,
    ConfigurationVersionConflictError,
    update_configuration,
)
from marquee.core.configuration_cache import configuration_provider
from marquee.core.jobs.manager import job_manager
from marquee.core.jobs.submission import Initiator, SubmissionError
from marquee.core.subtitles import coverage as subtitle_coverage
from marquee.core.subtitles import generation
from marquee.core.subtitles.config import subtitle_settings
from marquee.core.subtitles.scan_batch import create_subtitle_scan_batch
from marquee.core.tv_queries import series_visible
from marquee.database import get_db
from marquee.models import (
    Episode,
    EpisodeMediaFile,
    MediaFile,
    Movie,
    Series,
    SubtitleInventory,
    SubtitlePolicy,
)

router = APIRouter(prefix="/api/audio-subs", tags=["audio-subs"])

_ACTIVE_MEDIA_STATUSES = {"planned", "queued", "running"}


class DeepScanRequest(BaseModel):
    scope: Literal["movies", "tv", "all"] = "all"


class TvDeepScanRequest(BaseModel):
    season_number: int | None = None


class PreferencesRequest(BaseModel):
    expected_version: int
    preferred_languages: list[str] | None = None
    preferred_audio_languages: list[str] | None = None
    preferred_subtitle_languages: list[str] | None = None


class SeriesPreferencesRequest(BaseModel):
    preferred_audio_languages: list[str] | None = None
    preferred_subtitle_languages: list[str] | None = None


class TvGenerateRequest(BaseModel):
    season_number: int | None = None
    language_hint: str | None = None
    output: str = "external"
    task: Literal["transcribe", "translate"] = "transcribe"
    stream_index: int | None = None


def _parse_json(value: Any) -> dict:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value:
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return {}
    return {}


def _effective_series_languages(series: Series) -> tuple[list[str], list[str]]:
    return subtitle_coverage.effective_preferred_languages(
        preferred_languages=subtitle_settings.SUBTITLE_PREFERRED_LANGUAGES,
        preferred_audio_languages=series.preferred_audio_languages_json
        if series.preferred_audio_languages_json is not None
        else subtitle_settings.SUBTITLE_PREFERRED_AUDIO_LANGUAGES,
        preferred_subtitle_languages=series.preferred_subtitle_languages_json
        if series.preferred_subtitle_languages_json is not None
        else subtitle_settings.SUBTITLE_PREFERRED_SUBTITLE_LANGUAGES,
    )


async def _active_media_jobs(db: AsyncSession) -> dict[int, dict[str, list[str]]]:
    # The legacy MediaJob lifecycle was removed with the canonical schema; no
    # subtitle scan/generate work can be active until the family is remigrated.
    return {}


async def _load_tv_rows(db: AsyncSession, *, series_id: int | None = None):
    stmt = (
        select(Episode, Series, EpisodeMediaFile.media_file_id, SubtitleInventory.coverage_json)
        .join(Series, Series.id == Episode.series_id)
        .outerjoin(EpisodeMediaFile, EpisodeMediaFile.episode_id == Episode.id)
        .outerjoin(SubtitleInventory, SubtitleInventory.media_file_id == EpisodeMediaFile.media_file_id)
        .where(Episode.episode_file_path.is_not(None))
        .order_by(Series.title, Episode.season_number, Episode.episode_number)
    )
    if series_id is not None:
        stmt = stmt.where(Episode.series_id == series_id)
    return (await db.execute(stmt)).all()


async def _show_items(db: AsyncSession) -> list[dict]:
    rows = await _load_tv_rows(db)
    active_jobs = await _active_media_jobs(db)
    series_map: dict[int, dict] = {}
    season_coverages: dict[int, dict[int, list[tuple[Episode, EpisodeCoverage, int | None]]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for episode, series, media_file_id, coverage_json in rows:
        series_map[series.id] = {"series": series}
        cov = resolve_episode_coverage(
            episode_id=episode.id,
            season_number=episode.season_number,
            synced_audio_languages=episode.audio_languages_json,
            synced_subtitle_languages=episode.subtitle_languages_json,
            probed_coverage=_parse_json(coverage_json) or None,
        )
        season_coverages[series.id][episode.season_number].append((episode, cov, media_file_id))

    items: list[dict] = []
    for series_id, payload in series_map.items():
        series = payload["series"]
        preferred_audio, preferred_subs = _effective_series_languages(series)
        per_season = {
            number: season_rollup(
                [cov for _episode, cov, _media_file_id in entries],
                preferred_audio,
                preferred_subs,
            )
            for number, entries in season_coverages[series_id].items()
        }
        rollup = show_rollup(per_season)
        episode_total = rollup["episodes_total"]
        active_scan_jobs = sorted(
            {
                job_id
                for entries in season_coverages[series_id].values()
                for _episode, _cov, media_file_id in entries
                for job_id in active_jobs.get(media_file_id or -1, {}).get("scan", [])
            }
        )
        active_generate_jobs = sorted(
            {
                job_id
                for entries in season_coverages[series_id].values()
                for _episode, _cov, media_file_id in entries
                for job_id in active_jobs.get(media_file_id or -1, {}).get("generate", [])
            }
        )
        items.append(
            {
                "series_id": series.id,
                "title": series.title,
                "year": series.year,
                "rollup": rollup,
                "missing_languages": rollup["missing_languages"],
                "dub_coverage": rollup["dub_coverage"],
                "uniformity": rollup["uniformity"],
                "episode_fraction": f"{rollup['episodes_counted']}/{episode_total}" if episode_total else "0/0",
                "active_scan_job_ids": active_scan_jobs,
                "active_generation_job_ids": active_generate_jobs,
            }
        )
    return items


@router.get("/summary")
async def summary(db: Annotated[AsyncSession, Depends(get_db)], request: Request):
    movie_rows = (
        await db.execute(
            select(Movie.id, SubtitleInventory.coverage_json)
            .outerjoin(MediaFile, MediaFile.movie_id == Movie.id)
            .outerjoin(SubtitleInventory, SubtitleInventory.media_file_id == MediaFile.id)
        )
    ).all()
    movies = {
        "total": len({row.id for row in movie_rows}),
        "audio_ok": 0,
        "audio_gap": 0,
        "subtitle_ok": 0,
        "subtitle_gap": 0,
        "both_gap": 0,
        "unknown": 0,
        "forced_coverage": 0,
        "sdh_coverage": 0,
        "unknown_language_tracks": 0,
        "generated_tracks": 0,
    }
    for _movie_id, coverage_json in movie_rows:
        coverage = _parse_json(coverage_json)
        if not coverage:
            movies["unknown"] += 1
            continue
        audio_missing = bool(coverage.get("missing_preferred_audio_languages"))
        sub_missing = bool(coverage.get("missing_preferred_subtitle_languages"))
        movies["audio_ok" if not audio_missing else "audio_gap"] += 1
        movies["subtitle_ok" if not sub_missing else "subtitle_gap"] += 1
        if audio_missing and sub_missing:
            movies["both_gap"] += 1
        if coverage.get("forced_only_languages"):
            movies["forced_coverage"] += 1
        if coverage.get("sdh_languages"):
            movies["sdh_coverage"] += 1
        movies["unknown_language_tracks"] += int(coverage.get("unknown_language_track_count", 0))
        movies["generated_tracks"] += int(coverage.get("generated_track_count", 0))

    show_items = await _show_items(db)
    tv_episode = {
        "audio_ok": 0,
        "audio_gap": 0,
        "subtitle_ok": 0,
        "subtitle_gap": 0,
        "both_gap": 0,
        "unknown": 0,
        "forced_coverage": 0,
        "sdh_coverage": 0,
    }
    show_status_counts: dict[str, int] = defaultdict(int)
    uniformity_counts: dict[str, int] = defaultdict(int)
    highlights = []
    for item in show_items:
        rollup = item["rollup"]
        counts = rollup.get("status_counts", {})
        tv_episode["audio_ok"] += counts.get("ok", 0) + counts.get("subtitle_gap", 0)
        tv_episode["audio_gap"] += counts.get("audio_gap", 0) + counts.get("both_gap", 0)
        tv_episode["subtitle_ok"] += counts.get("ok", 0) + counts.get("audio_gap", 0)
        tv_episode["subtitle_gap"] += counts.get("subtitle_gap", 0) + counts.get("both_gap", 0)
        tv_episode["both_gap"] += counts.get("both_gap", 0)
        tv_episode["unknown"] += rollup.get("unknown_count", 0)
        tv_episode["forced_coverage"] += rollup.get("forced_coverage", 0)
        tv_episode["sdh_coverage"] += rollup.get("sdh_coverage", 0)
        show_status_counts[rollup["status"]] += 1
        uniformity_counts[rollup["uniformity"]] += 1
        if rollup["missing_audio_languages"]:
            highlights.append(
                {
                    "series_id": item["series_id"],
                    "title": item["title"],
                    "missing_audio_languages": rollup["missing_audio_languages"],
                    "coverage": rollup["dub_coverage"],
                }
            )
    # Deep-scan scheduling returns with the canonical PgQueuer scheduler.
    schedule = None
    pending_file_count = (
        await db.execute(select(SubtitleInventory).where(SubtitleInventory.file_signature.is_(None)))
    ).scalars().all()
    policies = (await db.execute(select(SubtitlePolicy))).scalars().all()
    return {
        "movies": movies,
        "tv": {
            **tv_episode,
            "show_status_counts": dict(show_status_counts),
            "uniformity_counts": dict(uniformity_counts),
            "dub_coverage_highlights": highlights[:5],
        },
        "preferred": {
            "audio": subtitle_settings.effective_preferred_audio_languages,
            "subtitles": subtitle_settings.effective_preferred_subtitle_languages,
            "shared": subtitle_settings.SUBTITLE_PREFERRED_LANGUAGES,
        },
        "policies": {
            "active_count": len(policies),
            "last_audit_summary": None,
        },
        "generator": (await list_generators_route(request))["generators"],
        "deep_scan": {
            "enabled": subtitle_settings.AUDIO_SUBS_DEEP_SCAN_ENABLED,
            "hour": subtitle_settings.AUDIO_SUBS_DEEP_SCAN_HOUR,
            "last_run_at": schedule.last_run_at.isoformat() if schedule and schedule.last_run_at else None,
            "pending_file_count": len(pending_file_count),
        },
    }


@router.get("/tv")
async def tv_index(
    db: Annotated[AsyncSession, Depends(get_db)],
    status: str | None = None,
    uniformity: str | None = None,
    missing_language: str | None = None,
    q: str | None = None,
    sort_by: Literal["title", "status", "coverage"] = "title",
):
    items = await _show_items(db)
    filtered = []
    for item in items:
        if status and item["rollup"]["status"] != status:
            continue
        if uniformity and item["rollup"]["uniformity"] != uniformity:
            continue
        if missing_language and missing_language not in item["missing_languages"]:
            continue
        if q and q.lower() not in item["title"].lower():
            continue
        filtered.append(item)
    if sort_by == "status":
        filtered.sort(key=lambda item: (item["rollup"]["status"], item["title"].lower()))
    elif sort_by == "coverage":
        filtered.sort(
            key=lambda item: (
                item["rollup"]["dub_coverage"]["ok"] / max(1, item["rollup"]["dub_coverage"]["of"]),
                item["title"].lower(),
            )
        )
    else:
        filtered.sort(key=lambda item: item["title"].lower())
    return {
        "total": len(filtered),
        "items": filtered,
        "applied_filters": {
            "status": status,
            "uniformity": uniformity,
            "missing_language": missing_language,
            "q": q,
            "sort_by": sort_by,
        },
    }


@router.get("/tv/{series_id}")
async def tv_detail(series_id: int, db: Annotated[AsyncSession, Depends(get_db)]):
    series = (await db.execute(select(Series).where(Series.id == series_id, series_visible()))).scalar_one_or_none()
    if series is None:
        raise HTTPException(status_code=404, detail=f"Series id={series_id} not found")
    rows = await _load_tv_rows(db, series_id=series_id)
    preferred_audio, preferred_subs = _effective_series_languages(series)
    active_jobs = await _active_media_jobs(db)
    seasons_payload: list[dict] = []
    by_season: dict[int, list[tuple[Episode, EpisodeCoverage, int | None]]] = defaultdict(list)
    for episode, _series, media_file_id, coverage_json in rows:
        cov = resolve_episode_coverage(
            episode_id=episode.id,
            season_number=episode.season_number,
            synced_audio_languages=episode.audio_languages_json,
            synced_subtitle_languages=episode.subtitle_languages_json,
            probed_coverage=_parse_json(coverage_json) or None,
        )
        by_season[episode.season_number].append((episode, cov, media_file_id))
    season_rollups = {
        number: season_rollup([cov for _episode, cov, _mf in entries], preferred_audio, preferred_subs)
        for number, entries in by_season.items()
    }
    for season_number in sorted(by_season):
        episodes = []
        season_scan_jobs: set[str] = set()
        season_generation_jobs: set[str] = set()
        for episode, cov, media_file_id in by_season[season_number]:
            if media_file_id is not None:
                season_scan_jobs.update(active_jobs.get(media_file_id, {}).get("scan", []))
                season_generation_jobs.update(active_jobs.get(media_file_id, {}).get("generate", []))
            episodes.append(
                {
                    "episode_id": episode.id,
                    "code": f"s{episode.season_number:02d}e{episode.episode_number:02d}",
                    "title": episode.title,
                    "audio_languages": cov.audio_languages,
                    "subtitle_languages": cov.subtitle_languages,
                    "forced_languages": cov.forced_only_languages if cov.tier == "probed" else [],
                    "sdh_languages": cov.sdh_languages if cov.tier == "probed" else [],
                    "tier": cov.tier,
                    "status": episode_status(cov, preferred_audio, preferred_subs),
                    "media_file_id": media_file_id,
                }
            )
        seasons_payload.append(
            {
                "season_number": season_number,
                "rollup": season_rollups[season_number],
                "episodes": episodes,
                "active_scan_job_ids": sorted(season_scan_jobs),
                "active_generation_job_ids": sorted(season_generation_jobs),
            }
        )
    return {
        "series": {"id": series.id, "title": series.title, "year": series.year},
        "preferred_audio_languages": preferred_audio,
        "preferred_subtitle_languages": preferred_subs,
        "rollup": show_rollup(season_rollups),
        "seasons": seasons_payload,
    }


@router.post("/tv/{series_id}/deep-scan", status_code=202)
async def tv_deep_scan(
    series_id: int,
    body: TvDeepScanRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    series = (await db.execute(select(Series).where(Series.id == series_id, series_visible()))).scalar_one_or_none()
    if series is None:
        raise HTTPException(status_code=404, detail=f"Series id={series_id} not found")
    try:
        async with db.begin():
            result = await create_subtitle_scan_batch(
                db,
                parent_job_type="subtitle_scan_all",
                scope="series",
                force=False,
                series_id=series_id,
                season_number=body.season_number,
                initiator=Initiator(kind="system", identifier="subtitle-scan-api"),
            )
    except SubmissionError as exc:
        raise HTTPException(status_code=422, detail=exc.code) from exc
    return submission_response(result.parent)


@router.post("/deep-scan", status_code=202)
async def deep_scan(
    body: DeepScanRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    try:
        async with db.begin():
            result = await create_subtitle_scan_batch(
                db,
                parent_job_type="subtitle_scan_all",
                scope=body.scope,
                force=False,
                initiator=Initiator(kind="system", identifier="subtitle-scan-api"),
            )
    except SubmissionError as exc:
        raise HTTPException(status_code=422, detail=exc.code) from exc
    return submission_response(result.parent)


@router.put("/preferences")
async def update_preferences(
    body: PreferencesRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    updates = {
        key: value
        for key, value in {
            "SUBTITLE_PREFERRED_LANGUAGES": body.preferred_languages,
            "SUBTITLE_PREFERRED_AUDIO_LANGUAGES": body.preferred_audio_languages,
            "SUBTITLE_PREFERRED_SUBTITLE_LANGUAGES": body.preferred_subtitle_languages,
        }.items()
        if value is not None
    }
    try:
        state, changed = await update_configuration(
            db,
            expected_version=body.expected_version,
            updates=updates,
            actor={"kind": "api", "id": "audio-subs"},
            trigger="audio_subs_preferences_api",
        )
    except ConfigurationVersionConflictError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail={
                "code": "configuration_version_conflict",
                "current_version": exc.current.version,
                "etag": exc.current.etag,
            },
        ) from exc
    except ConfigurationError as exc:
        await db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await db.commit()
    await configuration_provider.refresh_from_session(db)
    return {
        "ok": True,
        "configuration_version": state.version,
        "etag": state.etag,
        "changed": changed,
    }


@router.put("/tv/{series_id}/preferences")
async def update_series_preferences(
    series_id: int,
    body: SeriesPreferencesRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    series = (await db.execute(select(Series).where(Series.id == series_id))).scalar_one_or_none()
    if series is None:
        raise HTTPException(status_code=404, detail=f"Series id={series_id} not found")
    series.preferred_audio_languages_json = body.preferred_audio_languages
    series.preferred_subtitle_languages_json = body.preferred_subtitle_languages
    await db.commit()
    return {"ok": True}


@router.post("/tv/{series_id}/generate", status_code=202)
async def generate_tv(
    series_id: int,
    body: TvGenerateRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    series = (await db.execute(select(Series).where(Series.id == series_id, series_visible()))).scalar_one_or_none()
    if series is None:
        raise HTTPException(status_code=404, detail=f"Series id={series_id} not found")
    stmt = (
        select(EpisodeMediaFile.media_file_id)
        .join(Episode, Episode.id == EpisodeMediaFile.episode_id)
        .where(Episode.series_id == series_id, Episode.episode_file_path.is_not(None))
    )
    if body.season_number is not None:
        stmt = stmt.where(Episode.season_number == body.season_number)
    media_file_ids = sorted(set((await db.execute(stmt)).scalars().all()))
    if not media_file_ids:
        raise HTTPException(status_code=400, detail="No episode media files found for generation")
    generation.validate_generation_request(body.task, generation.current_subgen_model())
    batch, _children = await job_manager.create_batch(
        db,
        parent_type="subtitle_generate_batch",
        parent_payload={"series_id": series_id, **body.model_dump()},
        media_file_ids=media_file_ids,
    )
    return {
        "job_id": batch.id,
        "total": len(media_file_ids),
        "status_url": f"/api/jobs/{batch.id}/snapshot",
    }
