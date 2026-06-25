"""Radarr Overlay read model for HDR, custom formats, and preference targets."""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
from typing import Annotated, Any
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from marquee.api.library_serializers import enrich_movie, resolution_label
from marquee.api.routes.jobs import job_summary
from marquee.api.routes.library import _coverage_by_media_file
from marquee.core.dovi_analysis import conversion_eligibility
from marquee.core.jobs import job_manager
from marquee.core.jobs.manager import TERMINAL
from marquee.core.media_files import ensure_media_file_for_movie
from marquee.core.radarr_overlay import (
    classify_custom_format_tags,
    classify_hdr_tags,
    default_profile_preference,
    distribution_keys,
    is_valid_preference_pair,
    ordered_tags,
    overlay_bucket,
    preference_rank,
    preference_status,
    preference_target_choices,
    profile_hdr_targets,
)
from marquee.core.sort_title import sort_title
from marquee.database import get_db
from marquee.media import binaries
from marquee.models import (
    DoviState,
    Job,
    LetterboxState,
    MediaFile,
    Movie,
    RadarrCustomFormat,
    RadarrOverlayProfilePreference,
    RadarrProfileFormatItem,
    RadarrQualityProfile,
)

router = APIRouter(prefix="/api/hdr", tags=["hdr"])

PREFERENCE_STATUS_SORT = {
    "no_hdr_target": -1,
    "below_target": 0,
    "meets_target": 1,
    "exceeds_target": 2,
}
HDR_DISTRIBUTION_ORDER = (
    "sdr",
    "hdr",
    "hdr10",
    "hdr10p",
    "dovi",
    "dovi_no_fallback",
)


class ProfilePreferenceUpdate(BaseModel):
    profile_id: int
    meet_target: str | None = None
    exceed_target: str | None = None
    excluded_targets: list[str] | None = None


class ProfilePreferencesUpdatePayload(BaseModel):
    profiles: list[ProfilePreferenceUpdate]


