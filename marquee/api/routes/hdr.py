"""Radarr Overlay read model for HDR, custom formats, and preference targets."""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import UTC, datetime
from typing import Annotated, Any, NamedTuple
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from marquee.api.library_serializers import enrich_movie, resolution_label
from marquee.api.routes.jobs import job_summary
from marquee.api.routes.library import _coverage_by_media_file
from marquee.core.dovi_analysis import conversion_eligibility
from marquee.core.hdr_rollups import EpisodeHdr, episode_status, season_rollup, show_rollup
from marquee.core.jobs import job_manager
from marquee.core.jobs.manager import TERMINAL
from marquee.core.media_files import ensure_media_file_for_movie
from marquee.core.radarr_overlay import (
    PREFERENCE_STATUS_ORDER,
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
from marquee.core.tv_queries import season_downloaded, series_visible
from marquee.database import get_db
from marquee.media import binaries
from marquee.models import (
    DoviState,
    Episode,
    EpisodeMediaFile,
    Job,
    LetterboxState,
    MediaFile,
    Movie,
    RadarrCustomFormat,
    RadarrOverlayProfilePreference,
    RadarrProfileFormatItem,
    RadarrQualityProfile,
    Season,
    Series,
    SonarrCustomFormat,
    SonarrOverlayProfilePreference,
    SonarrProfileFormatItem,
    SonarrQualityProfile,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/hdr", tags=["hdr"])

FOUR_K_WIDTH_THRESHOLD = 3000  # plan 06 §6.2 — 4K/2160p-ish width floor for the "4K but SDR" insight

SHOW_STATUS_VALUES = (
    "exceeds_target",
    "meets_target",
    "gaps",
    "below_target",
    "no_hdr_target",
    "unknown",
)
SHOW_UNIFORMITY_VALUES = ("uniform", "uniform_by_season", "mixed")
# Worst-to-best rank for sort_by=status (ascending = worst first).
SHOW_STATUS_SORT = {
    value: rank
    for rank, value in enumerate(
        ("unknown", "no_hdr_target", "below_target", "gaps", "meets_target", "exceeds_target")
    )
}

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


class OverlayModels(NamedTuple):
    """One *arr's overlay model classes — Radarr and Sonarr mirror each other exactly."""

    profile_model: type[Any]
    format_item_model: type[Any]
    custom_format_model: type[Any]
    preference_model: type[Any]


RADARR_MODELS = OverlayModels(
    profile_model=RadarrQualityProfile,
    format_item_model=RadarrProfileFormatItem,
    custom_format_model=RadarrCustomFormat,
    preference_model=RadarrOverlayProfilePreference,
)
SONARR_MODELS = OverlayModels(
    profile_model=SonarrQualityProfile,
    format_item_model=SonarrProfileFormatItem,
    custom_format_model=SonarrCustomFormat,
    preference_model=SonarrOverlayProfilePreference,
)


async def _load_profile_context(
    db: AsyncSession,
    models: OverlayModels = RADARR_MODELS,
) -> tuple[
    dict[int, Any],
    dict[int, list[str]],
    dict[int, dict[str, Any]],
]:
    profile_rows = (await db.execute(select(models.profile_model))).scalars().all()
    profile_items_rows = (await db.execute(select(models.format_item_model))).scalars().all()
    cf_rows = (await db.execute(select(models.custom_format_model))).scalars().all()
    preference_rows = (await db.execute(select(models.preference_model))).scalars().all()

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


async def _load_movie_items(
    db: AsyncSession,
) -> tuple[list[dict[str, Any]], dict[int, RadarrQualityProfile], dict[int, dict[str, Any]]]:
    """Build the per-movie HDR item list shared by ``/api/hdr`` and ``/api/hdr/summary``.

    Every item still carries ``distribution_keys`` (stripped only by
    ``_serialize_item`` right before a list response is returned) so summary
    computations can reuse it without a second query.
    """
    movie_rows = (
        await db.execute(
            select(Movie, LetterboxState, DoviState)
            .outerjoin(LetterboxState, LetterboxState.movie_id == Movie.id)
            .outerjoin(DoviState, DoviState.movie_id == Movie.id)
            .where(Movie.movie_file_path.is_not(None), Movie.movie_file_path != "")
        )
    ).all()
    movies = [movie for movie, _, _ in movie_rows]
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
    for movie, lb, dovi_state in movie_rows:
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
            "dovi_status": dovi_state.status if dovi_state is not None else None,
            "dovi_profile": dovi_state.dovi_profile if dovi_state is not None else None,
            "dovi_el_type": dovi_state.el_type if dovi_state is not None else None,
            "dovi_bl_signal_compatibility_id": (
                dovi_state.bl_signal_compatibility_id if dovi_state is not None else None
            ),
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
                excluded_targets=preference_summary["excluded_targets"]
                if preference_summary
                else None,
            ),
        }
        all_items.append(item)

    return all_items, profiles_by_id, preference_summary_by_profile


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
    all_items, profiles_by_id, preference_summary_by_profile = await _load_movie_items(db)

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


