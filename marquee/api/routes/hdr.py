"""Radarr Overlay read model for HDR, custom formats, and profile targets."""

from __future__ import annotations

from collections import defaultdict
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from marquee.api.library_serializers import enrich_movie
from marquee.api.routes.library import _coverage_by_media_file
from marquee.core.pipeline_config import pipeline_settings
from marquee.core.radarr_overlay import (
    classify_custom_format_tags,
    classify_hdr_tags,
    distribution_keys,
    hdr_target_status,
    movie_cf_score,
    ordered_tags,
    overlay_bucket,
    profile_hdr_targets,
)
from marquee.database import get_db
from marquee.models import (
    LetterboxState,
    MediaFile,
    Movie,
    MovieCustomFormatScore,
    RadarrCustomFormat,
    RadarrProfileFormatItem,
    RadarrQualityProfile,
)

router = APIRouter(prefix="/api/hdr", tags=["hdr"])

HDR_TARGET_SORT = {
    "met_target": 0,
    "below_target": 1,
    "no_hdr_target": 2,
    "no_file": 3,
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
    hdr_target_status_value: str | None,
    dovi_no_fallback: bool | None,
) -> bool:
    if selected_tags:
        item_values = set(item["distribution_keys"]) | set(item["hdr_tags"])
        if not any(tag in item_values for tag in selected_tags):
            return False
    if profile_id is not None and item["profile_id"] != profile_id:
        return False
    if cf_score_min is not None and item["cf_score"] < cf_score_min:
        return False
    if cf_score_max is not None and item["cf_score"] > cf_score_max:
        return False
    if hdr_target_status_value and item["hdr_target_status"] != hdr_target_status_value:
        return False
    return not (dovi_no_fallback is True and not item["dovi_no_fallback"])


def _sort_items(items: list[dict[str, Any]], sort_by: str, sort_dir: str) -> list[dict[str, Any]]:
    reverse = sort_dir == "desc"
    if sort_by == "year":
        return sorted(items, key=lambda item: (item["year"], item["title"].lower()), reverse=reverse)
    if sort_by == "cf_score":
        return sorted(
            items,
            key=lambda item: (item["cf_score"], item["cf_cutoff"] or 0, item["title"].lower()),
            reverse=reverse,
        )
    if sort_by == "hdr_target_status":
        return sorted(
            items,
            key=lambda item: (
                HDR_TARGET_SORT.get(item["hdr_target_status"], 99),
                -item["cf_score"],
                item["title"].lower(),
            ),
            reverse=reverse,
        )
    return sorted(items, key=lambda item: item["title"].lower(), reverse=reverse)


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
    hdr_target_status_value: str | None = Query(
        None,
        alias="hdr_target_status",
        description="met_target | below_target | no_hdr_target | no_file",
    ),
    dovi_no_fallback: bool | None = Query(None),
    sort_by: str = Query("cf_score", description="title | year | cf_score | hdr_target_status"),
    sort_dir: str = Query("desc", description="asc | desc"),
):
    """Return the Radarr Overlay page data."""
    movie_rows = (
        await db.execute(
            select(Movie, LetterboxState).outerjoin(
                LetterboxState, LetterboxState.movie_id == Movie.id
            )
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

    profile_rows = (
        (await db.execute(select(RadarrQualityProfile))).scalars().all()
    )
    profiles_by_id = {profile.id: profile for profile in profile_rows}
    profile_items_rows = (
        (await db.execute(select(RadarrProfileFormatItem))).scalars().all()
    )
    profile_items_by_profile: dict[int, list[RadarrProfileFormatItem]] = defaultdict(list)
    for row in profile_items_rows:
        profile_items_by_profile[row.profile_id].append(row)

    cf_rows = (
        (await db.execute(select(RadarrCustomFormat))).scalars().all()
    )
    cf_classifications = {
        row.id: classify_custom_format_tags(row.name, row.specifications_json)
        for row in cf_rows
    }
    movie_cf_rows = (
        (
            await db.execute(
                select(MovieCustomFormatScore).where(
                    MovieCustomFormatScore.movie_id.in_(movie_ids)
                )
            )
        )
        .scalars()
        .all()
        if movie_ids
        else []
    )
    movie_cf_by_movie: dict[int, list[MovieCustomFormatScore]] = defaultdict(list)
    for row in movie_cf_rows:
        movie_cf_by_movie[row.movie_id].append(row)

    profile_targets = {
        profile_id: ordered_tags(
            profile_hdr_targets(items, cf_classifications)
        )
        for profile_id, items in profile_items_by_profile.items()
    }

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
        targets = profile_targets.get(movie.quality_profile_id or -1, [])
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
            "cf_score": movie_cf_score(movie_cf_by_movie.get(movie.id, [])),
            "cf_cutoff": profiles_by_id.get(movie.quality_profile_id).cutoff_format_score
            if movie.quality_profile_id in profiles_by_id
            else None,
            "cutoff_met": movie.quality_cutoff_met,
            "hdr_targets": targets,
            "hdr_target_status": hdr_target_status(
                has_file=media is not None or bool(movie.movie_file_path),
                file_tags=set(hdr_tags_for_movie),
                targets=set(targets),
                require_dovi_fallback=pipeline_settings.HDR_OVERLAY_DOVI_REQUIRE_FALLBACK,
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
            hdr_target_status_value=hdr_target_status_value,
            dovi_no_fallback=dovi_no_fallback,
        )
    ]
    ordered = _sort_items(filtered, sort_by, sort_dir)

    total = len(ordered)
    start = (page - 1) * page_size
    end = start + page_size

    return {
        "distribution": _distribution(all_items),
        "distribution_order": list(HDR_DISTRIBUTION_ORDER),
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": ordered[start:end],
        "profiles": [
            {
                "id": profile.id,
                "name": profile.name,
                "cutoff_format_score": profile.cutoff_format_score,
            }
            for profile in sorted(profile_rows, key=lambda profile: profile.name.lower())
        ],
        "applied_filters": {
            "hdr_tags": selected_tags,
            "cf_score_min": cf_score_min,
            "cf_score_max": cf_score_max,
            "profile_id": profile_id,
            "hdr_target_status": hdr_target_status_value,
            "dovi_no_fallback": dovi_no_fallback,
            "sort_by": sort_by,
            "sort_dir": sort_dir,
        },
    }