def _parse_hdr_tags(hdr: str | None, hdr_tags: list[str] | None) -> list[str]:
    values: list[str] = []
    for raw in hdr_tags or []:
        values.extend(part.strip() for part in raw.split(",") if part.strip())
    if hdr:
        values.append(hdr)
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _distribution(items: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = dict.fromkeys(HDR_DISTRIBUTION_ORDER, 0)
    for item in items:
        for key in item["distribution_keys"]:
            counts[key] += 1
    return counts


def _filter_item(
    item: dict[str, Any],
    *,
    selected_tags: list[str],
    profile_id: int | None,
    cf_score_min: int | None,
    cf_score_max: int | None,
    preference_status_value: str | None,
    dovi_no_fallback: bool | None,
) -> bool:
    if selected_tags:
        item_values = set(item["distribution_keys"]) | set(item["hdr_tags"])
        if not any(tag in item_values for tag in selected_tags):
            return False
    if profile_id is not None and item["profile_id"] != profile_id:
        return False

    cf_score = item["cf_score"]
    if cf_score_min is not None and (cf_score is None or cf_score < cf_score_min):
        return False
    if cf_score_max is not None and (cf_score is None or cf_score > cf_score_max):
        return False

    if preference_status_value and item["preference_status"] != preference_status_value:
        return False
    return not (dovi_no_fallback is True and not item["dovi_no_fallback"])


def _sort_title_value(item: dict[str, Any]) -> str:
    return sort_title(item["title"]).lower()


def _sort_cf_score_value(item: dict[str, Any]) -> int:
    return item["cf_score"] if item["cf_score"] is not None else -(10**9)


def _sort_items(items: list[dict[str, Any]], sort_by: str, sort_dir: str) -> list[dict[str, Any]]:
    reverse = sort_dir == "desc"
    if sort_by == "year":
        return sorted(
            items, key=lambda item: (item["year"], _sort_title_value(item)), reverse=reverse
        )
    if sort_by == "cf_score":
        return sorted(
            items,
            key=lambda item: (
                _sort_cf_score_value(item),
                item["cf_cutoff"] or -(10**9),
                _sort_title_value(item),
            ),
            reverse=reverse,
        )
    if sort_by == "preference_status":
        return sorted(
            items,
            key=lambda item: (
                PREFERENCE_STATUS_SORT.get(item["preference_status"], -(10**9)),
                _sort_cf_score_value(item),
                _sort_title_value(item),
            ),
            reverse=reverse,
        )
    return sorted(items, key=_sort_title_value, reverse=reverse)


async def _load_profile_context(
    db: AsyncSession,
) -> tuple[
    dict[int, RadarrQualityProfile],
    dict[int, list[str]],
    dict[int, dict[str, Any]],
]:
    profile_rows = (await db.execute(select(RadarrQualityProfile))).scalars().all()
    profile_items_rows = (await db.execute(select(RadarrProfileFormatItem))).scalars().all()
    cf_rows = (await db.execute(select(RadarrCustomFormat))).scalars().all()
    preference_rows = (await db.execute(select(RadarrOverlayProfilePreference))).scalars().all()

    profiles_by_id = {profile.id: profile for profile in profile_rows}
    profile_items_by_profile: dict[int, list[RadarrProfileFormatItem]] = defaultdict(list)
    for row in profile_items_rows:
        profile_items_by_profile[row.profile_id].append(row)

    cf_classifications = {
        row.id: classify_custom_format_tags(row.name, row.specifications_json) for row in cf_rows
    }
    profile_targets_by_id = {
        profile_id: ordered_tags(profile_hdr_targets(items, cf_classifications))
        for profile_id, items in profile_items_by_profile.items()
    }
    stored_preferences = {row.profile_id: row for row in preference_rows}

    preference_summary_by_profile: dict[int, dict[str, Any]] = {}
    for profile_id, profile in profiles_by_id.items():
        profile_targets = profile_targets_by_id.get(profile_id, [])
        available_targets = preference_target_choices(profile_targets)
        if not available_targets:
            continue

        stored = stored_preferences.get(profile_id)
        if stored and (
            (stored.meet_target is None or stored.meet_target in available_targets)
            and (stored.exceed_target is None or stored.exceed_target in available_targets)
            and is_valid_preference_pair(stored.meet_target, stored.exceed_target)
        ):
            meet_target = stored.meet_target
            exceed_target = stored.exceed_target
            excluded_targets = stored.excluded_targets or []
        else:
            meet_target, exceed_target = default_profile_preference(available_targets)
            excluded_targets = []

        preference_summary_by_profile[profile_id] = {
            "profile_id": profile_id,
            "profile_name": profile.name,
            "profile_targets": profile_targets,
            "available_preference_targets": available_targets,
            "meet_target": meet_target,
            "exceed_target": exceed_target,
            "excluded_targets": excluded_targets,
        }

    return profiles_by_id, profile_targets_by_id, preference_summary_by_profile


def _serialize_item(item: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in item.items() if key != "distribution_keys"}


@router.get("")
async def hdr_index(
    db: Annotated[AsyncSession, Depends(get_db)],
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    hdr: str | None = Query(None, description="Legacy single-tag filter"),
    hdr_tags: Annotated[
        list[str] | None,
        Query(description="Repeated or comma-separated HDR tags"),
    ] = None,
    cf_score_min: int | None = Query(None, ge=0),
    cf_score_max: int | None = Query(None, ge=0),
    profile_id: int | None = Query(None, ge=1),
    preference_status_value: str | None = Query(
        None,
        alias="preference_status",
        description="below_target | meets_target | exceeds_target | no_hdr_target",
    ),
    dovi_no_fallback: bool | None = Query(None),
    sort_by: str = Query("cf_score", description="title | cf_score | preference_status"),
    sort_dir: str = Query("desc", description="asc | desc"),
):
    """Return the Radarr Overlay page data."""
    movie_rows = (
        await db.execute(
            select(Movie, LetterboxState)
            .outerjoin(LetterboxState, LetterboxState.movie_id == Movie.id)
            .where(Movie.movie_file_path.is_not(None), Movie.movie_file_path != "")
        )
    ).all()
    movies = [movie for movie, _ in movie_rows]
    movie_ids = [movie.id for movie in movies]

    media_rows = (
        (
            await db.execute(
                select(MediaFile).where(
                    MediaFile.movie_id.in_(movie_ids),
                    MediaFile.is_active.is_(True),
                )
            )
        )
        .scalars()
        .all()
        if movie_ids
        else []
    )
    media_by_movie = {media.movie_id: media for media in media_rows}
    coverage = await _coverage_by_media_file(db, [media.id for media in media_rows])

    (
        profiles_by_id,
        profile_targets_by_id,
        preference_summary_by_profile,
    ) = await _load_profile_context(db)

    all_items: list[dict[str, Any]] = []
    for movie, lb in movie_rows:
        media = media_by_movie.get(movie.id)
        base = enrich_movie(
            movie,
            media,
            coverage.get(media.id) if media else None,
            lb.status if lb else None,
        )
        hdr_tags_for_movie = ordered_tags(classify_hdr_tags(movie.hdr_type_raw))
        preference_summary = preference_summary_by_profile.get(movie.quality_profile_id or -1)
        profile_targets = profile_targets_by_id.get(movie.quality_profile_id or -1, [])
        item = {
            **base,
            "hdr": overlay_bucket(movie.hdr_type_raw)
            if overlay_bucket(movie.hdr_type_raw) != "unknown"
            else base["hdr"],
            "hdr_tags": hdr_tags_for_movie,
            "distribution_keys": distribution_keys(movie.hdr_type_raw),
            "dovi_no_fallback": "dovi_no_fallback" in hdr_tags_for_movie,
            "profile_id": movie.quality_profile_id,
            "profile_name": profiles_by_id.get(movie.quality_profile_id).name
            if movie.quality_profile_id in profiles_by_id
            else None,
            "cf_score": movie.current_cf_score,
            "cf_cutoff": profiles_by_id.get(movie.quality_profile_id).cutoff_format_score
            if movie.quality_profile_id in profiles_by_id
            else None,
            "cutoff_met": movie.quality_cutoff_met,
            "profile_targets": profile_targets,
            "available_preference_targets": (
                preference_summary["available_preference_targets"] if preference_summary else []
            ),
            "meet_target": preference_summary["meet_target"] if preference_summary else None,
            "exceed_target": preference_summary["exceed_target"] if preference_summary else None,
            "preference_status": preference_status(
                file_tags=set(hdr_tags_for_movie),
                profile_targets=set(profile_targets),
                meet_target=preference_summary["meet_target"] if preference_summary else None,
                exceed_target=preference_summary["exceed_target"] if preference_summary else None,
                excluded_targets=preference_summary["excluded_targets"] if preference_summary else None,
            ),
        }
        all_items.append(item)

    selected_tags = _parse_hdr_tags(hdr, hdr_tags)
    filtered = [
        item
        for item in all_items
        if _filter_item(
            item,
            selected_tags=selected_tags,
            profile_id=profile_id,
            cf_score_min=cf_score_min,
            cf_score_max=cf_score_max,
            preference_status_value=preference_status_value,
            dovi_no_fallback=dovi_no_fallback,
        )
    ]
    ordered = _sort_items(filtered, sort_by, sort_dir)

    overlay_profile_ids = {
        item["profile_id"]
        for item in all_items
        if item["profile_id"] is not None and item["profile_id"] in profiles_by_id
    }

    total = len(ordered)
    start = (page - 1) * page_size
    end = start + page_size

    return {
        "distribution": _distribution(all_items),
        "distribution_order": list(HDR_DISTRIBUTION_ORDER),
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": [_serialize_item(item) for item in ordered[start:end]],
        "profiles": [
            {
                "id": profile.id,
                "name": profile.name,
                "cutoff_format_score": profile.cutoff_format_score,
            }
            for profile in sorted(
                (
                    profile
                    for profile in profiles_by_id.values()
                    if profile.id in overlay_profile_ids
                ),
                key=lambda profile: profile.name.lower(),
            )
        ],
        "profile_preferences": [
            preference_summary_by_profile[profile_id]
            for profile_id in sorted(
                (
                    profile_id
                    for profile_id in preference_summary_by_profile
                    if profile_id in overlay_profile_ids
                ),
                key=lambda current_profile_id: profiles_by_id[current_profile_id].name.lower(),
            )
        ],
        "applied_filters": {
            "hdr_tags": selected_tags,
            "cf_score_min": cf_score_min,
            "cf_score_max": cf_score_max,
            "profile_id": profile_id,
            "preference_status": preference_status_value,
            "dovi_no_fallback": dovi_no_fallback,
            "sort_by": sort_by,
            "sort_dir": sort_dir,
        },
    }


@router.put("/preferences")
async def put_profile_preferences(
    payload: ProfilePreferencesUpdatePayload,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Persist per-quality-profile meet/exceed preference targets."""
    if not payload.profiles:
        raise HTTPException(status_code=400, detail="No profile preferences provided")

    profiles_by_id, _, preference_summary_by_profile = await _load_profile_context(db)
    now = datetime.now(UTC)
    profile_ids = {profile.profile_id for profile in payload.profiles}
    existing_rows = {
        row.profile_id: row
        for row in (
            await db.execute(
                select(RadarrOverlayProfilePreference).where(
                    RadarrOverlayProfilePreference.profile_id.in_(profile_ids)
                )
            )
        )
        .scalars()
        .all()
    }

    applied: list[int] = []
    for update in payload.profiles:
        summary = preference_summary_by_profile.get(update.profile_id)
        if summary is None or update.profile_id not in profiles_by_id:
            raise HTTPException(
                status_code=400,
                detail=f"Profile {update.profile_id} has no editable HDR preference targets",
            )

        available_targets = set(summary["available_preference_targets"])
        if update.meet_target is not None and update.meet_target not in available_targets:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid meet_target for profile {update.profile_id}",
            )
        if update.exceed_target is not None and update.exceed_target not in available_targets:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid exceed_target for profile {update.profile_id}",
            )
        if not is_valid_preference_pair(update.meet_target, update.exceed_target):
            raise HTTPException(
                status_code=400,
                detail=f"exceed_target must be stricter than meet_target for profile {update.profile_id}",
            )
        if update.exceed_target is not None and update.meet_target is not None and preference_rank(
            update.exceed_target
        ) <= preference_rank(update.meet_target):
            raise HTTPException(
                status_code=400,
                detail=f"exceed_target must rank above meet_target for profile {update.profile_id}",
            )

        row = existing_rows.get(update.profile_id)
        if row is None:
            row = RadarrOverlayProfilePreference(profile_id=update.profile_id)
            db.add(row)
        row.meet_target = update.meet_target
        row.exceed_target = update.exceed_target
        row.excluded_targets = update.excluded_targets
        row.updated_at = now
        applied.append(update.profile_id)

    await db.commit()
    return {"applied_profile_ids": applied}


# ---------------------------------------------------------------------------
# Per-movie Dolby Vision detail + analysis
# ---------------------------------------------------------------------------


class DoviAnalyzeBatchRequest(BaseModel):
    movie_ids: list[int] | None = None


async def _load_movie(db: AsyncSession, movie_id: int) -> Movie:
    movie = (await db.execute(select(Movie).where(Movie.id == movie_id))).scalar_one_or_none()
    if movie is None:
        raise HTTPException(status_code=404, detail=f"Movie id={movie_id} not found")
    return movie


async def _active_dovi_job(db: AsyncSession, movie_id: int) -> Job | None:
    return (
        await db.execute(
            select(Job)
            .where(
                Job.type == "dovi_analyze",
                Job.subject_type == "movie",
                Job.subject_id == str(movie_id),
                Job.status.notin_(tuple(TERMINAL)),
            )
            .order_by(Job.created_at.desc(), Job.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


def _dovi_state_to_dict(state: DoviState | None) -> dict[str, Any] | None:
    if state is None:
        return None
    return {
        "status": state.status,
        "profile": state.dovi_profile,
        "level": state.dovi_level,
        "el_present": state.el_present,
        "el_type": state.el_type,
        "bl_signal_compatibility_id": state.bl_signal_compatibility_id,
        "source_codec": state.source_codec,
        "rpu_summary": state.rpu_summary_json,
        "error_reason": state.error_reason,
        "conversion": conversion_eligibility(state.dovi_profile, state.el_type),
        "last_analyzed_at": state.last_analyzed_at.isoformat()
        if state.last_analyzed_at
        else None,
    }


def _movie_detail_dict(movie: Movie) -> dict[str, Any]:
    return {
        "id": movie.id,
        "title": movie.title,
        "year": movie.year,
        "tmdb_id": movie.tmdb_id,
        "radarr_id": movie.radarr_id,
        "movie_file_path": movie.movie_file_path,
        "container": movie.container,
        "resolution": resolution_label(movie.video_width, movie.video_height),
        "has_hdr": movie.has_hdr,
        "has_dv": movie.has_dv,
        "hdr_type_raw": movie.hdr_type_raw,
        "quality_profile_id": movie.quality_profile_id,
    }


@router.get("/{movie_id}")
async def hdr_movie_detail(
    movie_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Per-movie HDR + Dolby Vision detail for the /hdr/[id] page."""
    movie = await _load_movie(db, movie_id)
    state = (
        await db.execute(select(DoviState).where(DoviState.movie_id == movie_id))
    ).scalar_one_or_none()
    profile = (
        await db.execute(
            select(RadarrQualityProfile).where(
                RadarrQualityProfile.id == movie.quality_profile_id
            )
        )
    ).scalar_one_or_none() if movie.quality_profile_id is not None else None
    hdr_tags = ordered_tags(classify_hdr_tags(movie.hdr_type_raw))
    active = await _active_dovi_job(db, movie_id)
    return {
        "movie": _movie_detail_dict(movie),
        "profile_name": profile.name if profile else None,
        "hdr_tags": hdr_tags,
        "hdr_bucket": overlay_bucket(movie.hdr_type_raw),
        "dovi": _dovi_state_to_dict(state),
        "binaries": {
            "dovi_tool": binaries.resolve("dovi_tool") is not None,
            "ffprobe": binaries.resolve("ffprobe") is not None,
        },
        "analysis_job": job_summary(active) if active else None,
    }


@router.post("/{movie_id}/analyze")
async def analyze_movie_dovi(
    movie_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Enqueue a single-movie DoVi analysis job; returns its job summary."""
    if binaries.resolve("ffprobe") is None:
        raise HTTPException(
            status_code=503,
            detail="ffprobe not found on PATH — install it to analyze Dolby Vision.",
        )
    movie = await _load_movie(db, movie_id)
    active = await _active_dovi_job(db, movie.id)
    if active is not None:
        return job_summary(active)
    media_file = await ensure_media_file_for_movie(db, movie)
    file_lock = {f"media-file:{media_file.id}": 1} if media_file is not None else {}
    job = await job_manager.create(
        db,
        job_type="dovi_analyze",
        payload={"movie_id": movie.id},
        priority=70,
        resources={"media_read": 1, **file_lock},
        subject_type="movie",
        subject_id=movie.id,
    )
    return job_summary(job)


@router.post("/analyze", status_code=202)
async def analyze_dovi_batch(
    body: DoviAnalyzeBatchRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Analyze every Radarr-known Dolby Vision movie (optionally a subset).

    Only movies Radarr flagged as DoVi (``has_dv``) with a media file are
    enqueued — there's no point probing files we already know are SDR/HDR10.
    """
    if binaries.resolve("ffprobe") is None:
        raise HTTPException(
            status_code=503,
            detail="ffprobe not found on PATH — install it to analyze Dolby Vision.",
        )
    rows = (
        await db.execute(
            select(Movie).where(
                Movie.has_dv.is_(True),
                Movie.movie_file_path.is_not(None),
                Movie.movie_file_path != "",
            )
        )
    ).scalars().all()
    if body.movie_ids:
        wanted = set(body.movie_ids)
        rows = [movie for movie in rows if movie.id in wanted]
    if not rows:
        raise HTTPException(
            status_code=400, detail="No Dolby Vision movies with a media file to analyze"
        )

    children: list[dict[str, Any]] = []
    for movie in rows:
        media_file = await ensure_media_file_for_movie(db, movie)
        if media_file is None:
            continue
        children.append(
            {
                "job_type": "dovi_analyze",
                "payload": {"movie_id": movie.id},
                "priority": 60,
                "resources": {"media_read": 1, f"media-file:{media_file.id}": 1},
                "subject_type": "movie",
                "subject_id": movie.id,
            }
        )
    if not children:
        raise HTTPException(
            status_code=400, detail="No Dolby Vision movies with a resolvable media file"
        )

    batch, _children = await job_manager.create_batch(
        db,
        parent_type="dovi_analyze_batch",
        parent_payload={"movie_ids": [movie.id for movie in rows]},
        parent_priority=60,
        parent_subject_type="dovi_batch",
        parent_subject_id=uuid4().hex,
        children=children,
    )
    return {
        "job_id": batch.id,
        "total": len(children),
        "events_url": f"/api/jobs/{batch.id}/events",
    }
