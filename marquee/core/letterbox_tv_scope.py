"""Scoped TV letterbox apply/revert helpers shared by routes and jobs."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from marquee.core.letterbox_service import letterbox_service
from marquee.models import Episode, EpisodeMediaFile, LetterboxState, MediaFile

ALLOWED_CONFIDENCE_LEVELS = {"high", "medium", "low", "variable", "all"}
DEFAULT_CONFIDENCE_LEVELS = ["high"]

ProgressCallback = Callable[[int, int], Awaitable[None]]
TvScopeRow = tuple[Episode, LetterboxState | None, int | None]


def normalize_confidence_levels(levels: list[str] | None) -> list[str] | None:
    values = DEFAULT_CONFIDENCE_LEVELS if levels is None else levels
    invalid = [value for value in values if value not in ALLOWED_CONFIDENCE_LEVELS]
    if invalid:
        raise ValueError(f"Invalid confidence level: {invalid[0]}")
    if "all" in values:
        return None
    return values


async def load_tv_scope_rows(
    db: AsyncSession,
    series_id: int,
    *,
    season_number: int | None = None,
) -> list[TvScopeRow]:
    stmt = (
        select(Episode, LetterboxState, MediaFile.id)
        .join(EpisodeMediaFile, EpisodeMediaFile.episode_id == Episode.id)
        .join(MediaFile, MediaFile.id == EpisodeMediaFile.media_file_id)
        .outerjoin(
            LetterboxState,
            (LetterboxState.media_type == "episode") & (LetterboxState.episode_id == Episode.id),
        )
        .where(
            Episode.series_id == series_id,
            Episode.episode_file_path.is_not(None),
            Episode.episode_file_path != "",
            MediaFile.is_active.is_(True),
        )
        .order_by(Episode.season_number, Episode.episode_number, Episode.id, MediaFile.id)
    )
    if season_number is not None:
        stmt = stmt.where(Episode.season_number == season_number)
    return list((await db.execute(stmt)).all())


def scope_episode_group(rows: list[TvScopeRow], episode_id: int) -> list[TvScopeRow]:
    target_row = next((row for row in rows if row[0].id == episode_id), None)
    if target_row is None:
        return []
    media_file_id = target_row[2]
    if media_file_id is None:
        return [target_row]
    return [row for row in rows if row[2] == media_file_id]


def _group_key(episode: Episode, media_file_id: int | None) -> int:
    return media_file_id if media_file_id is not None else -(episode.id or 0)


def _dedupe_groups(
    rows: list[TvScopeRow],
    *,
    applied_only: bool = False,
    confidence_levels: list[str] | None = None,
) -> list[tuple[list[TvScopeRow], LetterboxState]]:
    groups: dict[int, tuple[list[TvScopeRow], LetterboxState]] = {}
    for row in rows:
        episode, state, media_file_id = row
        if state is None:
            continue
        if applied_only:
            if state.applied_crop_top is None and state.applied_crop_bottom is None:
                continue
        else:
            if state.status != "candidate":
                continue
            if not (state.recommended_crop_top or state.recommended_crop_bottom):
                continue
            if confidence_levels is not None and state.confidence not in confidence_levels:
                continue
        group_key = _group_key(episode, media_file_id)
        groups.setdefault(group_key, (scope_episode_group(rows, episode.id), state))
    return list(groups.values())


async def apply_tv_scope(
    db: AsyncSession,
    series_id: int,
    *,
    season_number: int | None = None,
    confidence_levels: list[str] | None = None,
    progress: ProgressCallback | None = None,
) -> dict:
    levels = normalize_confidence_levels(confidence_levels)
    rows = await load_tv_scope_rows(db, series_id, season_number=season_number)
    groups = _dedupe_groups(rows, confidence_levels=levels)
    results: list[dict] = []
    total = len(groups)
    for index, (group_rows, state) in enumerate(groups, start=1):
        result = await letterbox_service.apply_episode_group(
            db,
            [episode for episode, *_rest in group_rows],
            top=state.recommended_crop_top or 0,
            bottom=state.recommended_crop_bottom or 0,
            source="job",
        )
        results.append(
            {
                "episode_ids": [episode.id for episode, *_rest in group_rows],
                "top": result.top,
                "bottom": result.bottom,
                "path": result.path,
                "verified": result.verified,
            }
        )
        if progress is not None:
            await progress(index, total)
    return {
        "applied_groups": len(results),
        "applied_episodes": sum(len(item["episode_ids"]) for item in results),
        "items": results,
    }


async def revert_tv_scope(
    db: AsyncSession,
    series_id: int,
    *,
    season_number: int | None = None,
    progress: ProgressCallback | None = None,
) -> dict:
    rows = await load_tv_scope_rows(db, series_id, season_number=season_number)
    groups = _dedupe_groups(rows, applied_only=True)
    results: list[dict] = []
    total = len(groups)
    for index, (group_rows, _state) in enumerate(groups, start=1):
        result = await letterbox_service.remove_episode_group(
            db,
            [episode for episode, *_rest in group_rows],
            source="job",
        )
        results.append(
            {
                "episode_ids": [episode.id for episode, *_rest in group_rows],
                "removed": result.removed,
                "path": result.path,
            }
        )
        if progress is not None:
            await progress(index, total)
    return {
        "removed_groups": len(results),
        "removed_episodes": sum(len(item["episode_ids"]) for item in results),
        "items": results,
    }