async def _apply_profile_preferences(
    db: AsyncSession,
    payload: ProfilePreferencesUpdatePayload,
    models: OverlayModels,
) -> list[int]:
    """Validate + persist meet/exceed preference targets for one *arr's profiles."""
    if not payload.profiles:
        raise HTTPException(status_code=400, detail="No profile preferences provided")

    profiles_by_id, _, preference_summary_by_profile = await _load_profile_context(db, models)
    now = datetime.now(UTC)
    profile_ids = {profile.profile_id for profile in payload.profiles}
    existing_rows = {
        row.profile_id: row
        for row in (
            await db.execute(
                select(models.preference_model).where(
                    models.preference_model.profile_id.in_(profile_ids)
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
        if (
            update.exceed_target is not None
            and update.meet_target is not None
            and preference_rank(update.exceed_target) <= preference_rank(update.meet_target)
        ):
            raise HTTPException(
                status_code=400,
                detail=f"exceed_target must rank above meet_target for profile {update.profile_id}",
            )

        row = existing_rows.get(update.profile_id)
        if row is None:
            row = models.preference_model(profile_id=update.profile_id)
            db.add(row)
        row.meet_target = update.meet_target
        row.exceed_target = update.exceed_target
        row.excluded_targets = update.excluded_targets
        row.updated_at = now
        applied.append(update.profile_id)

    await db.commit()
    return applied


@router.put("/preferences")
async def put_profile_preferences(
    payload: ProfilePreferencesUpdatePayload,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Persist per-quality-profile meet/exceed preference targets."""
    applied = await _apply_profile_preferences(db, payload, RADARR_MODELS)
    return {"applied_profile_ids": applied}


# ---------------------------------------------------------------------------
# TV HDR landing/rollup read-model (plan 06 phase 3)
# ---------------------------------------------------------------------------


def _sum_dicts(dicts: list[dict[str, int]], keys: tuple[str, ...]) -> dict[str, int]:
    totals = dict.fromkeys(keys, 0)
    for source in dicts:
        for key, count in source.items():
            totals[key] = totals.get(key, 0) + count
    return totals


async def _episode_media_file_id(db: AsyncSession, episode_id: int) -> int | None:
    return (
        await db.execute(
            select(EpisodeMediaFile.media_file_id).where(EpisodeMediaFile.episode_id == episode_id)
        )
    ).scalar_one_or_none()


def _build_season_rollups(
    season_map: dict[int, list[Episode]],
    *,
    profile_targets: list[str],
    meet_target: str | None,
    exceed_target: str | None,
    excluded_targets: list[str],
) -> dict[int, dict[str, Any]]:
    """Build one ``season_rollup`` dict per season number from raw Episode rows."""
    season_rollups: dict[int, dict[str, Any]] = {}
    for season_number, episodes in season_map.items():
        episode_hdrs = [
            EpisodeHdr(
                season_number=season_number,
                episode_number=episode.episode_number,
                title=episode.title,
                hdr_type_raw=episode.hdr_type_raw,
            )
            for episode in episodes
        ]
        statuses = [
            episode_status(
                eh.tags if eh.hdr_type_raw is not None else None,
                profile_targets,
                meet_target,
                exceed_target,
                excluded_targets,
            )
            for eh in episode_hdrs
        ]
        season_rollups[season_number] = season_rollup(episode_hdrs, statuses)
    return season_rollups


async def _load_show_items(
    db: AsyncSession,
) -> tuple[list[dict[str, Any]], dict[int, SonarrQualityProfile], dict[int, dict[str, Any]]]:
    """Build the per-show HDR rollup item list shared by ``/api/hdr/tv`` and ``/api/hdr/summary``.

    One query for visible series, one grouped query for their eligible
    episodes' HDR fields; rollups are built in Python (H8).
    """
    visible_series = (await db.execute(select(Series).where(series_visible()))).scalars().all()
    series_ids = [series.id for series in visible_series]

    episode_rows = (
        (
            await db.execute(
                select(Episode)
                .join(
                    Season,
                    (Season.series_id == Episode.series_id)
                    & (Season.season_number == Episode.season_number),
                )
                .where(
                    Episode.series_id.in_(series_ids),
                    Episode.episode_file_path.is_not(None),
                    season_downloaded(),
                )
            )
        )
        .scalars()
        .all()
        if series_ids
        else []
    )

    episodes_by_series: dict[int, dict[int, list[Episode]]] = defaultdict(lambda: defaultdict(list))
    for episode in episode_rows:
        episodes_by_series[episode.series_id][episode.season_number].append(episode)

    profiles_by_id, _, preference_summary_by_profile = await _load_profile_context(db, SONARR_MODELS)

    items: list[dict[str, Any]] = []
    for series in visible_series:
        season_map = episodes_by_series.get(series.id, {})
        preference_summary = preference_summary_by_profile.get(series.quality_profile_id or -1)
        profile_targets = preference_summary["profile_targets"] if preference_summary else []
        meet_target = preference_summary["meet_target"] if preference_summary else None
        exceed_target = preference_summary["exceed_target"] if preference_summary else None
        excluded_targets = preference_summary["excluded_targets"] if preference_summary else []

        season_rollups = _build_season_rollups(
            season_map,
            profile_targets=profile_targets,
            meet_target=meet_target,
            exceed_target=exceed_target,
            excluded_targets=excluded_targets,
        )
        rollup = show_rollup(season_rollups)
        profile = profiles_by_id.get(series.quality_profile_id)

        items.append(
            {
                "id": series.id,
                "title": series.title,
                "year": series.year,
                "poster_available": bool(series.poster_path),
                "profile_id": series.quality_profile_id,
                "profile_name": profile.name if profile else None,
                "profile_targets": profile_targets,
                "meet_target": meet_target,
                "exceed_target": exceed_target,
                "rollup": rollup,
                "seasons_count": len(season_map),
                "episodes_total": sum(len(episodes) for episodes in season_map.values()),
            }
        )

    return items, profiles_by_id, preference_summary_by_profile


def _filter_show_item(
    item: dict[str, Any],
    *,
    selected_tags: list[str],
    profile_id: int | None,
    preference_status_value: str | None,
    uniformity: str | None,
    dovi_no_fallback: bool | None,
) -> bool:
    if selected_tags:
        item_values = set(item["rollup"]["union_tags"]) | set(item["rollup"]["distribution"])
        if not any(tag in item_values for tag in selected_tags):
            return False
    if profile_id is not None and item["profile_id"] != profile_id:
        return False
    if preference_status_value and item["rollup"]["status"] != preference_status_value:
        return False
    if uniformity and item["rollup"]["uniformity"] != uniformity:
        return False
    return not (dovi_no_fallback is True and "dovi_no_fallback" not in item["rollup"]["union_tags"])


def _coverage_value(item: dict[str, Any]) -> float:
    fraction = item["rollup"]["meeting_fraction"]
    if fraction["of"] == 0:
        return -1.0
    return fraction["met"] / fraction["of"]


def _sort_show_items(items: list[dict[str, Any]], sort_by: str, sort_dir: str) -> list[dict[str, Any]]:
    reverse = sort_dir == "desc"
    if sort_by == "status":
        return sorted(
            items,
            key=lambda item: (
                SHOW_STATUS_SORT.get(item["rollup"]["status"], -(10**9)),
                _sort_title_value(item),
            ),
            reverse=reverse,
        )
    if sort_by == "coverage":
        return sorted(
            items, key=lambda item: (_coverage_value(item), _sort_title_value(item)), reverse=reverse
        )
    return sorted(items, key=_sort_title_value, reverse=reverse)


@router.get("/summary")
async def hdr_summary(db: Annotated[AsyncSession, Depends(get_db)]):
    """Library-wide HDR landing metrics: movies + TV + insights + both libraries' preferences."""
    movie_items, _radarr_profiles_by_id, radarr_preferences = await _load_movie_items(db)
    show_items, _sonarr_profiles_by_id, sonarr_preferences = await _load_show_items(db)

    movies_status_counts = dict.fromkeys(PREFERENCE_STATUS_ORDER, 0)
    movies_dovi_total = 0
    movies_dovi_analyzed = 0
    movies_dovi_no_fallback = 0
    movies_no_hdr_target = 0
    movies_four_k_sdr = 0
    for item in movie_items:
        movies_status_counts[item["preference_status"]] = (
            movies_status_counts.get(item["preference_status"], 0) + 1
        )
        if "dovi" in item["hdr_tags"]:
            movies_dovi_total += 1
            if item["dovi_status"] == "analyzed":
                movies_dovi_analyzed += 1
        if "dovi_no_fallback" in item["hdr_tags"]:
            movies_dovi_no_fallback += 1
        if item["preference_status"] == "no_hdr_target":
            movies_no_hdr_target += 1
        if (
            item["video_width"] is not None
            and item["video_width"] >= FOUR_K_WIDTH_THRESHOLD
            and "sdr" in item["distribution_keys"]
        ):
            movies_four_k_sdr += 1
    movies_unanalyzed_dovi = movies_dovi_total - movies_dovi_analyzed

    tv_show_status_counts = dict.fromkeys(SHOW_STATUS_VALUES, 0)
    tv_uniformity_counts = dict.fromkeys(SHOW_UNIFORMITY_VALUES, 0)
    tv_episodes_total = 0
    tv_episodes_unknown = 0
    for item in show_items:
        rollup = item["rollup"]
        tv_show_status_counts[rollup["status"]] = tv_show_status_counts.get(rollup["status"], 0) + 1
        tv_uniformity_counts[rollup["uniformity"]] = (
            tv_uniformity_counts.get(rollup["uniformity"], 0) + 1
        )
        tv_episodes_total += rollup["episodes_total"]
        tv_episodes_unknown += rollup["episodes_unknown"]
    tv_episode_distribution = _sum_dicts(
        [item["rollup"]["distribution"] for item in show_items], HDR_DISTRIBUTION_ORDER
    )

    series_ids = [item["id"] for item in show_items]
    tv_insights = await _tv_episode_insight_counts(db, series_ids)

    worst_offenders = sorted(
        (item for item in show_items if item["rollup"]["status"] in ("gaps", "below_target")),
        key=lambda item: (
            -item["rollup"]["status_counts"].get("below_target", 0),
            _sort_title_value(item),
        ),
    )[:10]

    return {
        "movies": {
            "total": len(movie_items),
            "distribution": _distribution(movie_items),
            "status_counts": movies_status_counts,
            "dovi_analysis": {"analyzed": movies_dovi_analyzed, "total_dovi": movies_dovi_total},
        },
        "tv": {
            "shows_total": len(show_items),
            "episodes_total": tv_episodes_total,
            "episodes_unknown": tv_episodes_unknown,
            "episode_distribution": tv_episode_distribution,
            "show_status_counts": tv_show_status_counts,
            "uniformity_counts": tv_uniformity_counts,
            "dovi_analysis": {
                "analyzed": tv_insights["dovi_analyzed_episodes"],
                "total_dovi": tv_insights["dovi_total_episodes"],
            },
        },
        "worst_offenders": [
            {
                "series_id": item["id"],
                "title": item["title"],
                "year": item["year"],
                "status": item["rollup"]["status"],
                "below_count": item["rollup"]["status_counts"].get("below_target", 0),
                "unknown_count": item["rollup"]["episodes_unknown"],
                "episodes_total": item["rollup"]["episodes_total"],
                "meeting_fraction": item["rollup"]["meeting_fraction"],
            }
            for item in worst_offenders
        ],
        "insights": {
            "dovi_no_fallback": {
                "movies": movies_dovi_no_fallback,
                "episodes": tv_insights["dovi_no_fallback_episodes"],
                "shows_affected": len(tv_insights["dovi_no_fallback_shows"]),
            },
            "unanalyzed_dovi": {
                "movies": movies_unanalyzed_dovi,
                "episodes": tv_insights["dovi_total_episodes"] - tv_insights["dovi_analyzed_episodes"],
            },
            "no_hdr_target": {
                "movies": movies_no_hdr_target,
                "shows": tv_show_status_counts.get("no_hdr_target", 0),
            },
            "four_k_sdr": {
                "movies": movies_four_k_sdr,
                "shows_affected": len(tv_insights["four_k_sdr_shows"]),
                "episodes": tv_insights["four_k_sdr_episodes"],
            },
        },
        "profile_preferences": {
            "radarr": sorted(radarr_preferences.values(), key=lambda summary: summary["profile_name"].lower()),
            "sonarr": sorted(sonarr_preferences.values(), key=lambda summary: summary["profile_name"].lower()),
        },
    }


async def _tv_episode_insight_counts(db: AsyncSession, series_ids: list[int]) -> dict[str, Any]:
    """Episode-level counts feeding ``/summary``'s TV dovi-analysis + insights blocks."""
    if not series_ids:
        return {
            "dovi_no_fallback_episodes": 0,
            "dovi_no_fallback_shows": set(),
            "dovi_total_episodes": 0,
            "dovi_analyzed_episodes": 0,
            "four_k_sdr_episodes": 0,
            "four_k_sdr_shows": set(),
        }

    rows = (
        await db.execute(
            select(Episode, DoviState)
            .join(
                Season,
                (Season.series_id == Episode.series_id)
                & (Season.season_number == Episode.season_number),
            )
            .outerjoin(DoviState, DoviState.episode_id == Episode.id)
            .where(
                Episode.series_id.in_(series_ids),
                Episode.episode_file_path.is_not(None),
                season_downloaded(),
            )
        )
    ).all()

    dovi_no_fallback_episodes = 0
    dovi_no_fallback_shows: set[int] = set()
    dovi_total_episodes = 0
    dovi_analyzed_episodes = 0
    four_k_sdr_episodes = 0
    four_k_sdr_shows: set[int] = set()

    for episode, dovi_state in rows:
        tags = ordered_tags(classify_hdr_tags(episode.hdr_type_raw))
        if "dovi_no_fallback" in tags:
            dovi_no_fallback_episodes += 1
            dovi_no_fallback_shows.add(episode.series_id)
        if "dovi" in tags:
            dovi_total_episodes += 1
            if dovi_state is not None and dovi_state.status == "analyzed":
                dovi_analyzed_episodes += 1
        if (
            episode.video_width is not None
            and episode.video_width >= FOUR_K_WIDTH_THRESHOLD
            and episode.hdr_type_raw == "SDR"
        ):
            four_k_sdr_episodes += 1
            four_k_sdr_shows.add(episode.series_id)

    return {
        "dovi_no_fallback_episodes": dovi_no_fallback_episodes,
        "dovi_no_fallback_shows": dovi_no_fallback_shows,
        "dovi_total_episodes": dovi_total_episodes,
        "dovi_analyzed_episodes": dovi_analyzed_episodes,
        "four_k_sdr_episodes": four_k_sdr_episodes,
        "four_k_sdr_shows": four_k_sdr_shows,
    }


@router.get("/tv")
async def hdr_tv_index(
    db: Annotated[AsyncSession, Depends(get_db)],
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    hdr_tags: Annotated[
        list[str] | None,
        Query(description="Repeated or comma-separated HDR tags"),
    ] = None,
    preference_status_value: str | None = Query(
        None,
        alias="preference_status",
        description="exceeds_target | meets_target | gaps | below_target | no_hdr_target | unknown",
    ),
    uniformity: str | None = Query(None, description="uniform | uniform_by_season | mixed"),
    profile_id: int | None = Query(None, ge=1),
    dovi_no_fallback: bool | None = Query(None),
    sort_by: str = Query("title", description="title | status | coverage"),
    sort_dir: str = Query("asc", description="asc | desc"),
):
    """Return the TV show list with rollups + filters (movie-envelope-compatible)."""
    items, profiles_by_id, preference_summary_by_profile = await _load_show_items(db)

    selected_tags = _parse_hdr_tags(None, hdr_tags)
    filtered = [
        item
        for item in items
        if _filter_show_item(
            item,
            selected_tags=selected_tags,
            profile_id=profile_id,
            preference_status_value=preference_status_value,
            uniformity=uniformity,
            dovi_no_fallback=dovi_no_fallback,
        )
    ]
    ordered = _sort_show_items(filtered, sort_by, sort_dir)

    overlay_profile_ids = {
        item["profile_id"]
        for item in items
        if item["profile_id"] is not None and item["profile_id"] in profiles_by_id
    }

    total = len(ordered)
    start = (page - 1) * page_size
    end = start + page_size

    episode_distribution = _sum_dicts(
        [item["rollup"]["distribution"] for item in items], HDR_DISTRIBUTION_ORDER
    )

    return {
        "distribution": episode_distribution,
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
            for profile in sorted(
                (profile for profile in profiles_by_id.values() if profile.id in overlay_profile_ids),
                key=lambda profile: profile.name.lower(),
            )
        ],
        "profile_preferences": [
            preference_summary_by_profile[profile_id]
            for profile_id in sorted(
                (pid for pid in preference_summary_by_profile if pid in overlay_profile_ids),
                key=lambda current_profile_id: profiles_by_id[current_profile_id].name.lower(),
            )
        ],
        "applied_filters": {
            "hdr_tags": selected_tags,
            "profile_id": profile_id,
            "preference_status": preference_status_value,
            "uniformity": uniformity,
            "dovi_no_fallback": dovi_no_fallback,
            "sort_by": sort_by,
            "sort_dir": sort_dir,
        },
    }


@router.get("/tv/{series_id}")
async def hdr_tv_detail(
    series_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Season × episode HDR read-model for one show (the granular detail page)."""
    series = (await db.execute(select(Series).where(Series.id == series_id))).scalar_one_or_none()
    if series is None:
        raise HTTPException(status_code=404, detail=f"Series id={series_id} not found")
    visible = (
        await db.execute(select(Series.id).where(Series.id == series_id, series_visible()))
    ).scalar_one_or_none()
    if visible is None:
        raise HTTPException(status_code=404, detail=f"Series id={series_id} not found")

    season_rows = (
        (
            await db.execute(
                select(Season)
                .where(Season.series_id == series_id, season_downloaded())
                .order_by(Season.season_number)
            )
        )
        .scalars()
        .all()
    )
    season_numbers = [season.season_number for season in season_rows]

    episode_rows = (
        (
            await db.execute(
                select(Episode, DoviState)
                .outerjoin(DoviState, DoviState.episode_id == Episode.id)
                .where(
                    Episode.series_id == series_id,
                    Episode.episode_file_path.is_not(None),
                    Episode.season_number.in_(season_numbers),
                )
                .order_by(Episode.season_number, Episode.episode_number)
            )
        ).all()
        if season_numbers
        else []
    )

    episodes_by_season: dict[int, list[tuple[Episode, DoviState | None]]] = defaultdict(list)
    for episode, dovi_state in episode_rows:
        episodes_by_season[episode.season_number].append((episode, dovi_state))

    profiles_by_id, _, preference_summary_by_profile = await _load_profile_context(db, SONARR_MODELS)
    preference_summary = preference_summary_by_profile.get(series.quality_profile_id or -1)
    profile_targets = preference_summary["profile_targets"] if preference_summary else []
    meet_target = preference_summary["meet_target"] if preference_summary else None
    exceed_target = preference_summary["exceed_target"] if preference_summary else None
    excluded_targets = preference_summary["excluded_targets"] if preference_summary else []
    profile = profiles_by_id.get(series.quality_profile_id)

    season_map = {number: [ep for ep, _ in episodes_by_season.get(number, [])] for number in season_numbers}
    season_rollups = _build_season_rollups(
        season_map,
        profile_targets=profile_targets,
        meet_target=meet_target,
        exceed_target=exceed_target,
        excluded_targets=excluded_targets,
    )

    seasons_payload = []
    for season_number in season_numbers:
        pairs = episodes_by_season.get(season_number, [])
        episodes_payload = []
        for episode, dovi_state in pairs:
            tags = ordered_tags(classify_hdr_tags(episode.hdr_type_raw))
            status = episode_status(
                tags if episode.hdr_type_raw is not None else None,
                profile_targets,
                meet_target,
                exceed_target,
                excluded_targets,
            )
            episodes_payload.append(
                {
                    "id": episode.id,
                    "episode_number": episode.episode_number,
                    "title": episode.title,
                    "hdr_type_raw": episode.hdr_type_raw,
                    "hdr_tags": tags,
                    "bucket": overlay_bucket(episode.hdr_type_raw)
                    if episode.hdr_type_raw is not None
                    else "unknown",
                    "resolution": resolution_label(episode.video_width, episode.video_height),
                    "preference_status": status,
                    "dovi": _dovi_state_to_dict(dovi_state) if dovi_state is not None else None,
                }
            )
        seasons_payload.append(
            {
                "season_number": season_number,
                "is_specials": season_number == 0,
                "rollup": season_rollups[season_number],
                "episodes": episodes_payload,
            }
        )

    rollup = show_rollup(season_rollups)

    episode_ids = [episode.id for episode, _ in episode_rows]
    active_jobs = (
        (
            await db.execute(
                select(Job)
                .where(
                    Job.type == "dovi_analyze",
                    Job.subject_type == "episode",
                    Job.subject_id.in_([str(episode_id) for episode_id in episode_ids]),
                    Job.status.notin_(tuple(TERMINAL)),
                )
                .order_by(Job.created_at.desc(), Job.id.desc())
            )
        )
        .scalars()
        .all()
        if episode_ids
        else []
    )

    return {
        "series": {
            "id": series.id,
            "title": series.title,
            "year": series.year,
            "tvdb_id": series.tvdb_id,
            "tmdb_id": series.tmdb_id,
            "quality_profile_id": series.quality_profile_id,
        },
        "profile": {
            "id": profile.id if profile else None,
            "name": profile.name if profile else None,
            "targets": profile_targets,
            "meet_target": meet_target,
            "exceed_target": exceed_target,
            "excluded_targets": excluded_targets,
        },
        "rollup": rollup,
        "seasons": seasons_payload,
        "analysis_jobs": [job_summary(job) for job in active_jobs],
        "binaries": {"ffprobe": binaries.resolve("ffprobe") is not None},
    }


@router.put("/tv/preferences")
async def put_tv_profile_preferences(
    payload: ProfilePreferencesUpdatePayload,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Persist per-Sonarr-quality-profile meet/exceed preference targets."""
    applied = await _apply_profile_preferences(db, payload, SONARR_MODELS)
    return {"applied_profile_ids": applied}


class TvAnalyzeShowRequest(BaseModel):
    season_number: int | None = None


class TvAnalyzeLibraryRequest(BaseModel):
    series_ids: list[int] | None = None


@router.post("/tv/{series_id}/analyze", status_code=202)
async def analyze_tv_show_dovi(
    series_id: int,
    body: TvAnalyzeShowRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Enqueue a DoVi analysis batch for one show (or one season of it)."""
    if binaries.resolve("ffprobe") is None:
        raise HTTPException(
            status_code=503,
            detail="ffprobe not found on PATH — install it to analyze Dolby Vision.",
        )
    series = (await db.execute(select(Series).where(Series.id == series_id))).scalar_one_or_none()
    if series is None:
        raise HTTPException(status_code=404, detail=f"Series id={series_id} not found")

    query = select(Episode).where(
        Episode.series_id == series_id,
        Episode.has_dv.is_(True),
        Episode.episode_file_path.is_not(None),
    )
    if body.season_number is not None:
        query = query.where(Episode.season_number == body.season_number)
    episodes = (await db.execute(query)).scalars().all()
    if not episodes:
        raise HTTPException(
            status_code=400, detail="No Dolby Vision episodes with a media file to analyze"
        )

    children = await _dovi_episode_batch_children(db, episodes)
    batch, _children = await job_manager.create_batch(
        db,
        parent_type="dovi_analyze_batch",
        parent_payload={"series_id": series_id, "episode_ids": [episode.id for episode in episodes]},
        parent_priority=60,
        parent_subject_type="dovi_tv_batch",
        parent_subject_id=str(series_id),
        children=children,
    )
    return {
        "job_id": batch.id,
        "total": len(children),
        "events_url": f"/api/jobs/{batch.id}/events",
    }


async def _dovi_episode_batch_children(
    db: AsyncSession, episodes: list[Episode]
) -> list[dict[str, Any]]:
    """Build ``dovi_analyze`` batch children for episodes, mirroring the movie batch shape.

    Unlike the movie path, a missing MediaFile row does not skip the episode
    (H4/§6.6) — it just proceeds without the file-lock resource.
    """
    children: list[dict[str, Any]] = []
    for episode in episodes:
        media_file_id = await _episode_media_file_id(db, episode.id)
        if media_file_id is None:
            logger.info(
                "no MediaFile row for episode id=%s — proceeding without file lock", episode.id
            )
        file_lock = {f"media-file:{media_file_id}": 1} if media_file_id is not None else {}
        children.append(
            {
                "job_type": "dovi_analyze",
                "payload": {"episode_id": episode.id},
                "priority": 60,
                "resources": {"media_read": 1, **file_lock},
                "subject_type": "episode",
                "subject_id": episode.id,
            }
        )
    return children


@router.post("/tv/analyze", status_code=202)
async def analyze_tv_dovi_batch(
    body: TvAnalyzeLibraryRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Analyze every Dolby Vision TV episode across visible shows (optionally a subset)."""
    if binaries.resolve("ffprobe") is None:
        raise HTTPException(
            status_code=503,
            detail="ffprobe not found on PATH — install it to analyze Dolby Vision.",
        )
    visible_series_ids = (await db.execute(select(Series.id).where(series_visible()))).scalars().all()
    if body.series_ids:
        wanted = set(body.series_ids)
        visible_series_ids = [sid for sid in visible_series_ids if sid in wanted]

    episodes = (
        (
            await db.execute(
                select(Episode).where(
                    Episode.series_id.in_(visible_series_ids),
                    Episode.has_dv.is_(True),
                    Episode.episode_file_path.is_not(None),
                )
            )
        )
        .scalars()
        .all()
        if visible_series_ids
        else []
    )
    if not episodes:
        raise HTTPException(
            status_code=400, detail="No Dolby Vision episodes with a media file to analyze"
        )

    children = await _dovi_episode_batch_children(db, episodes)
    batch, _children = await job_manager.create_batch(
        db,
        parent_type="dovi_analyze_batch",
        parent_payload={"series_ids": list(visible_series_ids), "episode_ids": [episode.id for episode in episodes]},
        parent_priority=60,
        parent_subject_type="dovi_tv_batch",
        parent_subject_id=uuid4().hex,
        children=children,
    )
    return {
        "job_id": batch.id,
        "total": len(children),
        "events_url": f"/api/jobs/{batch.id}/events",
    }


# ---------------------------------------------------------------------------
# Per-movie Dolby Vision detail + analysis
# ---------------------------------------------------------------------------


class DoviAnalyzeBatchRequest(BaseModel):
    movie_ids: list[int] | None = None


class DoviConvertRequest(BaseModel):
    kind: str | None = None


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


async def _active_dovi_conversion_job(db: AsyncSession, movie_id: int) -> Job | None:
    return (
        await db.execute(
            select(Job)
            .where(
                Job.type == "dovi_convert",
                Job.subject_type == "movie",
                Job.subject_id == str(movie_id),
                Job.status.notin_(tuple(TERMINAL)),
            )
            .order_by(Job.created_at.desc(), Job.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def _latest_dovi_conversion_job(db: AsyncSession, movie_id: int) -> Job | None:
    return (
        await db.execute(
            select(Job)
            .where(
                Job.type == "dovi_convert",
                Job.subject_type == "movie",
                Job.subject_id == str(movie_id),
            )
            .order_by(Job.created_at.desc(), Job.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


def _dovi_job_detail(job: Job | None) -> dict | None:
    if job is None:
        return None
    data = job_summary(job)
    data["result"] = job.result
    data["error"] = job.error
    return data


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
        "last_analyzed_at": state.last_analyzed_at.isoformat() if state.last_analyzed_at else None,
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
        (
            await db.execute(
                select(RadarrQualityProfile).where(
                    RadarrQualityProfile.id == movie.quality_profile_id
                )
            )
        ).scalar_one_or_none()
        if movie.quality_profile_id is not None
        else None
    )
    hdr_tags = ordered_tags(classify_hdr_tags(movie.hdr_type_raw))
    active = await _active_dovi_job(db, movie_id)
    conversion_job = await _latest_dovi_conversion_job(db, movie_id)
    return {
        "movie": _movie_detail_dict(movie),
        "profile_name": profile.name if profile else None,
        "hdr_tags": hdr_tags,
        "hdr_bucket": overlay_bucket(movie.hdr_type_raw),
        "dovi": _dovi_state_to_dict(state),
        "binaries": {
            "ffmpeg": binaries.resolve("ffmpeg") is not None,
            "dovi_tool": binaries.resolve("dovi_tool") is not None,
            "ffprobe": binaries.resolve("ffprobe") is not None,
        },
        "analysis_job": job_summary(active) if active else None,
        "conversion_job": _dovi_job_detail(conversion_job),
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


@router.post("/{movie_id}/convert")
async def convert_movie_dovi(
    movie_id: int,
    body: DoviConvertRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Enqueue a supported Dolby Vision Profile 8.1 remediation job."""
    missing = [
        name for name in ("ffmpeg", "ffprobe", "dovi_tool") if binaries.resolve(name) is None
    ]
    if missing:
        raise HTTPException(
            status_code=503,
            detail=f"Required binary not found on PATH: {', '.join(missing)}",
        )
    movie = await _load_movie(db, movie_id)
    state = (
        await db.execute(select(DoviState).where(DoviState.movie_id == movie_id))
    ).scalar_one_or_none()
    if state is None or state.status != "analyzed":
        raise HTTPException(
            status_code=409,
            detail="Run Dolby Vision analysis before starting remediation.",
        )
    conversion = conversion_eligibility(state.dovi_profile, state.el_type)
    kind = body.kind or conversion.get("kind")
    if not conversion.get("eligible") or kind != conversion.get("kind"):
        raise HTTPException(
            status_code=422,
            detail={
                "code": "not_eligible",
                "message": conversion.get("reason") or "This stream is not eligible.",
            },
        )
    active = await _active_dovi_conversion_job(db, movie.id)
    if active is not None:
        return _dovi_job_detail(active)
    media_file = await ensure_media_file_for_movie(db, movie)
    file_lock = {f"media-file:{media_file.id}": 1} if media_file is not None else {}
    job = await job_manager.create(
        db,
        job_type="dovi_convert",
        payload={"movie_id": movie.id, "kind": kind},
        priority=75,
        resources={"media_write": 1, **file_lock},
        subject_type="movie",
        subject_id=movie.id,
        max_attempts=1,
    )
    return _dovi_job_detail(job)


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
        (
            await db.execute(
                select(Movie).where(
                    Movie.has_dv.is_(True),
                    Movie.movie_file_path.is_not(None),
                    Movie.movie_file_path != "",
                )
            )
        )
        .scalars()
        .all()
    )
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
