"""Radarr Overlay read model for HDR, custom formats, and preference targets."""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from marquee.api.library_serializers import enrich_movie
from marquee.api.routes.library import _coverage_by_media_file
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
from marquee.models import (
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
    "hdr",
    "hdr10",
    "hdr10p",
    "dovi",
    "dovi_no_fallback",
    "sdr",
    "unknown",
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
    counts = dict.fromkeys(HDR_DISTRIBUTION_ORDER, 0)
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
